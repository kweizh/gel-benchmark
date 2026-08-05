#!/usr/bin/env python3
"""tenantdesk support-desk CLI (legacy version).

Tenant scoping happens here in Python: the caller tells us which tenant it wants
to work with, we read every ticket in the database and drop the ones that do not
match.  Anything that talks to the database without going through this file sees
all of the data.
"""

import argparse
import json
import sys

import gel

TICKET_SHAPE = """
    {
        ref,
        subject,
        status_str := <str>.status,
        tenant_slug := .tenant.slug
    }
"""


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="app.py")
    subcommands = parser.add_subparsers(dest="command", required=True)

    listing = subcommands.add_parser("list-tickets")
    listing.add_argument("--actor", required=True)

    creating = subcommands.add_parser("create-ticket")
    creating.add_argument("--actor", required=True)
    creating.add_argument("--tenant", required=True)
    creating.add_argument("--ref", required=True)
    creating.add_argument("--subject", required=True)

    return parser.parse_args(argv)


def as_dict(row):
    return {
        "ref": row.ref,
        "subject": row.subject,
        "status": row.status_str,
        "tenant": row.tenant_slug,
    }


def actor_tenant(client, actor):
    return client.query_single(
        """
        select assert_single((
            select Actor filter .email = <str>$email
        )).tenant.slug
        """,
        email=actor,
    )


def list_tickets(client, actor):
    wanted = actor_tenant(client, actor)
    rows = client.query("select Ticket " + TICKET_SHAPE)
    return [as_dict(row) for row in rows if row.tenant_slug == wanted]


def create_ticket(client, actor, tenant, ref, subject):
    row = client.query_single(
        """
        select (
            insert Ticket {
                ref := <str>$ref,
                subject := <str>$subject,
                tenant := assert_exists(assert_single((
                    select Tenant filter .slug = <str>$tenant
                )))
            }
        ) """
        + TICKET_SHAPE,
        ref=ref,
        subject=subject,
        tenant=tenant,
    )
    return as_dict(row)


def main(argv):
    args = parse_args(argv)
    client = gel.create_client()
    try:
        if args.command == "list-tickets":
            print(json.dumps(list_tickets(client, args.actor)))
        else:
            print(
                json.dumps(
                    create_ticket(
                        client, args.actor, args.tenant, args.ref, args.subject
                    )
                )
            )
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
