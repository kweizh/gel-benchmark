#!/usr/bin/env python3
"""Load the handbook corpus (seed_data.json) into the Gel database.

Re-runnable: after any number of runs the database holds exactly one
Article per slug, with field values taken from the file.
"""
import json
import os

import gel

HERE = os.path.dirname(os.path.abspath(__file__))
SEED_PATH = os.path.join(HERE, "seed_data.json")


UPSERT_QUERY = """
with
    slug := <str>$slug,
    title := <str>$title,
    summary := <str>$summary,
    body := <str>$body,
    status := <ArticleStatus>$status,
    tags := <array<str>>$tags,
select (
    insert Article {
        slug := slug,
        title := title,
        summary := summary,
        body := body,
        status := status,
        tags := array_unpack(tags),
    }
    unless conflict on .slug
    else (
        update Article
        set {
            title := title,
            summary := summary,
            body := body,
            status := status,
            tags := array_unpack(tags),
        }
    )
) { slug }
"""


def main() -> None:
    with open(SEED_PATH, "r", encoding="utf-8") as f:
        records = json.load(f)

    client = gel.create_client()
    try:
        for record in records:
            client.query(
                UPSERT_QUERY,
                slug=record["slug"],
                title=record["title"],
                summary=record["summary"],
                body=record["body"],
                status=record["status"],
                tags=list(record.get("tags", [])),
            )
        print(f"Loaded {len(records)} articles from {SEED_PATH}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
