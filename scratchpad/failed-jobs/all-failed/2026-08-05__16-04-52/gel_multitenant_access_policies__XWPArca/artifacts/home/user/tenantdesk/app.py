#!/usr/bin/env python3
"""tenantdesk support-desk CLI.

Tenant isolation is enforced by database-level access policies on the Ticket
type.  Every command sets `global current_actor_email` on its connection so
the database can scope visibility and writes to the caller's own tenant.
"""

import argparse
import json
import sys

import gel

TICKET_SHAPE = """{ref, subject, status_str := <str>.status, tenant_slug := .tenant.slug}"""

VALID_STATUSES = {"open", "pending", "closed"}


def die(code: int, msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(code)


def as_dict(row) -> dict:
    return {
        "ref": row.ref,
        "subject": row.subject,
        "status": row.status_str,
        "tenant": row.tenant_slug,
    }


def query_one(client: gel.Client, query: str, **kwargs):
    """Like query_single but tolerant of access-policy ambiguity."""
    rows = client.query(query, **kwargs)
    if not rows:
        return None
    return rows[0]


def get_actor(client: gel.Client, email: str) -> dict | None:
    """Look up the actor and return a dict with tenant_slug and role, or None."""
    result = query_one(
        client,
        """
        select Actor {
            tenant_slug := .tenant.slug,
            role_str := <str>.role,
        }
        filter .email = <str>$email
        """,
        email=email,
    )
    if result is None:
        return None
    return {"tenant_slug": result.tenant_slug, "role": result.role_str}


def resolve_actor(client: gel.Client, email: str) -> dict:
    """Look up the actor; die(4) if not found."""
    actor = get_actor(client, email)
    if actor is None:
        die(4, "error: unknown-actor")
    return actor


def cmd_whoami(client: gel.Client, email: str) -> None:
    actor = resolve_actor(client, email)
    count = client.query_single("select count(Ticket)")
    print(json.dumps({
        "actor": email,
        "tenant": actor["tenant_slug"],
        "role": actor["role"],
        "visible_tickets": count,
    }))


def cmd_list_tickets(client: gel.Client, email: str) -> None:
    resolve_actor(client, email)
    rows = client.query(f"select Ticket {TICKET_SHAPE} order by .ref")
    print(json.dumps([as_dict(r) for r in rows]))


def cmd_create_ticket(
    client: gel.Client, email: str, tenant_slug: str, ref: str, subject: str
) -> None:
    actor = resolve_actor(client, email)

    if tenant_slug != actor["tenant_slug"]:
        die(3, "error: denied")

    # Check for conflict
    existing = client.query(
        "select Ticket {ref} filter .ref = <str>$ref",
        ref=ref,
    )
    if existing:
        die(5, "error: conflict")

    try:
        row = query_one(
            client,
            f"""
            select (
                insert Ticket {{
                    ref := <str>$ref,
                    subject := <str>$subject,
                    tenant := (select Tenant filter .slug = <str>$tenant_slug),
                }}
            ) {TICKET_SHAPE}
            """,
            ref=ref,
            subject=subject,
            tenant_slug=tenant_slug,
        )
    except gel.errors.ConstraintViolationError:
        die(5, "error: conflict")
    except gel.errors.AccessPolicyError:
        die(3, "error: denied")

    print(json.dumps(as_dict(row)))


def cmd_update_ticket(
    client: gel.Client,
    email: str,
    ref: str,
    subject: str | None,
    status: str | None,
    tenant_slug: str | None,
) -> None:
    actor = resolve_actor(client, email)

    # If tenant is being explicitly passed, it must match actor's tenant
    if tenant_slug is not None and tenant_slug != actor["tenant_slug"]:
        die(3, "error: denied")

    # Find the ticket (access policies ensure it belongs to actor's tenant)
    ticket = query_one(
        client,
        f"select Ticket {TICKET_SHAPE} filter .ref = <str>$ref",
        ref=ref,
    )
    if ticket is None:
        die(3, "error: denied")

    # Build the update shape
    set_parts = []
    params: dict = {"ref": ref}
    if subject is not None:
        set_parts.append("subject := <str>$subject")
        params["subject"] = subject
    if status is not None:
        set_parts.append("status := <TicketStatus>$status")
        params["status"] = status
    if tenant_slug is not None:
        set_parts.append("tenant := (select Tenant filter .slug = <str>$tenant_slug)")
        params["tenant_slug"] = tenant_slug

    set_clause = ", ".join(set_parts)

    try:
        row = query_one(
            client,
            f"""
            select (
                update Ticket
                filter .ref = <str>$ref
                set {{ {set_clause} }}
            ) {TICKET_SHAPE}
            """,
            **params,
        )
    except gel.errors.AccessPolicyError:
        die(3, "error: denied")

    if row is None:
        die(3, "error: denied")

    print(json.dumps(as_dict(row)))


def cmd_delete_ticket(client: gel.Client, email: str, ref: str) -> None:
    resolve_actor(client, email)

    ticket = query_one(
        client,
        "select Ticket {ref} filter .ref = <str>$ref",
        ref=ref,
    )
    if ticket is None:
        die(3, "error: denied")

    try:
        query_one(
            client,
            "select (delete Ticket filter .ref = <str>$ref) {ref}",
            ref=ref,
        )
    except gel.errors.AccessPolicyError:
        die(3, "error: denied")

    print(json.dumps({"ref": ref, "deleted": True}))


def cmd_load_seed(client: gel.Client, filepath: str) -> None:
    """Load seed data. Uses per-tenant admin identities for ticket operations."""
    with open(filepath, "r") as f:
        data = json.load(f)

    tenants_count = 0
    actors_count = 0
    tickets_count = 0

    # Upsert tenants
    for t in data.get("tenants", []):
        existing = client.query(
            "select Tenant filter .slug = <str>$slug",
            slug=t["slug"],
        )
        if not existing:
            client.query(
                "insert Tenant { slug := <str>$slug, name := <str>$name }",
                slug=t["slug"],
                name=t["name"],
            )
        else:
            client.query(
                "update Tenant filter .slug = <str>$slug set { name := <str>$name }",
                slug=t["slug"],
                name=t["name"],
            )
        tenants_count += 1

    # Upsert actors
    for a in data.get("actors", []):
        existing = client.query(
            "select Actor filter .email = <str>$email",
            email=a["email"],
        )
        if not existing:
            client.query(
                """
                insert Actor {
                    email := <str>$email,
                    tenant := (select Tenant filter .slug = <str>$tenant),
                    role := <ActorRole>$role,
                }
                """,
                email=a["email"],
                tenant=a["tenant"],
                role=a["role"],
            )
        else:
            client.query(
                """
                update Actor
                filter .email = <str>$email
                set {
                    tenant := (select Tenant filter .slug = <str>$tenant),
                    role := <ActorRole>$role,
                }
                """,
                email=a["email"],
                tenant=a["tenant"],
                role=a["role"],
            )
        actors_count += 1

    # Build a map of tenant -> admin email for ticket operations
    tenant_admins: dict[str, str] = {}
    for a in data.get("actors", []):
        if a["role"] == "admin" and a["tenant"] not in tenant_admins:
            tenant_admins[a["tenant"]] = a["email"]

    # Upsert tickets — each must be done under the tenant's admin identity
    for tk in data.get("tickets", []):
        tenant_slug = tk["tenant"]
        admin_email = tenant_admins.get(tenant_slug)
        if admin_email is None:
            continue

        admin_client = client.with_globals(
            {"current_actor_email": admin_email}
        )
        try:
            existing = admin_client.query(
                "select Ticket filter .ref = <str>$ref",
                ref=tk["ref"],
            )
            if not existing:
                admin_client.query(
                    """
                    insert Ticket {
                        ref := <str>$ref,
                        subject := <str>$subject,
                        status := <TicketStatus>$status,
                        tenant := (select Tenant filter .slug = <str>$tenant),
                    }
                    """,
                    ref=tk["ref"],
                    subject=tk["subject"],
                    status=tk["status"],
                    tenant=tenant_slug,
                )
            else:
                admin_client.query(
                    """
                    update Ticket
                    filter .ref = <str>$ref
                    set {
                        subject := <str>$subject,
                        status := <TicketStatus>$status,
                    }
                    """,
                    ref=tk["ref"],
                    subject=tk["subject"],
                    status=tk["status"],
                )
        finally:
            admin_client.close()
        tickets_count += 1

    print(json.dumps({
        "tenants": tenants_count,
        "actors": actors_count,
        "tickets": tickets_count,
    }))


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="app.py")
    subcommands = parser.add_subparsers(dest="command")

    whoami = subcommands.add_parser("whoami")
    whoami.add_argument("--actor", required=True)

    listing = subcommands.add_parser("list-tickets")
    listing.add_argument("--actor", required=True)

    creating = subcommands.add_parser("create-ticket")
    creating.add_argument("--actor", required=True)
    creating.add_argument("--tenant", required=True)
    creating.add_argument("--ref", required=True)
    creating.add_argument("--subject", required=True)

    updating = subcommands.add_parser("update-ticket")
    updating.add_argument("--actor", required=True)
    updating.add_argument("--ref", required=True)
    updating.add_argument("--subject", default=None)
    updating.add_argument("--status", default=None)
    updating.add_argument("--tenant", default=None)

    deleting = subcommands.add_parser("delete-ticket")
    deleting.add_argument("--actor", required=True)
    deleting.add_argument("--ref", required=True)

    loading = subcommands.add_parser("load-seed")
    loading.add_argument("--file", required=True)

    return parser.parse_args(argv)


