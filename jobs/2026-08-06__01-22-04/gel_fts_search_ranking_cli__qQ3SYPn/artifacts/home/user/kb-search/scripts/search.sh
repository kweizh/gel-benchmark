#!/usr/bin/env bash
# Relevance-ranked, paginated full-text search over Article.
#
# Usage: search.sh QUERY [LIMIT] [OFFSET]
set -euo pipefail

usage() {
    echo "usage: search.sh QUERY [LIMIT] [OFFSET]" >&2
    exit 2
}

# --- argument validation -----------------------------------------------

if [ "$#" -lt 1 ] || [ "$#" -gt 3 ]; then
    usage
fi

QUERY="$1"
LIMIT="${2:-5}"
OFFSET="${3:-0}"

if [ -z "$QUERY" ]; then
    usage
fi

is_nonneg_int() {
    [[ "$1" =~ ^[0-9]+$ ]]
}

if ! is_nonneg_int "$LIMIT"; then
    usage
fi
if ! is_nonneg_int "$OFFSET"; then
    usage
fi
if [ "$LIMIT" -lt 1 ]; then
    usage
fi

# Normalize (strip any leading zeros, forcing base-10 interpretation so that
# e.g. "08" isn't mistaken for an invalid octal literal by the shell).
LIMIT=$((10#$LIMIT))
OFFSET=$((10#$OFFSET))

# Reject the (guaranteed-absent-in-QUERY) dollar-quote delimiter as a
# defensive measure; QUERY is documented to never contain it.
if [[ "$QUERY" == *'$$'* ]]; then
    usage
fi

# --- run the ranked search ----------------------------------------------

RAW="$(gel query -F json -f - <<EDGEQL
with
    q := <str>\$\$${QUERY}\$\$,
    scored := (
        select fts::search(Article, q, language := "eng", weights := [1.0, 0.5, 0.1, 0.0])
    ),
    matched := (
        select scored
        filter .object.published and .score > 0
    ),
    ranked := (
        for m in matched union (
            select (
                slug := m.object.slug,
                title := m.object.title,
                section := m.object.section,
                score := <float64>round(<decimal>m.score, 4),
            )
        )
    )
select {
    total := count(matched),
    page := array_agg((
        select ranked
        order by .score desc then .slug asc
        offset <int64>${OFFSET}
        limit <int64>${LIMIT}
    ))
};
EDGEQL
)"

jq -c --arg query "$QUERY" --argjson limit "$LIMIT" --argjson offset "$OFFSET" '
    .[0] as $r
    | {
        query: $query,
        limit: $limit,
        offset: $offset,
        total: $r.total,
        results: ($r.page | to_entries | map({
            rank: (.key + $offset + 1),
            slug: .value.slug,
            title: .value.title,
            section: .value.section,
            score: .value.score
        }))
    }
' <<<"$RAW"
