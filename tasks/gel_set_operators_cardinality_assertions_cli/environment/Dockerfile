FROM ubuntu:24.04

ARG POCHI_VERSION
ARG GEL_MAJOR=6

ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

# --------------------------------------------------------------------------- #
# Base tooling: always-required packages, Pochi CLI, pytest stack.
# NOTE: python3-pytest is deliberately NOT installed from apt -- the distro
# build is too old for pytest-json-ctrf and aborts the whole verifier run.
# --------------------------------------------------------------------------- #
RUN apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl wget ca-certificates git python3 python3-pip bash ripgrep \
        jq util-linux procps coreutils && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    curl -fsSL https://getpochi.com/install.sh | bash -s ${POCHI_VERSION} && \
    ln -s /root/.pochi/bin/pochi /usr/local/bin/pochi && \
    pip3 install --break-system-packages pytest pytest-json-ctrf && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# --------------------------------------------------------------------------- #
# Gel 6: official apt repository (server + `gel` CLI).
# --------------------------------------------------------------------------- #
RUN mkdir -p /usr/local/share/keyrings && \
    curl --proto '=https' --tlsv1.2 -sSf \
        -o /usr/local/share/keyrings/gel-keyring.gpg \
        https://packages.geldata.com/keys/gel-keyring.gpg && \
    echo "deb [signed-by=/usr/local/share/keyrings/gel-keyring.gpg] https://packages.geldata.com/apt $(grep VERSION_CODENAME= /etc/os-release | cut -d= -f2) main" \
        > /etc/apt/sources.list.d/gel.list && \
    apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        gel-${GEL_MAJOR} && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# The `gel` CLI must be on PATH; fall back to the official installer.
RUN if ! command -v gel >/dev/null 2>&1; then \
        curl --proto '=https' --tlsv1.2 -sSfL https://geldata.com/sh | sh -s -- -y && \
        ln -sf /root/.local/bin/gel /usr/local/bin/gel; \
    fi && \
    gel --version

# The server binary name varies across images/versions; resolve it defensively
# once and expose it under a stable path instead of hardcoding a guess.
RUN candidate=""; \
    for name in gel-server gel-server-${GEL_MAJOR}; do \
        if command -v "$name" >/dev/null 2>&1; then candidate="$(command -v "$name")"; break; fi; \
    done; \
    if [ -z "$candidate" ]; then candidate="$(ls /usr/bin/gel-server* 2>/dev/null | head -n 1)"; fi; \
    if [ -z "$candidate" ]; then candidate="$(ls /usr/lib/*/gel-server-*/bin/gel-server 2>/dev/null | head -n 1)"; fi; \
    if [ -z "$candidate" ]; then echo "FATAL: no gel-server binary found in this image" >&2; exit 1; fi; \
    ln -sf "$candidate" /usr/local/bin/gel-server-bin; \
    /usr/local/bin/gel-server-bin --version

# --------------------------------------------------------------------------- #
# Client connection settings for the local, container-only instance.
# --------------------------------------------------------------------------- #
ENV GEL_HOST=127.0.0.1
ENV GEL_PORT=5656
ENV GEL_USER=admin
ENV GEL_BRANCH=main
ENV GEL_CLIENT_SECURITY=insecure_dev_mode

# --------------------------------------------------------------------------- #
# Unprivileged account for the server (gel-server refuses to run as root) and
# an explicit, pre-owned runstate directory (never rely on the /run default).
# --------------------------------------------------------------------------- #
RUN useradd --system --create-home --home-dir /var/lib/gelsrv/home --shell /usr/sbin/nologin gelsrv && \
    mkdir -p /var/lib/gelsrv/data /var/lib/gelsrv/run /var/log/gelsrv && \
    chown -R gelsrv:gelsrv /var/lib/gelsrv /var/log/gelsrv && \
    chmod 0755 /var/lib/gelsrv /var/log/gelsrv

