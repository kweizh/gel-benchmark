#!/bin/bash

# Generate RUN_ID
RUN_ID="zr$(tr -dc 'a-z0-9' < /dev/urandom | head -c 8)"
mkdir -p /logs/artifacts
echo "$RUN_ID" > /logs/artifacts/run-id

# Bring the local Gel server up in the background (idempotent; the pytest
# fixtures call the same script if the server is not reachable yet).
( /usr/local/bin/gel-start.sh >>/var/log/gel-server-start.log 2>&1 || \
  echo "WARNING: the local Gel server did not start; run /usr/local/bin/gel-start.sh" >&2 ) &

exec "$@"
