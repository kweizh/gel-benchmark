"""One-off loader that populates the database from data/seed.json.

Run with: python3 scripts/seed.py
"""
import json
import os
import sys

import gel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED_PATH = os.path.join(ROOT, "data", "seed.json")


def main():
    with open(SEED_PATH) as f:
        fixture = json.load(f)

    client = gel.create_client()
    try:
        # Wipe existing data so re-running this script is idempotent.
        client.query("delete Service;")
        client.query("delete Team;")

        for team in fixture["teams"]:
            client.query(
                "insert Team { name := <str>$name, region := <str>$region };",
                name=team["name"],
                region=team["region"],
            )

        for svc in fixture["services"]:
            client.query(
                """
                insert Service {
                    name := <str>$name,
                    tier := <int64>$tier,
                    active := <bool>$active,
                    owner := (select Team filter .name = <str>$team),
                };
                """,
                name=svc["name"],
                tier=svc["tier"],
                active=svc["active"],
                team=svc["team"],
            )

        team_count = client.query_single("select count(Team);")
        service_count = client.query_single("select count(Service);")
        print(f"Loaded {team_count} teams and {service_count} services.")
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
