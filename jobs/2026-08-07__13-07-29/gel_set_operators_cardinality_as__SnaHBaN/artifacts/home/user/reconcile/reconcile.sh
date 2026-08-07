#!/usr/bin/env bash
#
# Reconciliation CLI for the Gel-backed stock-audit database.
#
# Usage: bash reconcile.sh <report> [flags]
#
set -u

# Resolve the directory this script lives in, regardless of the caller's cwd.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
QUERIES_DIR="${SCRIPT_DIR}/queries"

# Make sure the local Gel server is up (idempotent).
gel-ctl start >/dev/null 2>&1 || true

# Run an EdgeQL file through the gel CLI and print its single JSON result
# object on stdout. The gel `query -F json` output wraps the (single) result
# row in a JSON array; we unwrap it with jq. Any gel warnings are discarded
# (they go to stderr from gel itself and are not surfaced to our stdout).
run_query() {
    local file="$1"
    gel query -f "$file" -F json 2>/dev/null | jq -c '.[0]'
}

# Escape a string for safe embedding inside a single-quoted EdgeQL string
# literal (EdgeQL uses backslash escaping, like `\'`).
edgeql_escape() {
    local s="$1"
    s="${s//\\/\\\\}"
    s="${s//\'/\\\'}"
    printf '%s' "$s"
}

if [[ $# -lt 1 ]]; then
    echo "error: unknown report: " >&2
    exit 2
fi

REPORT="$1"
shift

# Generic flag/positional-argument parsing for the remaining arguments.
STRICT=0
ASSERT=0
HAS_SKU_FLAG=0
SKU_FLAG_VALUE=""
POSITIONAL=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --strict)
            STRICT=1
            shift
            ;;
        --assert)
            ASSERT=1
            shift
            ;;
        --sku)
            HAS_SKU_FLAG=1
            SKU_FLAG_VALUE="${2:-}"
            shift 2
            ;;
        *)
            POSITIONAL+=("$1")
            shift
            ;;
    esac
done

case "$REPORT" in
    balance)
        RAW="$(run_query "${QUERIES_DIR}/balance.edgeql")"
        echo "$RAW" | jq -c '
          {
            report: .report,
            warehouses: [
              .warehouses[] | {
                code: .code,
                shelf_units: .shelf_units,
                ledger_units: .ledger_units,
                counted_skus: .counted_codes,
                ledger_skus: .ledger_codes,
                both: .both_codes,
                shelf_only: .shelf_only_codes,
                ledger_only: .ledger_only_codes,
                all_skus: .all_codes,
                unreconciled_skus: .unreconciled_codes,
                is_balanced: .is_balanced
              }
            ]
          }
        '
        exit 0
        ;;

    sku)
        if [[ "${#POSITIONAL[@]}" -lt 1 ]]; then
            echo "error: missing sku code" >&2
            exit 2
        fi
        CODE="${POSITIONAL[0]}"
        ESCAPED="$(edgeql_escape "$CODE")"

        TMP_QUERY="$(mktemp)"
        trap 'rm -f "$TMP_QUERY"' EXIT

        # Substitute the (escaped) code into the query template using plain
        # bash string replacement (avoids sed metacharacter pitfalls).
        TEMPLATE="$(cat "${QUERIES_DIR}/sku.edgeql")"
        printf '%s' "${TEMPLATE//__CODE__/$ESCAPED}" > "$TMP_QUERY"

        RAW="$(run_query "$TMP_QUERY")"
        EXISTS="$(echo "$RAW" | jq -r '.exists')"

        if [[ "$EXISTS" == "false" && "$STRICT" -eq 1 ]]; then
            echo "error: sku not found: ${CODE}" >&2
            exit 3
        fi

        echo "$RAW" | jq -c '.'
        exit 0
        ;;

    duplicates)
        RAW="$(run_query "${QUERIES_DIR}/duplicates.edgeql")"
        CLEAN="$(echo "$RAW" | jq -r '(.pairs | length) == 0')"

        if [[ "$CLEAN" == "false" && "$ASSERT" -eq 1 ]]; then
            echo "error: duplicate shelf counts" >&2
            exit 4
        fi

        echo "$RAW" | jq -c --argjson clean "$CLEAN" '{report: .report, clean: $clean, pairs: .pairs}'
        exit 0
        ;;

    matrix)
        SKU_FILTER_EXPR="true"
        if [[ "$HAS_SKU_FLAG" -eq 1 ]]; then
            ESCAPED="$(edgeql_escape "$SKU_FLAG_VALUE")"
            SKU_FILTER_EXPR=".code = '${ESCAPED}'"
        fi

        TMP_QUERY="$(mktemp)"
        trap 'rm -f "$TMP_QUERY"' EXIT

        # Substitute the filter expression into the query template using
        # plain bash string replacement (avoids sed metacharacter pitfalls).
        TEMPLATE="$(cat "${QUERIES_DIR}/matrix.edgeql")"
        printf '%s' "${TEMPLATE//__SKU_FILTER__/$SKU_FILTER_EXPR}" > "$TMP_QUERY"

        RAW="$(run_query "$TMP_QUERY")"

        echo "$RAW" | jq -c '.'
        exit 0
        ;;

    *)
        echo "error: unknown report: ${REPORT}" >&2
        exit 2
        ;;
esac
