#!/usr/bin/env python3
"""tenantdesk support-desk CLI.

Tenant isolation is enforced by the database itself: every object type that
holds tenant data (`Ticket`) carries access policies keyed off the
`current_actor_email` session-level global (see dbschema/default.gel). This
CLI is intentionally a thin wrapper around that: for every command it
resolves the caller's own identity, sets `current_actor_email` on its
connection for the duration of the call, and lets the database decide what
is visible and what is allowed. Nothing here re-implements or second-guesses
those checks -- a connection that never runs this file, but sets the same
global itself, gets exactly the same guarantees.
"""

import argparse
import json
import random
import sys
import time

import gel
import gel.errors as gel_errors

TICKET_SHAPE = """
    {
        ref,
        subject,
        status_str := <str>.status,
        tenant_slug := .tenant.slug,
    }
"""

VALID_STATUSES = ("open", "pending", "closed")

# How many times a single write may be retried after a *transient*
# database-level conflict (e.g. two `create-ticket` processes racing for the
# same tenant/ref) before we give up. This is on top of the retries the gel
# client already performs internally; belt and suspenders.
MAX_CONFLICT_RETRIES = 100


class CliError(Exception):
    """A well-defined CLI failure: exactly one stderr line plus an exit code."""

    def __init__(self, exit_code, message):
        super().__init__(message)
        self.exit_code = exit_code
        self.message = message


def fail(exit_code, message):
    raise CliError(exit_code, message)


class ArgumentParser(argparse.ArgumentParser):
    """argparse normally prints a multi-line usage blurb and exits(2) on its
    own. We want exactly one stderr line, so every parsing failure is turned
    into a CliError instead of argparse handling it itself."""

    def error(self, message):
        raise CliError(2, "error: usage")

    def exit(self, status=0, message=None):
        # Reached for things like `-h`/`--help`; treat as malformed usage
        # too, since none of our subcommands define help output.
        raise CliError(2, "error: usage")


def build_parser():
    parser = ArgumentParser(prog="app.py", add_help=False)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def actor_parser(name):
        sp = subparsers.add_parser(name, add_help=False)
        sp.add_argument("--actor", required=True)
        return sp

    actor_parser("whoami")
    actor_parser("list-tickets")

    creating = actor_parser("create-ticket")
    creating.add_argument("--tenant", required=True)
    creating.add_argument("--ref", required=True)
    creating.add_argument("--subject", required=True)

    updating = actor_parser("update-ticket")
    updating.add_argument("--ref", required=True)
    updating.add_argument("--subject")
    updating.add_argument("--status", choices=VALID_STATUSES)
    updating.add_argument("--tenant")

    deleting = actor_parser("delete-ticket")
    deleting.add_argument("--ref", required=True)

    seeding = subparsers.add_parser("load-seed", add_help=False)
    seeding.add_argument("--file", required=True)

    return parser


def parse_args(argv):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "update-ticket":
        if args.subject is None and args.status is None and args.tenant is None:
            fail(2, "error: usage")

    return args


def run_with_conflict_retries(func):
    """Run `func` (a zero-arg callable issuing one or more queries), quietly
    retrying if it hits a transient database-level conflict. Never lets such
    a conflict reach the caller."""
    attempt = 0
    while True:
        try:
            return func()
        except gel_errors.TransactionConflictError:
            attempt += 1
            if attempt >= MAX_CONFLICT_RETRIES:
                raise
            time.sleep(min(0.01 * (2 ** attempt), 0.5) + random.random() * 0.01)


def as_ticket_dict(row):
    return {
        "ref": row.ref,
        "subject": row.subject,
        "status": row.status_str,
        "tenant": row.tenant_slug,
    }


def resolve_actor(client, email):
    """Look up an Actor by email. `Actor` carries no access policies, so
    this works identically no matter what `current_actor_email` happens to
    be set to on `client` -- Actors must stay readable on every connection.
    """
    row = client.query_single(
        """
        select assert_single((
            select Actor filter .email = <str>$email
        )) {
            role_str := <str>.role,
            tenant_slug := .tenant.slug,
        }
        """,
        email=email,
    )
    if row is None:
        fail(4, "error: unknown-actor")
    return row.tenant_slug, row.role_str