RUN cat > /usr/local/bin/gel-ctl <<'GELCTL'
#!/bin/bash
# gel-ctl {start|stop|restart|status|logs}
# Idempotent lifecycle helper for the container-local Gel server.
DATA_DIR=/var/lib/gelsrv/data
RUN_DIR=/var/lib/gelsrv/run
LOG_DIR=/var/log/gelsrv
LOG_FILE="$LOG_DIR/server.log"
PID_FILE=/var/lib/gelsrv/server.pid
SERVER_BIN=/usr/local/bin/gel-server-bin
SRV_USER=gelsrv

server_ready() {
    gel query --wait-until-available 1sec "select 1" >/dev/null 2>&1
}

start_server() {
    if server_ready; then
        echo "gel-ctl: server already running"
        return 0
    fi
    mkdir -p "$DATA_DIR" "$RUN_DIR" "$LOG_DIR"
    chown -R "$SRV_USER":"$SRV_USER" "$DATA_DIR" "$RUN_DIR" "$LOG_DIR"
    runuser -u "$SRV_USER" -- env \
        GEL_SERVER_SECURITY=insecure_dev_mode \
        GEL_SERVER_TLS_CERT_MODE=generate_self_signed \
        GEL_SERVER_COMPILER_POOL_MODE=on_demand \
        GEL_SERVER_COMPILER_POOL_SIZE=1 \
        GEL_SERVER_LOG_LEVEL=info \
        "$SERVER_BIN" \
            --data-dir="$DATA_DIR" \
            --runstate-dir="$RUN_DIR" \
            --port=5656 \
            --bind-address=127.0.0.1 \
        >>"$LOG_FILE" 2>&1 &
    echo $! >"$PID_FILE"
    i=0
    while [ "$i" -lt 240 ]; do
        if server_ready; then
            echo "gel-ctl: server ready on 127.0.0.1:5656"
            return 0
        fi
        i=$((i + 1))
        sleep 1
    done
    echo "gel-ctl: server did not become ready within 240s" >&2
    tail -n 80 "$LOG_FILE" >&2
    return 1
}

stop_server() {
    pkill -f gel-server >/dev/null 2>&1
    rm -f "$PID_FILE"
    i=0
    while [ "$i" -lt 90 ]; do
        if ! server_ready; then
            echo "gel-ctl: server stopped"
            return 0
        fi
        i=$((i + 1))
        sleep 1
    done
    echo "gel-ctl: server did not stop" >&2
    return 1
}

case "${1:-}" in
    start)   start_server ;;
    stop)    stop_server ;;
    restart) stop_server; start_server ;;
    status)
        if server_ready; then
            echo "gel-ctl: running"
        else
            echo "gel-ctl: not running"
            exit 1
        fi
        ;;
    logs)    tail -n "${2:-100}" "$LOG_FILE" ;;
    *)
        echo "usage: gel-ctl {start|stop|restart|status|logs [n]}" >&2
        exit 2
        ;;
esac
GELCTL

RUN chmod 0755 /usr/local/bin/gel-ctl

# --------------------------------------------------------------------------- #
# Seeded reconciliation project.
# --------------------------------------------------------------------------- #
RUN mkdir -p /home/user && chmod 0755 /home/user
RUN mkdir -p /home/user/reconcile/dbschema

RUN cat > /home/user/reconcile/gel.toml <<'GELTOML'
[instance]
server-version = "6"
GELTOML

RUN cat > /home/user/reconcile/dbschema/default.gel <<'SCHEMA'
module default {
    type Warehouse {
        required code: str {
            constraint exclusive;
        }
        required region: str;
    }

    type Sku {
        required code: str {
            constraint exclusive;
        }
        required label: str;
    }

    type ShelfCount {
        required warehouse: Warehouse;
        required sku: Sku;
        required quantity: int64;
        required tag: str;
    }

    type LedgerLine {
        required warehouse: Warehouse;
        required sku: Sku;
        required quantity: int64;
        required tag: str;
    }
}
SCHEMA

