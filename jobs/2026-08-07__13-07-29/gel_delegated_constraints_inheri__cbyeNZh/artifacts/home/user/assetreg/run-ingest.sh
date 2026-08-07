#!/usr/bin/env bash
# Runs the asset registry batch ingest CLI.
#
# Usage:
#   bash run-ingest.sh --input <manifest.json> --report <report.json>
#
# Both flags are mandatory and may appear in either order.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec "${SCRIPT_DIR}/node_modules/.bin/tsx" "${SCRIPT_DIR}/src/ingest.ts" "$@"
