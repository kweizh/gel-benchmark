#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
QUERIES_DIR="$SCRIPT_DIR/queries"

# Ensure gel server is running
gel-ctl start >/dev/null 2>&1 || true

report="${1:-}"
shift 2>/dev/null || true

case "$report" in
    balance)
        result=$(gel query -F json -f "$QUERIES_DIR/balance.edgeql")
        echo "$result" | jq '
            [.[] | {
                code: .code,
                shelf_units: .shelf_units,
                ledger_units: .ledger_units,
                counted_skus: .counted_codes,
                ledger_skus: .ledger_codes,
                both: ((.counted_codes - (.counted_codes - .ledger_codes)) | sort),
                shelf_only: ((.counted_codes - .ledger_codes) | sort),
                ledger_only: ((.ledger_codes - .counted_codes) | sort),
                all_skus: ((.counted_codes + .ledger_codes | unique | sort)),
                unreconciled_skus: .unreconciled_codes,
                is_balanced: .is_balanced
            }]
            | { report: "balance", warehouses: . }
        '
        ;;

    sku)
        sku_code="${1:-}"
        strict=false
        if [ "${2:-}" = "--strict" ]; then
            strict=true
        fi
        if [ -z "$sku_code" ]; then
            echo "error: missing sku code" >&2
            exit 2
        fi

        tmpfile=$(mktemp)
        sed "s/\$code/'${sku_code}'/g" "$QUERIES_DIR/sku.edgeql" > "$tmpfile"
        result=$(gel query -F json -f "$tmpfile")
        rm -f "$tmpfile"

        exists=$(echo "$result" | jq -r '.[0].ex')
        sole_wh=$(echo "$result" | jq -r '.[0].sole_warehouse_code')
        shelf_wh=$(echo "$result" | jq -r '.[0].shelf_warehouses')
        shelf_units=$(echo "$result" | jq -r '.[0].shelf_units')
        ledger_units=$(echo "$result" | jq -r '.[0].ledger_units')

        if [ "$exists" = "false" ]; then
            if [ "$strict" = true ]; then
                echo "error: sku not found: $sku_code" >&2
                exit 3
            else
                jq -n --arg code "$sku_code" '{
                    report: "sku",
                    code: $code,
                    exists: false,
                    sole_warehouse: null,
                    shelf_warehouses: [],
                    shelf_units: 0,
                    ledger_units: 0
                }'
            fi
        else
            jq -n --arg code "$sku_code" \
                --argjson exists true \
                --arg sole_wh "$sole_wh" \
                --argjson shelf_wh "$shelf_wh" \
                --argjson shelf_units "$shelf_units" \
                --argjson ledger_units "$ledger_units" '{
                report: "sku",
                code: $code,
                exists: $exists,
                sole_warehouse: (if $sole_wh == "null" or $sole_wh == "" then null else $sole_wh end),
                shelf_warehouses: $shelf_wh,
                shelf_units: $shelf_units,
                ledger_units: $ledger_units
            }'
        fi
        ;;

    duplicates)
        assert=false
        if [ "${1:-}" = "--assert" ]; then
            assert=true
        fi

        result=$(gel query -F json -f "$QUERIES_DIR/duplicates.edgeql")
        clean=$(echo "$result" | jq -r '.[0].clean')
        pairs=$(echo "$result" | jq '.[0].pairs')

        if [ "$assert" = true ] && [ "$clean" = "false" ]; then
            echo "error: duplicate shelf counts" >&2
            exit 4
        fi

        jq -n --argjson clean "$clean" --argjson pairs "$pairs" '{
            report: "duplicates",
            clean: $clean,
            pairs: $pairs
        }'
        ;;

    matrix)
        sku_filter=""
        if [ "${1:-}" = "--sku" ]; then
            sku_filter="${2:-}"
        fi

        tmpfile=$(mktemp)
        sed "s/\$filter_code/'${sku_filter}'/g" "$QUERIES_DIR/matrix.edgeql" > "$tmpfile"
        result=$(gel query -F json -f "$tmpfile")
        rm -f "$tmpfile"

        echo "$result" | jq '.[0] | {
            report: "matrix",
            cells: .cells,
            total_delta: .total_delta
        }'
        ;;

    *)
        echo "error: unknown report: $report" >&2
        exit 2
        ;;
esac