def cmd_whoami(client, args):
    tenant_slug, role = resolve_actor(client, args.actor)
    scoped = client.with_globals(current_actor_email=args.actor)
    visible = scoped.query_single("select count(Ticket)")
    return {
        "actor": args.actor,
        "tenant": tenant_slug,
        "role": role,
        "visible_tickets": visible,
    }


def cmd_list_tickets(client, args):
    resolve_actor(client, args.actor)
    scoped = client.with_globals(current_actor_email=args.actor)
    rows = scoped.query("select Ticket " + TICKET_SHAPE + " order by .ref")
    return [as_ticket_dict(row) for row in rows]


def cmd_create_ticket(client, args):
    tenant_slug, _role = resolve_actor(client, args.actor)

    # `--tenant` is caller-supplied and untrusted: it may only ever name the
    # caller's own tenant. (The database would refuse the insert regardless,
    # via the `insert_own_tenant` access policy -- this check just lets us
    # report the failure without depending on error-message matching.)
    if args.tenant != tenant_slug:
        fail(3, "error: denied")

    scoped = client.with_globals(current_actor_email=args.actor)

    def do_insert():
        return scoped.query_single(
            """
            select (
                insert Ticket {
                    ref := <str>$ref,
                    subject := <str>$subject,
                    tenant := assert_exists((
                        select Tenant filter .slug = <str>$tenant
                    )),
                }
            ) """
            + TICKET_SHAPE,
            ref=args.ref,
            subject=args.subject,
            tenant=args.tenant,
        )

    try:
        row = run_with_conflict_retries(do_insert)
    except gel_errors.ConstraintViolationError:
        fail(5, "error: conflict")
    except gel_errors.AccessPolicyError:
        fail(3, "error: denied")

    return as_ticket_dict(row)


def cmd_update_ticket(client, args):
    resolve_actor(client, args.actor)
    scoped = client.with_globals(current_actor_email=args.actor)

    current = scoped.query_single(
        "select Ticket " + TICKET_SHAPE + " filter .ref = <str>$ref",
        ref=args.ref,
    )
    if current is None:
        fail(3, "error: denied")

    # A ticket's tenant can never change; asking to "move" it anywhere but
    # where it already is must fail, no matter the caller's role.
    if args.tenant is not None and args.tenant != current.tenant_slug:
        fail(3, "error: denied")

    new_subject = args.subject if args.subject is not None else current.subject
    new_status = args.status if args.status is not None else current.status_str

    def do_update():
        return scoped.query_single(
            """
            select (
                update Ticket filter .ref = <str>$ref set {
                    subject := <str>$subject,
                    status := <TicketStatus><str>$status,
                }
            ) """
            + TICKET_SHAPE,
            ref=args.ref,
            subject=new_subject,
            status=new_status,
        )

    try:
        row = run_with_conflict_retries(do_update)
    except gel_errors.AccessPolicyError:
        fail(3, "error: denied")

    if row is None:
        # Not selectable for update: wrong role, or it stopped being ours
        # between the read above and now.
        fail(3, "error: denied")

    return as_ticket_dict(row)


def cmd_delete_ticket(client, args):
    resolve_actor(client, args.actor)
    scoped = client.with_globals(current_actor_email=args.actor)

    def do_delete():
        return scoped.query_single(
            "select (delete Ticket filter .ref = <str>$ref) { ref }",
            ref=args.ref,
        )

    try:
        row = run_with_conflict_retries(do_delete)
    except gel_errors.AccessPolicyError:
        fail(3, "error: denied")

    if row is None:
        fail(3, "error: denied")

    return {"ref": row.ref, "deleted": True}


