#!/bin/bash

# Generate RUN_ID
RUN_ID="zr$(tr -dc 'a-z0-9' < /dev/urandom | head -c 8)"
mkdir -p /logs/artifacts
echo "$RUN_ID" > /logs/artifacts/run-id

# Bring the local Gel instance up before handing control to the CMD.
/usr/local/bin/start-gel || echo "warning: the local Gel server did not start" >&2

exec "$@"
