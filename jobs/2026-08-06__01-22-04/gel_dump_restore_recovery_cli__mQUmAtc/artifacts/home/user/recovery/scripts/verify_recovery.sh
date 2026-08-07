#!/usr/bin/env bash
# Verification CLI for the Gel disaster-recovery task.
# See /home/user/recovery/scripts/verify_recovery.py for the implementation.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"

exec python3 "${SCRIPT_DIR}/verify_recovery.py" "$@"
