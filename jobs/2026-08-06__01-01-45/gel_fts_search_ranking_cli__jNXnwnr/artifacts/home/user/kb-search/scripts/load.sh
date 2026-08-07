#!/bin/bash
set -euo pipefail

# Read the corpus JSON file
CORPUS_FILE="/home/user/kb-search/data/corpus.json"
if [ ! -f "$CORPUS_FILE" ]; then
  echo "Corpus file not found: $CORPUS_FILE" >&2
  exit 1
fi

JSON_CONTENT=$(cat "$CORPUS_FILE")

# Run the query using gel query -f -
gel query -f - > /dev/null <<EOF
delete Article;
with
  raw_data := to_json(\$\$$JSON_CONTENT\$\$),
for item in json_array_unpack(raw_data) union (
  insert Article {
    slug := <str>item['slug'],
    title := <str>item['title'],
    summary := <str>item['summary'],
    body := <str>item['body'],
    section := <str>item['section'],
    published := <bool>item['published']
  }
);
EOF
