FROM ubuntu:24.04

ARG POCHI_VERSION

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8

# ---------------------------------------------------------------------------
# Base tooling (no python3-pytest on purpose: the distro pytest is too old for
# pytest-json-ctrf and aborts the whole suite with an INTERNALERROR).
# ---------------------------------------------------------------------------
RUN apt-get update -y && \
    apt-get install -y --no-install-recommends \
        curl wget ca-certificates git python3 python3-pip bash ripgrep \
        gnupg procps util-linux jq && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    curl -fsSL https://getpochi.com/install.sh | bash -s ${POCHI_VERSION} && \
    ln -s /root/.pochi/bin/pochi /usr/local/bin/pochi && \
    pip3 install --break-system-packages "pytest>=8" pytest-json-ctrf && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Gel 6 server + Gel CLI from the official APT repository.
# The package ships the server as /usr/bin/gel-server-6 (no plain
# `gel-server`), and creates the unprivileged `gel` system user the server has
# to run as.
# ---------------------------------------------------------------------------
RUN mkdir -p /usr/local/share/keyrings && \
    curl --proto '=https' --tlsv1.2 -fsSL \
        -o /usr/local/share/keyrings/gel-keyring.gpg \
        https://packages.geldata.com/keys/gel-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/local/share/keyrings/gel-keyring.gpg] https://packages.geldata.com/apt noble main" \
        > /etc/apt/sources.list.d/gel.list && \
    apt-get update -y && \
    apt-get install -y --no-install-recommends gel-server-6 gel-cli && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    mkdir -p /var/lib/gel/6/data /var/lib/gel/run /opt/harbor && \
    chown -R gel:gel /var/lib/gel && \
    chmod 0755 /var/lib/gel && \
    touch /var/log/gel-server.log && \
    gel --version

RUN mkdir -p /home/user && chmod 0755 /home/user

# Client-side connection settings for the local, TLS-self-signed dev server.
ENV GEL_HOST=127.0.0.1 \
    GEL_PORT=5656 \
    GEL_USER=admin \
    GEL_BRANCH=main \
    GEL_CLIENT_TLS_SECURITY=insecure \
    GEL_RUN_VERSION_CHECK=never

# ---------------------------------------------------------------------------
# Helper: resolve the server binary defensively (its name differs across
# install methods and major versions).
# ---------------------------------------------------------------------------
RUN cat >/usr/local/bin/gel-resolve-server.sh <<'RESOLVE'
#!/usr/bin/env bash
# Print the absolute path of the local Gel server binary, or fail loudly.
for candidate in gel-server gel-server-6; do
    if command -v "$candidate" >/dev/null 2>&1; then
        command -v "$candidate"
        exit 0
    fi
done
for candidate in /usr/bin/gel-server*; do
    if [ -x "$candidate" ]; then
        echo "$candidate"
        exit 0
    fi
done
for candidate in /usr/lib/*/gel-server-*/bin/gel-server; do
    if [ -x "$candidate" ]; then
        echo "$candidate"
        exit 0
    fi
done
echo "FATAL: no Gel server binary found (tried gel-server, gel-server-6, /usr/bin/gel-server*, /usr/lib/*/gel-server-*/bin/gel-server)" >&2
exit 1
RESOLVE

# ---------------------------------------------------------------------------
# Helper: idempotently start the server. An explicit --runstate-dir is always
# passed, because the default (/run/gel) is root-owned while the server has to
# run as the unprivileged `gel` user.
# ---------------------------------------------------------------------------
RUN cat >/usr/local/bin/gel-start.sh <<'START'
#!/usr/bin/env bash
set -uo pipefail

DATA_DIR=/var/lib/gel/6/data
RUNSTATE_DIR=/var/lib/gel/run
LOG_FILE=/var/log/gel-server.log
PORT=5656

probe() {
    env GEL_HOST=127.0.0.1 GEL_PORT="$PORT" GEL_USER=admin GEL_BRANCH=main \
        GEL_CLIENT_TLS_SECURITY=insecure GEL_RUN_VERSION_CHECK=never \
        GEL_WAIT_UNTIL_AVAILABLE=1s \
        gel query -F json 'select 1' >/dev/null 2>&1
}

if probe; then
    echo "Gel server is already accepting connections on 127.0.0.1:${PORT}."
    exit 0
fi

SERVER_BIN="$(/usr/local/bin/gel-resolve-server.sh)" || exit 1

mkdir -p "$DATA_DIR" "$RUNSTATE_DIR"
RUN_AS=()
if [ "$(id -u)" = "0" ]; then
    chown -R gel:gel /var/lib/gel
    RUN_AS=(runuser -u gel --)
