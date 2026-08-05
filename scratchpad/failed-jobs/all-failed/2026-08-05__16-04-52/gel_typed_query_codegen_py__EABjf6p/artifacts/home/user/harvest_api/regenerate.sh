#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 -m gel.codegen --dir app/queries --target blocking