def cmd_load_seed(args):
    try:
        with open(args.file, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        fail(2, "error: usage")

    tenants = data.get("tenants", [])
    actors = data.get("actors", [])
    tickets = data.get("tickets", [])

    client = gel.create_client().with_retry_options(
        gel.RetryOptions(attempts=50)
    )
    try:
        for tenant in tenants:
            client.query_single(
                """
                insert Tenant {
                    slug := <str>$slug,
                    name := <str>$name,
                }
                unless conflict on (.slug)
                else (update Tenant set { name := <str>$name })
                """,
                slug=tenant["slug"],
                name=tenant["name"],
            )

        for actor in actors:
            client.query_single(
                """
                insert Actor {
                    email := <str>$email,
                    tenant := assert_exists((
                        select Tenant filter .slug = <str>$tenant
                    )),
                    role := <ActorRole><str>$role,
                }
                unless conflict on (.email)
                else (update Actor set {
                    tenant := assert_exists((
                        select Tenant filter .slug = <str>$tenant
                    )),
                    role := <ActorRole><str>$role,
                })
                """,
                email=actor["email"],
                tenant=actor["tenant"],
                role=actor["role"],
            )

        # Tickets can only be created through an actual admin/agent Actor of
        # their own tenant (that is the whole point of the isolation rules),
        # so pick one such Actor per tenant from the seed data itself to act
        # as the identity that loads that tenant's tickets.
        tenant_seeder = {}
        for actor in actors:
            if actor["role"] not in ("admin", "agent"):
                continue
            if actor["tenant"] not in tenant_seeder or actor["role"] == "admin":
                tenant_seeder[actor["tenant"]] = actor["email"]

        tickets_by_tenant = {}
        for ticket in tickets:
            tickets_by_tenant.setdefault(ticket["tenant"], []).append(ticket)

        ticket_count = 0
        for tenant_slug, ticket_list in tickets_by_tenant.items():
            seeder_email = tenant_seeder.get(tenant_slug)
            if seeder_email is None:
                # No admin/agent Actor exists for this tenant in the seed
                # data, so there is no identity under which the database
                # would ever allow these tickets to be created.
                continue

            scoped = client.with_globals(current_actor_email=seeder_email)
            for ticket in ticket_list:

                def do_upsert(ticket=ticket, scoped=scoped, tenant_slug=tenant_slug):
                    return scoped.query_single(
                        """
                        insert Ticket {
                            ref := <str>$ref,
                            subject := <str>$subject,
                            status := <TicketStatus><str>$status,
                            tenant := assert_exists((
                                select Tenant filter .slug = <str>$tenant
                            )),
                        }
                        unless conflict on (.ref, .tenant)
                        else (update Ticket set {
                            subject := <str>$subject,
                            status := <TicketStatus><str>$status,
                        })
                        """,
                        ref=ticket["ref"],
                        subject=ticket["subject"],
                        status=ticket["status"],
                        tenant=tenant_slug,
                    )

                run_with_conflict_retries(do_upsert)

            refs = [ticket["ref"] for ticket in ticket_list]
            ticket_count += scoped.query_single(
                "select count(Ticket filter .ref in array_unpack(<array<str>>$refs))",
                refs=refs,
            )

        tenant_count = client.query_single(
            "select count(Tenant filter .slug in array_unpack(<array<str>>$slugs))",
            slugs=[tenant["slug"] for tenant in tenants],
        )
        actor_count = client.query_single(
            "select count(Actor filter .email in array_unpack(<array<str>>$emails))",
            emails=[actor["email"] for actor in actors],
        )

        return {
            "tenants": tenant_count,
            "actors": actor_count,
            "tickets": ticket_count,
        }
    finally:
        client.close()


def dispatch(client, args):
    if args.command == "whoami":
        return cmd_whoami(client, args)
    if args.command == "list-tickets":
        return cmd_list_tickets(client, args)
    if args.command == "create-ticket":
        return cmd_create_ticket(client, args)
    if args.command == "update-ticket":
        return cmd_update_ticket(client, args)
    if args.command == "delete-ticket":
        return cmd_delete_ticket(client, args)
    fail(2, "error: usage")


def main(argv):
    try:
        args = parse_args(argv)

        if args.command == "load-seed":
            result = cmd_load_seed(args)
        else:
            client = gel.create_client().with_retry_options(
                gel.RetryOptions(attempts=50)
            )
            try:
                result = dispatch(client, args)
            finally:
                client.close()
    except CliError as e:
        print(e.message, file=sys.stderr)
        return e.exit_code

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