fi

echo "Starting ${SERVER_BIN} (log file: ${LOG_FILE})."
nohup "${RUN_AS[@]}" "$SERVER_BIN" \
    --data-dir="$DATA_DIR" \
    --runstate-dir="$RUNSTATE_DIR" \
    --bind-address=127.0.0.1 \
    --port="$PORT" \
    --security=insecure_dev_mode \
    --tls-cert-mode=generate_self_signed \
    --compiler-pool-mode=on_demand \
    --compiler-pool-size=2 \
    --log-level=warn \
    >>"$LOG_FILE" 2>&1 &

for _ in $(seq 1 150); do
    if probe; then
        echo "Gel server is ready on 127.0.0.1:${PORT}."
        exit 0
    fi
    sleep 2
done

echo "ERROR: the Gel server did not become ready in time. Last log lines:" >&2
tail -n 60 "$LOG_FILE" >&2
exit 1
START

RUN chmod 0755 /usr/local/bin/gel-resolve-server.sh \
        /usr/local/bin/gel-start.sh && \
    mkdir -p /home/user/inventory/dbschema/migrations

# ---------------------------------------------------------------------------
# Project skeleton
# ---------------------------------------------------------------------------
RUN cat >/home/user/inventory/gel.toml <<'TOML'
[instance]
server-version = "6.11"

[project]
schema-dir = "dbschema"
TOML

RUN cat >/opt/harbor/schema_v1.gel <<'SDL1'
module default {
  type Warehouse {
    required code: str {
      constraint exclusive;
    }
    required name: str;
  }

  type Part {
    required sku: str {
      constraint exclusive;
    }
    required description: str;
    required unit_price_cents: int64;
  }
}
SDL1

RUN cat >/opt/harbor/schema_v2.gel <<'SDL2'
module default {
  type Warehouse {
    required code: str {
      constraint exclusive;
    }
    required name: str;
  }

  type Part {
    required sku: str {
      constraint exclusive;
    }
    required description: str;
    required unit_price_cents: int64;
  }

  type StockLevel {
    required part: Part;
    required warehouse: Warehouse;
    required quantity: int64;
    constraint exclusive on ((.part, .warehouse));
  }
}
SDL2

# The out-of-band change a previous developer applied straight to the database.
# Kept to a SINGLE DDL statement so the database records exactly one
# DDL-generated revision.
RUN cat >/opt/harbor/out_of_band.edgeql <<'DDL'
create type default::AuditEvent {
  create required property event: std::str;
  create property note: std::str;
  create required property seq: std::int64 {
    create constraint std::exclusive;
  };
};
DDL

RUN cat >/opt/harbor/seed.edgeql <<'SEED'
insert Warehouse { code := 'W-EAST', name := 'East Distribution Center' };
insert Warehouse { code := 'W-WEST', name := 'West Distribution Center' };
insert Warehouse { code := 'W-SOUTH', name := 'South Depot' };
insert Part { sku := 'SKU-1001', description := 'Hex bolt M6', unit_price_cents := 250 };
insert Part { sku := 'SKU-1002', description := 'Steel washer', unit_price_cents := 45 };
insert Part { sku := 'SKU-1003', description := 'Copper pipe 1m', unit_price_cents := 1875 };
insert Part { sku := 'SKU-1004', description := 'Rubber gasket', unit_price_cents := 320 };
insert Part { sku := 'SKU-1005', description := 'Brass fitting', unit_price_cents := 990 };
insert StockLevel { part := assert_exists((select Part filter .sku = 'SKU-1001')), warehouse := assert_exists((select Warehouse filter .code = 'W-EAST')), quantity := 41 };
insert StockLevel { part := assert_exists((select Part filter .sku = 'SKU-1001')), warehouse := assert_exists((select Warehouse filter .code = 'W-WEST')), quantity := 8 };
insert StockLevel { part := assert_exists((select Part filter .sku = 'SKU-1002')), warehouse := assert_exists((select Warehouse filter .code = 'W-EAST')), quantity := 100 };
insert StockLevel { part := assert_exists((select Part filter .sku = 'SKU-1003')), warehouse := assert_exists((select Warehouse filter .code = 'W-SOUTH')), quantity := 7 };
insert StockLevel { part := assert_exists((select Part filter .sku = 'SKU-1004')), warehouse := assert_exists((select Warehouse filter .code = 'W-WEST')), quantity := 15 };
insert StockLevel { part := assert_exists((select Part filter .sku = 'SKU-1004')), warehouse := assert_exists((select Warehouse filter .code = 'W-SOUTH')), quantity := 2 };
insert StockLevel { part := assert_exists((select Part filter .sku = 'SKU-1004')), warehouse := assert_exists((select Warehouse filter .code = 'W-EAST')), quantity := 1 };
insert AuditEvent { event := 'initial-load', note := 'imported from the legacy spreadsheet', seq := 1 };
insert AuditEvent { event := 'manual-hotfix', seq := 2 };
insert AuditEvent { event := 'stock-recount', note := 'south depot recount', seq := 3 };
SEED