RUN cat > /tmp/seed.edgeql <<'SEED'
for row in {
    ('W-AMS', 'eu-west'),
    ('W-BER', 'eu-central'),
    ('W-CDG', 'eu-west'),
    ('W-DUB', 'eu-west'),
} union (
    insert Warehouse { code := row.0, region := row.1 }
);

for row in {
    ('SKU-001', 'Pallet jack'),
    ('SKU-002', 'Shrink wrap'),
    ('SKU-003', 'Label roll'),
    ('SKU-004', 'Barcode scanner'),
    ('SKU-005', 'Stacking crate'),
    ('SKU-006', 'Safety vest'),
    ('SKU-007', 'Dock bumper'),
} union (
    insert Sku { code := row.0, label := row.1 }
);

for row in {
    ('W-AMS', 'SKU-001', 5),
    ('W-AMS', 'SKU-002', 3),
    ('W-AMS', 'SKU-002', 4),
    ('W-AMS', 'SKU-003', 2),
    ('W-BER', 'SKU-001', 7),
    ('W-BER', 'SKU-004', 1),
    ('W-CDG', 'SKU-005', 4),
    ('W-CDG', 'SKU-005', 5),
} union (
    insert ShelfCount {
        warehouse := assert_single((select Warehouse filter .code = row.0)),
        sku := assert_single((select Sku filter .code = row.1)),
        quantity := row.2,
        tag := 'baseline',
    }
);

for row in {
    ('W-AMS', 'SKU-001', 5),
    ('W-AMS', 'SKU-002', 7),
    ('W-AMS', 'SKU-006', 2),
    ('W-BER', 'SKU-001', 7),
    ('W-BER', 'SKU-004', 1),
    ('W-CDG', 'SKU-005', 4),
} union (
    insert LedgerLine {
        warehouse := assert_single((select Warehouse filter .code = row.0)),
        sku := assert_single((select Sku filter .code = row.1)),
        quantity := row.2,
        tag := 'baseline',
    }
);
SEED

# Bootstrap the instance: first migration + deterministic seed data, then stop
# the server again so the container starts from a clean, reproducible state.
RUN cd /home/user/reconcile && \
    gel-ctl start && \
    gel migration create --non-interactive && \
    gel migrate && \
    gel query --file /tmp/seed.edgeql && \
    gel query -F json "select count(ShelfCount)" && \
    gel query -F json "select count(LedgerLine)" && \
    gel migration status && \
    rm -f /tmp/seed.edgeql && \
    gel-ctl stop

RUN chmod -R 0755 /home/user/reconcile

LABEL Author="generate-harbor-tasks"
LABEL Description="Gel 6 server + CLI with a seeded warehouse reconciliation project at /home/user/reconcile for EdgeQL set-operator and cardinality work."

CMD ["/bin/bash", "-c", "trap : TERM INT; sleep infinity & wait"]

# Container contents:
#   - always required apt packages: curl wget ca-certificates git python3 python3-pip bash ripgrep
#   - extra apt packages: jq util-linux procps coreutils (shell-only solutions + runuser/pkill)
#   - pip3 packages: pytest, pytest-json-ctrf (modern pytest on purpose; the apt
#     python3-pytest build breaks pytest-json-ctrf and aborts the whole run)
#   - Gel 6 (server + `gel` CLI) from https://packages.geldata.com/apt
#   - /usr/local/bin/gel-server-bin: defensively resolved gel-server binary
#   - /usr/local/bin/gel-ctl: idempotent start/stop/status helper for the local
#     server; always passes an explicit --runstate-dir and runs as `gelsrv`
#   - /var/lib/gelsrv/{data,run}: server data + runstate, owned by `gelsrv`
#   - /home/user/reconcile: Gel project (gel.toml, dbschema/default.gel,
#     dbschema/migrations/<hash>.edgeql) with Warehouse/Sku/ShelfCount/LedgerLine
#     seeded with the deterministic audit dataset used by the tests

COPY test_initial_state.py /bootstrap/test_initial_state.py

# Setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
