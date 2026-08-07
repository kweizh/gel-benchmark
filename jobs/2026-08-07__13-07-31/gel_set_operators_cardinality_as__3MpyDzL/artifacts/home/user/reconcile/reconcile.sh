#!/usr/bin/env bash
set -eo pipefail

# Find the directory where this script is located
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Check if report name is provided
if [ -z "$1" ]; then
    echo "error: unknown report: " >&2
    exit 2
fi

REPORT="$1"

case "$REPORT" in
    balance)
        # Run balance query
        gel query --file "$DIR/queries/balance.edgeql" 2>/dev/null
        ;;
        
    sku)
        # Parse arguments for sku
        STRICT=false
        CODE=""
        for arg in "${@:2}"; do
            if [ "$arg" = "--strict" ]; then
                STRICT=true
            else
                CODE="$arg"
            fi
        done
        
        # Check if code is missing
        if [ -z "$CODE" ]; then
            echo "error: missing sku code" >&2
            exit 2
        fi
        
        # Run the sku query with global variable set
        JSON_OUTPUT=$( (echo "set global current_sku_code := '${CODE}';"; cat "$DIR/queries/sku.edgeql") | gel query --file - 2>/dev/null )
        
        # Check if SKU exists
        EXISTS=$(echo "$JSON_OUTPUT" | jq '.exists')
        
        if [ "$EXISTS" = "false" ] && [ "$STRICT" = "true" ]; then
            echo "error: sku not found: ${CODE}" >&2
            exit 3
        else
            echo "$JSON_OUTPUT"
        fi
        ;;
        
    duplicates)
        # Parse arguments for duplicates
        ASSERT=false
        for arg in "${@:2}"; do
            if [ "$arg" = "--assert" ]; then
                ASSERT=true
            fi
        done
        
        # Run duplicates query
        JSON_OUTPUT=$(gel query --file "$DIR/queries/duplicates.edgeql" 2>/dev/null)
        
        # Check if clean
        CLEAN=$(echo "$JSON_OUTPUT" | jq '.clean')
        
        if [ "$CLEAN" = "false" ] && [ "$ASSERT" = "true" ]; then
            echo "error: duplicate shelf counts" >&2
            exit 4
        else
            echo "$JSON_OUTPUT"
        fi
        ;;
        
    matrix)
        # Parse arguments for matrix
        FILTER_BY_SKU=false
        CODE=""
        args=("${@:2}")
        i=0
        while [ $i -lt ${#args[@]} ]; do
            if [ "${args[$i]}" = "--sku" ]; then
                FILTER_BY_SKU=true
                CODE="${args[$((i+1))]}"
                i=$((i+2))
            else
                i=$((i+1))
            fi
        done
        
        # Run matrix query
        JSON_OUTPUT=$( (echo "set global filter_by_sku := ${FILTER_BY_SKU};"; echo "set global current_sku_code := '${CODE}';"; cat "$DIR/queries/matrix.edgeql") | gel query --file - 2>/dev/null )
        
        echo "$JSON_OUTPUT"
        ;;
        
    *)
        echo "error: unknown report: ${REPORT}" >&2
        exit 2
        ;;
esac