RUN cat >/opt/harbor/snapshot_query.edgeql <<'SNAPQ'
select {
  warehouses := (select Warehouse { id, code, name } order by .code),
  parts := (select Part { id, sku, description, unit_price_cents } order by .sku),
  stock_levels := (
    select StockLevel {
      id,
      quantity,
      part_sku := .part.sku,
      warehouse_code := .warehouse.code
    } order by .part.sku then .warehouse.code
  ),
  audit_events := (select AuditEvent { id, event, note, seq } order by .seq),
  db_revisions := (
    select schema::Migration { name, gb := <str>.generated_by } filter not .builtin
  )
};
SNAPQ

RUN cat >/opt/harbor/make_snapshot.py <<'SNAP'
"""Combine the live-database snapshot with the on-disk migration history."""

import glob
import json
import os
import re

MIGRATIONS_DIR = "/home/user/inventory/dbschema/migrations"
HEADER_RE = re.compile(r"CREATE\s+MIGRATION\s+([A-Za-z0-9_]+)", re.IGNORECASE)

with open("/opt/harbor/db_snapshot.json") as fh:
    raw = json.load(fh)
assert isinstance(raw, list) and len(raw) == 1, f"unexpected snapshot payload: {raw!r}"
db = raw[0]

fs_revisions = []
for path in sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql"))):
    with open(path) as fh:
        match = HEADER_RE.search(fh.read())
    assert match is not None, f"no CREATE MIGRATION header in {path}"
    fs_revisions.append(match.group(1))

ddl = [row["name"] for row in db["db_revisions"] if row["gb"] == "DDLStatement"]
assert len(db["db_revisions"]) == 3, db["db_revisions"]
assert len(ddl) == 1, ddl
assert len(fs_revisions) == 1, fs_revisions
assert fs_revisions[0] in {row["name"] for row in db["db_revisions"]}
assert len(db["warehouses"]) == 3, db["warehouses"]
assert len(db["parts"]) == 5, db["parts"]
assert len(db["stock_levels"]) == 7, db["stock_levels"]
assert len(db["audit_events"]) == 3, db["audit_events"]

snapshot = {
    "fs_revisions": fs_revisions,
    "db_revisions": db["db_revisions"],
    "ddl_revision": ddl[0],
    "warehouses": db["warehouses"],
    "parts": db["parts"],
    "stock_levels": db["stock_levels"],
    "audit_events": db["audit_events"],
}
with open("/opt/harbor/initial_state.json", "w") as fh:
    json.dump(snapshot, fh, indent=2, sort_keys=True)
print("snapshot written:", json.dumps({k: len(v) if isinstance(v, list) else v for k, v in snapshot.items()}))
SNAP

# ---------------------------------------------------------------------------
# Bake the drifted initial state:
#   revision 1 + revision 2 through the normal workflow, revision 3 applied as
#   bare DDL, application data seeded, then the newest migration FILE removed so
#   that the database history is ahead of dbschema/migrations by two revisions
#   and the SDL on disk no longer describes the live schema.
# ---------------------------------------------------------------------------
RUN cat >/opt/harbor/bake.sh <<'BAKE'
#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/user/inventory
export GEL_HOST=127.0.0.1
export GEL_PORT=5656
export GEL_USER=admin
export GEL_BRANCH=main
export GEL_CLIENT_TLS_SECURITY=insecure
export GEL_RUN_VERSION_CHECK=never

cd "$PROJECT"

