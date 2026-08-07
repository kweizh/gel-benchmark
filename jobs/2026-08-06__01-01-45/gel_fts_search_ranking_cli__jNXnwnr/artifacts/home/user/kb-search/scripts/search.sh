#!/bin/bash
set -euo pipefail

# Check number of arguments
if [ "$#" -lt 1 ] || [ "$#" -gt 3 ]; then
  echo "usage: search.sh QUERY [LIMIT] [OFFSET]" >&2
  exit 2
fi

QUERY="$1"
# Check empty query
if [ -z "$QUERY" ]; then
  echo "usage: search.sh QUERY [LIMIT] [OFFSET]" >&2
  exit 2
fi

# Set defaults
LIMIT="5"
OFFSET="0"

if [ "$#" -ge 2 ]; then
  LIMIT="$2"
  # Validate LIMIT is a non-negative decimal integer and >= 1
  if [[ ! "$LIMIT" =~ ^[0-9]+$ ]] || [ "$LIMIT" -lt 1 ]; then
    echo "usage: search.sh QUERY [LIMIT] [OFFSET]" >&2
    exit 2
  fi
fi

if [ "$#" -eq 3 ]; then
  OFFSET="$3"
  # Validate OFFSET is a non-negative decimal integer
  if [[ ! "$OFFSET" =~ ^[0-9]+$ ]]; then
    echo "usage: search.sh QUERY [LIMIT] [OFFSET]" >&2
    exit 2
  fi
fi

# Run the search query
gel query -F json -f - <<EOF | jq '.[0]'
with
  q := \$\$$QUERY\$\$,
  limit_val := <int64>$LIMIT,
  offset_val := <int64>$OFFSET,
  all_matches := (
    with
      result := fts::search(
        (select Article filter .published),
        q,
        language := 'eng',
        weights := [1.0, 0.5, 0.1, 0.0]
      )
    select (
      score := result.score,
      slug := result.object.slug,
      title := result.object.title,
      section := result.object.section
    )
    filter .score > 0
    order by .score desc then .slug asc
  ),
  paginated_matches := (
    select all_matches
    offset offset_val
    limit limit_val
  )
select {
  query := q,
  \`limit\` := limit_val,
  \`offset\` := offset_val,
  total := count(all_matches),
  results := (
    select (
      with item := enumerate(paginated_matches)
      select (
        rank := offset_val + item.0 + 1,
        slug := item.1.slug,
        title := item.1.title,
        section := item.1.section,
        score := <float64>round(<decimal>item.1.score, 4)
      )
    )
  )
};
EOF
