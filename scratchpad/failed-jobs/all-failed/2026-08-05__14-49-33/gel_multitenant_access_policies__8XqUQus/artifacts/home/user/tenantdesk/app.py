#!/usr/bin/env python3
"""tenantdesk support-ticket CLI.

Multi-tenant isolation is enforced at the database level.
"""

import argparse
import json
import sys

import gel


class CustomArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        sys.stderr.write(f"error: {message}\n")
        sys.exit(2)


def parse_args(argv):
    parser = CustomArgumentParser(prog="app.py", add_help=False)
    subparsers = parser.add_subparsers(dest="command", required=True)

    # whoami
    p_whoami = subparsers.add_parser("whoami", add_help=False)
    p_whoami.add_argument("--actor", required=True)

    # list-tickets
    p_list = subparsers.add_parser("list-tickets", add_help=False)
    p_list.add_argument("--actor", required=True)

    # create-ticket
    p_create = subparsers.add_parser("create-ticket", add_help=False)
    p_create.add_argument("--actor", required=True)
    p_create.add_argument("--tenant", required=True)
    p_create.add_argument("--ref", required=True)
    p_create.add_argument("--subject", required=True)

    # update-ticket
    p_update = subparsers.add_parser("update-ticket", add_help=False)
    p_update.add_argument("--actor", required=True)
    p_update.add_argument("--ref", required=True)
    p_update.add_argument("--subject")
    p_update.add_argument("--status")
    p_update.add_argument("--tenant")

    # delete-ticket
    p_delete = subparsers.add_parser("delete-ticket", add_help=False)
    p_delete.add_argument("--actor", required=True)
    p_delete.add_argument("--ref", required=True)

    # load-seed
    p_load = subparsers.add_parser("load-seed", add_help=False)
    p_load.add_argument("--file", required=True)

    return parser.parse_args(argv)


