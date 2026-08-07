#!/usr/bin/env bash
# Loads data/corpus.json into the `Article` type, upserting on `slug` and
# removing any Article rows whose slug is no longer present in the corpus.
# Safe to run repeatedly (idempotent).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CORPUS_FILE="$PROJECT_DIR/data/corpus.json"

if [ ! -f "$CORPUS_FILE" ]; then
    echo "load.sh: corpus file not found: $CORPUS_FILE" >&2
    exit 1
fi

CORPUS_JSON="$(cat "$CORPUS_FILE")"

gel query -F json -f - > /dev/null <<EDGEQL
with items := to_json(\$corpus\$${CORPUS_JSON}\$corpus\$)
for item in json_array_unpack(items) union (
    insert Article {
        slug := <str>item['slug'],
        title := <str>item['title'],
        summary := <str>item['summary'],
        body := <str>item['body'],
        section := <str>item['section'],
        published := <bool>item['published'],
    }
    unless conflict on .slug
    else (
        update Article set {
            title := <str>item['title'],
            summary := <str>item['summary'],
            body := <str>item['body'],
            section := <str>item['section'],
            published := <bool>item['published'],
        }
    )
);

with items := to_json(\$corpus\$${CORPUS_JSON}\$corpus\$),
    slugs := (select <str>json_array_unpack(items)['slug'])
delete Article filter .slug not in slugs;
EDGEQL
