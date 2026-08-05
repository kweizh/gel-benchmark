"""Load seed_data.json into the Gel database.

Re-runnable: after any number of runs the database holds exactly one Article
per slug, with field values taken from the JSON file.
"""

import asyncio
import json
import pathlib

import gel

SEED_FILE = pathlib.Path(__file__).parent / "seed_data.json"


async def seed() -> None:
    with open(SEED_FILE) as f:
        records = json.load(f)

    async with gel.create_async_client() as client:
        for record in records:
            slug = record["slug"]
            # Delete any existing article with this slug, then insert the new one.
            # This makes the script re-runnable.
            await client.query(
                "DELETE Article FILTER .slug = <str>$slug",
                slug=slug,
            )
            await client.query(
                """
                INSERT Article {
                    slug := <str>$slug,
                    title := <str>$title,
                    summary := <str>$summary,
                    body := <str>$body,
                    status := <ArticleStatus>$status,
                    tags := array_unpack(<array<str>>$tags),
                }
                """,
                slug=slug,
                title=record["title"],
                summary=record["summary"],
                body=record["body"],
                status=record["status"],
                tags=record["tags"],
            )


if __name__ == "__main__":
    asyncio.run(seed())
