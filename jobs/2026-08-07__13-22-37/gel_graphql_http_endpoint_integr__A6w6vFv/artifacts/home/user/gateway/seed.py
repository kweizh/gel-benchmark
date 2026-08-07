#!/usr/bin/env python3
"""Load the fixture at data/seed.json into the database.

Idempotent: clears all existing default::Service and default::Team objects
before inserting exactly the 3 teams and 9 services described in the fixture.
"""
import json
import os
import sys

import gel


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "data", "seed.json")) as f:
        seed = json.load(f)

    client = gel.create_client()

    # Delete existing managed objects (services first, then teams) so the
    # script is safe to re-run.
    client.execute("delete default::Service;")
    client.execute("delete default::Team;")

    # Insert teams.
    for team in seed["teams"]:
        client.execute(
            """
            insert default::Team {
                name := <str>$name,
                region := <str>$region,
            }
            """,
            name=team["name"],
            region=team["region"],
        )

    # Insert services, linking each to its owning team by name.
    for svc in seed["services"]:
        client.execute(
            """
            insert default::Service {
                name := <str>$name,
                tier := <int64>$tier,
                active := <bool>$active,
                owner := (
                    select default::Team filter .name = <str>$team
                ),
            }
            """,
            name=svc["name"],
            tier=svc["tier"],
            active=svc["active"],
            team=svc["team"],
        )

    # Verify counts.
    teams = client.query("select count(default::Team);")
    services = client.query("select count(default::Service);")
    print(f"Seeded {teams[0]} teams and {services[0]} services.")


if __name__ == "__main__":
    sys.exit(main())