count_files() {
    ls "$PROJECT"/dbschema/migrations/*.edgeql 2>/dev/null | wc -l | tr -d ' '
}

echo "=== starting the local Gel server (bootstraps the data directory) ==="
/usr/local/bin/gel-start.sh

echo "=== revision 1: Warehouse + Part ==="
cp /opt/harbor/schema_v1.gel "$PROJECT/dbschema/default.gel"
gel migration create --non-interactive
gel migrate

echo "=== revision 2: StockLevel ==="
cp /opt/harbor/schema_v2.gel "$PROJECT/dbschema/default.gel"
gel migration create --non-interactive
gel migrate
test "$(count_files)" = "2"

echo "=== revision 3: applied out of band as bare DDL ==="
gel query 'configure current branch set allow_bare_ddl := "AlwaysAllow"'
gel query "$(cat /opt/harbor/out_of_band.edgeql)"
gel query 'configure current branch set allow_bare_ddl := "NeverAllow"'
test "$(gel query -F json 'select count((select schema::Migration filter not .builtin))')" = "3"
test "$(gel query -F json 'select count((select schema::Migration filter not .builtin and .generated_by = schema::MigrationGeneratedBy.DDLStatement))')" = "1"

echo "=== seeding application data ==="
gel query --file /opt/harbor/seed.edgeql
test "$(gel query -F json 'select count(Part)')" = "5"
test "$(gel query -F json 'select count(StockLevel)')" = "7"
test "$(gel query -F json 'select count(AuditEvent)')" = "3"

echo "=== snapshotting the live database ==="
gel query -F json --file /opt/harbor/snapshot_query.edgeql > /opt/harbor/db_snapshot.json

echo "=== breaking the on-disk history: remove the newest migration file ==="
LAST_FILE="$(ls "$PROJECT"/dbschema/migrations/*.edgeql | sort | tail -n 1)"
echo "removing ${LAST_FILE}"
rm -f "$LAST_FILE"
test "$(count_files)" = "1"

python3 /opt/harbor/make_snapshot.py

echo "=== the drift must be real ==="
if gel migration status; then
    echo "FATAL: 'gel migration status' unexpectedly succeeded; the drift was not created" >&2
    exit 1
fi
if gel migration create --non-interactive; then
    echo "FATAL: 'gel migration create' unexpectedly succeeded on the drifted project" >&2
    exit 1
fi
test "$(count_files)" = "1"

echo "=== shutting the server down cleanly ==="
pkill -INT -f 'gel-server' || true
for _ in $(seq 1 90); do
    if ! pgrep -f 'gel-server' >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

echo "=== cleaning up the build-only helpers (they would give the drift away) ==="
rm -f /opt/harbor/db_snapshot.json \
      /opt/harbor/out_of_band.edgeql \
      /opt/harbor/seed.edgeql \
      /opt/harbor/snapshot_query.edgeql \
      /opt/harbor/schema_v1.gel \
      /opt/harbor/schema_v2.gel \
      /opt/harbor/make_snapshot.py

chmod -R a+rwX "$PROJECT"
chmod 0444 /opt/harbor/initial_state.json
echo "=== bake complete ==="
BAKE

RUN chmod 0755 /opt/harbor/bake.sh && \
    bash /opt/harbor/bake.sh && \
    rm -f /opt/harbor/bake.sh && \
    ls -la /opt/harbor /home/user/inventory /home/user/inventory/dbschema /home/user/inventory/dbschema/migrations

LABEL Author="generate-harbor-tasks"
LABEL Description="Gel 6 inventory project with a deliberately drifted migration history (database ahead of dbschema/migrations, bare-DDL revision, seeded data) plus a local Gel server."

CMD ["/bin/bash", "-c", "trap : TERM INT; sleep infinity & wait"]

# Container contents:
#   - always required apt packages: curl wget ca-certificates git python3 python3-pip bash ripgrep
#     (python3-pytest intentionally omitted; pytest comes from pip3 so it stays compatible
#      with pytest-json-ctrf)
#   - always required pip3 packages: pytest, pytest-json-ctrf
#   - Gel 6 server (gel-server-6) and Gel CLI (gel) from packages.geldata.com, running as the
#     unprivileged `gel` user with an explicit --runstate-dir
#   - /usr/local/bin/gel-resolve-server.sh: defensive lookup of the server binary
#   - /usr/local/bin/gel-start.sh: idempotent server start (called by the container
#     entrypoint in the background and by the pytest fixtures)
#   - /home/user/inventory: the Gel project (gel.toml, dbschema/default.gel, dbschema/migrations)
#     left in a drifted state: one migration file on disk, three revisions recorded in the
#     database (one of them produced by bare DDL), seeded Warehouse/Part/StockLevel/AuditEvent data
#   - /var/lib/gel/6/data: baked database, /var/lib/gel/run: runstate dir, /var/log/gel-server.log
#   - /opt/harbor/initial_state.json: build-time snapshot of the initial revisions and seed data
#     used by the verification tests

COPY test_initial_state.py /bootstrap/test_initial_state.py

# Setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