def main(argv):
    args = parse_args(argv)

    if hasattr(args, "status") and args.status is not None:
        if args.status not in VALID_STATUSES:
            die(2, "error: malformed invocation")

    if args.command == "update-ticket":
        if args.subject is None and args.status is None and args.tenant is None:
            die(2, "error: malformed invocation")

    client = gel.create_client()

    try:
        if args.command == "load-seed":
            cmd_load_seed(client, args.file)
        else:
            client_with_global = client.with_globals(
                {"current_actor_email": args.actor}
            )
            try:
                if args.command == "whoami":
                    cmd_whoami(client_with_global, args.actor)
                elif args.command == "list-tickets":
                    cmd_list_tickets(client_with_global, args.actor)
                elif args.command == "create-ticket":
                    cmd_create_ticket(
                        client_with_global,
                        args.actor,
                        args.tenant,
                        args.ref,
                        args.subject,
                    )
                elif args.command == "update-ticket":
                    cmd_update_ticket(
                        client_with_global,
                        args.actor,
                        args.ref,
                        args.subject,
                        args.status,
                        args.tenant,
                    )
                elif args.command == "delete-ticket":
                    cmd_delete_ticket(client_with_global, args.actor, args.ref)
            finally:
                client_with_global.close()
    finally:
        client.close()

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except gel.errors.AccessPolicyError:
        die(3, "error: denied")
    except Exception:
        die(3, "error: denied")
