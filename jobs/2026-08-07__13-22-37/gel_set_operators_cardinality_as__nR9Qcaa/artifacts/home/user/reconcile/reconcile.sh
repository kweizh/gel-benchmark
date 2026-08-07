#!/usr/bin/env bash
#
# reconcile.sh — set-operator reconciliation CLI for the Gel audit database.
#
# Usage:
#   bash /home/user/reconcile/reconcile.sh <report> [flags]
#
# On success it writes exactly one JSON document to stdout and exits 0.
# All data is read fresh from the live Gel instance on every invocation;
# nothing is cached or hard-coded. Every EdgeQL statement lives in a
# .edgeql file under queries/ and is executed through the `gel` CLI.

set -uo pipefail

# Resolve our own location so the script behaves identically from any CWD.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QUERIES="$SCRIPT_DIR/queries"

# Make sure the local Gel server is accepting connections. The command is
# idempotent and only returns once the server is ready. Suppress all of its
# output so that stdout stays clean (we must emit exactly one JSON document).
gel-ctl start >/dev/null 2>&1 || true

# Run an EdgeQL file and emit its raw JSON result set on stdout.
gel_json() {
    gel query -F json -f "$1"
}

# Emit an error message on stderr and exit with the given status.
die() {
    echo "error: $1" >&2
    exit "$2"
}

report="${1:-}"
shift || true

case "$report" in
    balance)
        if ! out=$(gel_json "$QUERIES/balance.edgeql" \
                   | jq -c '.[0]
                            | .warehouses[] |= ( .counted_skus      |= map(.code)
                                               | .ledger_skus       |= map(.code)
                                               | .unreconciled_skus |= map(.code) )'); then
            die "query failed" 1
        fi
        printf '%s\n' "$out"
        ;;

    sku)
        code="${1:-}"
        if [ -z "$code" ]; then
            die "missing sku code" 2
        fi
        shift || true
        strict=no
        while [ $# -gt 0 ]; do
            arg="$1"; shift
            case "$arg" in
                --strict) strict=yes ;;
            esac
        done

        if ! raw=$(gel_json "$QUERIES/sku.edgeql"); then
            die "query failed" 1
        fi

        if printf '%s\n' "$raw" | jq -e --arg c "$code" 'any(.[0].skus[]; .code == $c)' >/dev/null 2>&1; then
            printf '%s\n' "$raw" \
                | jq -c --arg c "$code" \
                    '.[0].skus[]
                     | select(.code == $c)
                     | { report:           "sku",
                         code:              $c,
                         exists:            true,
                         sole_warehouse:    .sole_warehouse.code,
                         shelf_warehouses:  .shelf_warehouses,
                         shelf_units:       .shelf_units,
                         ledger_units:      .ledger_units }'
        else
            if [ "$strict" = "yes" ]; then
                die "sku not found: $code" 3
            fi
            jq -nc --arg c "$code" \
                '{ report:          "sku",
                   code:             $c,
                   exists:           false,
                   sole_warehouse:   null,
                   shelf_warehouses: [],
                   shelf_units:      0,
                   ledger_units:     0 }'
        fi
        ;;

    duplicates)
        assert=no
        while [ $# -gt 0 ]; do
            arg="$1"; shift
            case "$arg" in
                --assert) assert=yes ;;
            esac
        done

        if ! raw=$(gel_json "$QUERIES/duplicates.edgeql"); then
            die "query failed" 1
        fi

        if [ "$assert" = "yes" ]; then
            if printf '%s\n' "$raw" | jq -e '.[0] | (.pairs | length) > 0' >/dev/null 2>&1; then
                die "duplicate shelf counts" 4
            fi
        fi

        printf '%s\n' "$raw" | jq -c '.[0] | {report, clean: (.pairs | length == 0), pairs}'
        ;;

    matrix)
        skufilter=""
        sku_seen=no
        while [ $# -gt 0 ]; do
            arg="$1"; shift
            case "$arg" in
                --sku)
                    sku_seen=yes
                    skufilter="${1:-}"
                    shift || true
                    ;;
            esac
        done

        if ! raw=$(gel_json "$QUERIES/matrix.edgeql"); then
            die "query failed" 1
        fi

        if [ "$sku_seen" = "yes" ]; then
            printf '%s\n' "$raw" \
                | jq -c --arg c "$skufilter" \
                    '.[0]
                     | .cells |= map(select(.sku == $c))
                     | .total_delta = (([.cells[].delta] | add) // 0)'
        else
            printf '%s\n' "$raw" \
                | jq -c '.[0] | .total_delta = (([.cells[].delta] | add) // 0)'
        fi
        ;;

    *)
        die "unknown report: $report" 2
        ;;
esac
