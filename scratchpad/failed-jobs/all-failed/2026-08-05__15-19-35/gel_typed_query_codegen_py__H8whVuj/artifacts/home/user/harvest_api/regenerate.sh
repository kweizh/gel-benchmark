#!/usr/bin/env bash
# Regenerate the typed Python query modules from the .edgeql files in
# app/queries/. Safe to run at any time (idempotent); the generated files
# are written next to the .edgeql files they come from.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

python3 -m gel.codegen --dir app/queries --target blocking
