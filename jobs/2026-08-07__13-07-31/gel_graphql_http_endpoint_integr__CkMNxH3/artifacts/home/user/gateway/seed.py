import json
import gel

def seed():
    client = gel.create_client()
    try:
        # Clear existing data to be idempotent
        client.query("DELETE Service;")
        client.query("DELETE Team;")
        
        with open("data/seed.json") as f:
            data = json.load(f)
            
        for team in data["teams"]:
            client.query(
                "INSERT Team { name := <str>$name, region := <str>$region };",
                name=team["name"],
                region=team["region"]
            )
            print(f"Inserted team: {team['name']}")
            
        for service in data["services"]:
            client.query(
                """
                INSERT Service {
                    name := <str>$name,
                    tier := <int64>$tier,
                    active := <bool>$active,
                    owner := (SELECT Team FILTER .name = <str>$team)
                };
                """,
                name=service["name"],
                tier=service["tier"],
                active=service["active"],
                team=service["team"]
            )
            print(f"Inserted service: {service['name']}")
            
    finally:
        client.close()

if __name__ == "__main__":
    seed()