def main(argv):
    args = parse_args(argv)

    # Validate status for update-ticket
    if args.command == "update-ticket":
        if args.status is not None and args.status not in ("open", "pending", "closed"):
            sys.stderr.write("error: invalid status\n")
            sys.exit(2)
        if args.subject is None and args.status is None and args.tenant is None:
            sys.stderr.write("error: at least one update option must be supplied\n")
            sys.exit(2)

    client = gel.create_client()

    try:
        if args.command == "load-seed":
            try:
                with open(args.file, 'r') as f:
                    data = json.load(f)
            except Exception as e:
                sys.stderr.write(f"error: failed to read seed file: {e}\n")
                sys.exit(2)

            # Disable access policies for loading seed
            admin_client = client.with_config(apply_access_policies=False)

            for tx in admin_client.transaction():
                with tx:
                    for t in data.get("tenants", []):
                        tx.query_single(
                            """
                            insert Tenant {
                                slug := <str>$slug,
                                name := <str>$name
                            }
                            unless conflict on .slug
                            else (
                                update Tenant set {
                                    name := <str>$name
                                }
                            )
                            """,
                            slug=t["slug"],
                            name=t["name"]
                        )

                    for a in data.get("actors", []):
                        tx.query_single(
                            """
                            insert Actor {
                                email := <str>$email,
                                tenant := (select Tenant filter .slug = <str>$tenant_slug),
                                role := <ActorRole>$role
                            }
                            unless conflict on .email
                            else (
                                update Actor set {
                                    tenant := (select Tenant filter .slug = <str>$tenant_slug),
                                    role := <ActorRole>$role
                                }
                            )
                            """,
                            email=a["email"],
                            tenant_slug=a["tenant"],
                            role=a["role"]
                        )

                    for tk in data.get("tickets", []):
                        tx.query_single(
                            """
                            insert Ticket {
                                ref := <str>$ref,
                                subject := <str>$subject,
                                status := <TicketStatus>$status,
                                tenant := (select Tenant filter .slug = <str>$tenant_slug)
                            }
                            unless conflict on (.ref, .tenant)
                            else (
                                update Ticket set {
                                    subject := <str>$subject,
                                    status := <TicketStatus>$status
                                }
                            )
                            """,
                            ref=tk["ref"],
                            subject=tk["subject"],
                            status=tk["status"],
                            tenant_slug=tk["tenant"]
                        )

                    tenants_count = tx.query_single("select count(Tenant)")
                    actors_count = tx.query_single("select count(Actor)")
                    tickets_count = tx.query_single("select count(Ticket)")

            result = {
                "tenants": tenants_count,
                "actors": actors_count,
                "tickets": tickets_count
            }
            print(json.dumps(result))
            return 0

        # All other subcommands require an actor
        actor_info = client.query_single(
            """
            select Actor {
                email,
                tenant: { slug },
                role
            } filter .email = <str>$email
            """,
            email=args.actor
        )
        if not actor_info:
            sys.stderr.write("error: unknown-actor\n")
            sys.exit(4)

        authed_client = client.with_globals({'current_actor_email': args.actor})

        if args.command == "whoami":
            visible_tickets = authed_client.query_single("select count(Ticket)")
            result = {
                "actor": actor_info.email,
                "tenant": actor_info.tenant.slug,
                "role": str(actor_info.role),
                "visible_tickets": visible_tickets
            }
            print(json.dumps(result))
            return 0

        elif args.command == "list-tickets":
            tickets = authed_client.query(
                """
                select Ticket {
                    ref,
                    subject,
                    status_str := <str>.status,
                    tenant_slug := .tenant.slug
                } order by .ref asc
                """
            )
            result = [
                {
                    "ref": t.ref,
                    "subject": t.subject,
                    "status": t.status_str,
                    "tenant": t.tenant_slug
                }
                for t in tickets
            ]
            print(json.dumps(result))
            return 0

        elif args.command == "create-ticket":
            if actor_info.tenant.slug != args.tenant:
                sys.stderr.write("error: denied\n")
                sys.exit(3)

            try:
                for tx in authed_client.transaction():
                    with tx:
                        created = tx.query_single(
                            """
                            select (
                                insert Ticket {
                                    ref := <str>$ref,
                                    subject := <str>$subject,
                                    tenant := (select Tenant filter .slug = <str>$tenant)
                                }
                            ) {
                                ref,
                                subject,
                                status_str := <str>.status,
                                tenant_slug := .tenant.slug
                            }
                            """,
                            ref=args.ref,
                            subject=args.subject,
                            tenant=args.tenant
                        )
            except gel.errors.ConstraintViolationError:
                sys.stderr.write("error: conflict\n")
                sys.exit(5)
            except gel.errors.AccessPolicyError:
                sys.stderr.write("error: denied\n")
                sys.exit(3)

            result = {
                "ref": created.ref,
                "subject": created.subject,
                "status": created.status_str,
                "tenant": created.tenant_slug
            }
            print(json.dumps(result))
            return 0

        elif args.command == "update-ticket":
            # Check if ticket exists and is visible
            tickets = authed_client.query(
                "select Ticket { ref, tenant_slug := .tenant.slug } filter .ref = <str>$ref",
                ref=args.ref
            )
            ticket = tickets[0] if tickets else None
            if not ticket:
                sys.stderr.write("error: denied\n")
                sys.exit(3)

            if args.tenant is not None and args.tenant != ticket.tenant_slug:
                sys.stderr.write("error: denied\n")
                sys.exit(3)

            try:
                for tx in authed_client.transaction():
                    with tx:
                        updated_list = tx.query(
                            """
                            select (
                                update Ticket
                                filter .ref = <str>$ref
                                set {
                                    subject := <optional str>$subject ?? .subject,
                                    status := <TicketStatus>(<optional str>$status_str ?? <str>.status)
                                }
                            ) {
                                ref,
                                subject,
                                status_str := <str>.status,
                                tenant_slug := .tenant.slug
                            }
                            """,
                            ref=args.ref,
                            subject=args.subject,
                            status_str=args.status
                        )
                        updated = updated_list[0] if updated_list else None
            except gel.errors.AccessPolicyError:
                sys.stderr.write("error: denied\n")
                sys.exit(3)

            if not updated:
                sys.stderr.write("error: denied\n")
                sys.exit(3)

            result = {
                "ref": updated.ref,
                "subject": updated.subject,
                "status": updated.status_str,
                "tenant": updated.tenant_slug
            }
            print(json.dumps(result))
            return 0

        elif args.command == "delete-ticket":
            try:
                for tx in authed_client.transaction():
                    with tx:
                        deleted = tx.query(
                            "delete Ticket filter .ref = <str>$ref",
                            ref=args.ref
                        )
            except gel.errors.AccessPolicyError:
                sys.stderr.write("error: denied\n")
                sys.exit(3)

            if len(deleted) == 0:
                sys.stderr.write("error: denied\n")
                sys.exit(3)

            result = {
                "ref": args.ref,
                "deleted": True
            }
            print(json.dumps(result))
            return 0

    finally:
        client.close()

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
