FROM ubuntu:24.04

ARG POCHI_VERSION

# ---------------------------------------------------------------------------
# Base tooling + Gel 6 server/CLI + Python client stack.
#
# NOTE: `python3-pytest` is deliberately NOT installed from apt. The distro
# package is too old for `pytest-json-ctrf`, which reads `TestReport.start`
# and aborts the whole run with an INTERNALERROR ("no tests ran"). pytest is
# installed from PyPI instead so both stay mutually compatible.
# ---------------------------------------------------------------------------
RUN apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl wget ca-certificates git python3 python3-pip bash ripgrep \
        gnupg procps netcat-openbsd jq locales && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    curl -fsSL https://getpochi.com/install.sh | bash -s ${POCHI_VERSION} && \
    ln -s /root/.pochi/bin/pochi /usr/local/bin/pochi && \
    mkdir -p /usr/local/share/keyrings && \
    curl --proto '=https' --tlsv1.2 -sSf \
        -o /usr/local/share/keyrings/gel-keyring.gpg \
        https://packages.geldata.com/keys/gel-keyring.gpg && \
    echo "deb [signed-by=/usr/local/share/keyrings/gel-keyring.gpg] https://packages.geldata.com/apt noble main" \
        > /etc/apt/sources.list.d/gel.list && \
    apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends gel-6 && \
    pip3 install --break-system-packages --no-cache-dir \
        "pytest>=8.3,<9" pytest-json-ctrf "gel==3.1.0" "psycopg[binary]==3.3.4" && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

# Connection parameters for the PostgreSQL wire protocol (SQL adapter). These
# point at the local Gel server inside this container; there is no external
# service and no real secret involved.
ENV GEL_SQL_HOST=127.0.0.1
ENV GEL_SQL_PORT=5656
ENV GEL_SQL_USER=admin
ENV GEL_SQL_PASSWORD=gelbench
ENV GEL_SQL_DBNAME=main

RUN mkdir -p /home/user && chmod 0755 /home/user

# ---------------------------------------------------------------------------
# Dedicated unprivileged account for the server (gel-server refuses to run as
# root) plus an explicit runstate directory (the default /run/gel is root-owned
# tmpfs and makes non-root startups fail).
# ---------------------------------------------------------------------------
RUN (id gelsrv >/dev/null 2>&1 || useradd -r -m -d /var/lib/gel -s /bin/bash gelsrv) && \
    mkdir -p /var/lib/gel/data /var/lib/gel/run && \
    touch /var/log/gel-server.log && \
    chown -R gelsrv:gelsrv /var/lib/gel /var/log/gel-server.log

# Resolve the server binary defensively: the name varies between images and
# major versions (gel-server, gel-server-6, gel-server-7, ...).
RUN cat > /usr/local/bin/gel-server-path <<'EOF'
#!/bin/bash
for candidate in gel-server gel-server-6 gel-server-7; do
    if command -v "$candidate" >/dev/null 2>&1; then
        command -v "$candidate"
        exit 0
    fi
done
found="$(ls /usr/bin/gel-server* 2>/dev/null | head -n 1)"
if [ -n "$found" ]; then
    echo "$found"
    exit 0
fi
echo "FATAL: no gel-server binary found (tried gel-server, gel-server-6, gel-server-7, /usr/bin/gel-server*)" >&2
exit 1
EOF

RUN cat > /usr/local/bin/gel-start <<'EOF'
#!/bin/bash
# Idempotently start the local Gel server and block until it answers queries.
PORT=5656
DB_USER=admin
DB_PASSWORD=gelbench
DB_BRANCH=main
DATA_DIR=/var/lib/gel/data
RUN_DIR=/var/lib/gel/run
LOG_FILE=/var/log/gel-server.log

ready() {
    gel --dsn "gel://${DB_USER}:${DB_PASSWORD}@127.0.0.1:${PORT}/${DB_BRANCH}" \
        --tls-security insecure query 'select 1' >/dev/null 2>&1
}

if ready; then
    echo "gel-server already running on port ${PORT}"
    exit 0
fi

BIN="$(/usr/local/bin/gel-server-path)" || exit 1

mkdir -p "$RUN_DIR"
touch "$LOG_FILE"
chown gelsrv:gelsrv "$RUN_DIR" "$LOG_FILE"

su gelsrv -s /bin/bash -c "GEL_SERVER_BINARY_ENDPOINT_SECURITY=optional nohup '${BIN}' --data-dir='${DATA_DIR}' --runstate-dir='${RUN_DIR}' --tls-cert-mode=generate_self_signed --port=${PORT} --bind-address=127.0.0.1 --log-level=warn >> '${LOG_FILE}' 2>&1 &"

for _ in $(seq 1 180); do
    if ready; then
        echo "gel-server is ready on port ${PORT}"
        exit 0
    fi
    sleep 1
done

echo "FATAL: gel-server did not become ready within 180 seconds" >&2
tail -n 80 "$LOG_FILE" >&2
exit 1
EOF

RUN cat > /usr/local/bin/gel-stop <<'EOF'
#!/bin/bash
# Stop the local Gel server if it is running.
pkill -f 'gel-server' >/dev/null 2>&1
for _ in $(seq 1 60); do
    if ! pgrep -f 'gel-server' >/dev/null 2>&1; then
        echo "gel-server stopped"
        exit 0
    fi
    sleep 1
done
pkill -9 -f 'gel-server' >/dev/null 2>&1
echo "gel-server force-stopped"
exit 0
EOF

RUN chmod 0755 /usr/local/bin/gel-server-path /usr/local/bin/gel-start /usr/local/bin/gel-stop

# ---------------------------------------------------------------------------
# Bootstrap the data directory (sets the admin password used by both the
# binary protocol and the SQL adapter).
# ---------------------------------------------------------------------------
RUN BIN="$(/usr/local/bin/gel-server-path)" && \
    su gelsrv -s /bin/bash -c "'${BIN}' --data-dir=/var/lib/gel/data --runstate-dir=/var/lib/gel/run --tls-cert-mode=generate_self_signed --bootstrap-command=\"ALTER ROLE admin SET password := 'gelbench'\" --bootstrap-only --log-level=warn"

# ---------------------------------------------------------------------------
# Project skeleton: gel.toml + empty schema directory. No user schema and no
# migrations yet - those are the executor's job.
# ---------------------------------------------------------------------------
RUN mkdir -p /home/user/project/dbschema /home/user/project/data

RUN cat > /home/user/project/gel.toml <<'EOF'
[instance]
server-version = "6.11"

[project]
schema-dir = "dbschema"
EOF

RUN cat > /home/user/project/dbschema/default.gel <<'EOF'
module default {
}
EOF

RUN cat > /home/user/project/data/seed.json <<'EOF'
{
  "artists": [
    {
      "handle": "aurora-lane",
      "name": "Aurora Lane",
      "country": "SE",
      "aliases": ["Lane", "A.L.", "Aurora"]
    },
    {
      "handle": "brick-mono",
      "name": "Brick Mono",
      "country": "US",
      "aliases": []
    },
    {
      "handle": "cassia-vek",
      "name": "Cassia Vek",
      "country": "BR",
      "aliases": ["Vek"]
    },
    {
      "handle": "delta-nine",
      "name": "Delta Nine",
      "country": "JP",
      "aliases": ["D9", "Delta"]
    },
    {
      "handle": "echo-void",
      "name": "Echo Void",
      "country": "DE",
      "aliases": ["EV"]
    }
  ],
  "albums": [
    {
      "slug": "alb-northern-lights",
      "title": "Northern Lights",
      "year": 1999,
      "label": "Fjord Records"
    },
    {
      "slug": "alb-quiet-machines",
      "title": "Quiet Machines",
      "year": 2007,
      "label": null
    },
    {
      "slug": "alb-zero-hour",
      "title": "Zero Hour",
      "year": 2007,
      "label": "Zero Hour Media"
    }
  ],
  "tracks": [
    {
      "slug": "trk-aurora-drift",
      "title": "Aurora Drift",
      "album": "alb-northern-lights",
      "duration_ms": 214300,
      "royalty_rate": "0.125",
      "tags": ["ambient", "night"],
      "contributors": [
        {"artist": "aurora-lane", "role": "lead vocals", "share_bp": 6000},
        {"artist": "delta-nine", "role": "synth", "share_bp": 4000}
      ]
    },
    {
      "slug": "trk-brick-house",
      "title": "Brick House",
      "album": "alb-northern-lights",
      "duration_ms": 187000,
      "royalty_rate": "0.0725",
      "tags": ["rock"],
      "contributors": [
        {"artist": "brick-mono", "role": "guitar", "share_bp": 10000}
      ]
    },
    {
      "slug": "trk-cafe-varsang",
      "title": "Café Vårsång 変奏曲",
      "album": "alb-northern-lights",
      "duration_ms": 305999,
      "royalty_rate": "0.5",
      "tags": ["ambient", "live", "instrumental"],
      "contributors": [
        {"artist": "cassia-vek", "role": "piano", "share_bp": 5000},
        {"artist": "aurora-lane", "role": "strings", "share_bp": 3000},
        {"artist": "delta-nine", "role": "percussion", "share_bp": 2000}
      ]
    },
    {
      "slug": "trk-delta-pulse",
      "title": "Delta Pulse",
      "album": "alb-quiet-machines",
      "duration_ms": 121001,
      "royalty_rate": "0.33333",
      "tags": [],
      "contributors": [
        {"artist": "delta-nine", "role": "synth", "share_bp": 10000}
      ]
    },
    {
      "slug": "trk-echoless",
      "title": "Echoless",
      "album": "alb-quiet-machines",
      "duration_ms": 95500,
      "royalty_rate": "0.2",
      "tags": ["ambient"],
      "contributors": []
    },
    {
      "slug": "trk-zenith",
      "title": "Zenith",
      "album": "alb-quiet-machines",
      "duration_ms": 240000,
      "royalty_rate": "0.1075",
      "tags": ["night", "instrumental"],
      "contributors": [
        {"artist": "aurora-lane", "role": "vocals", "share_bp": 7000},
        {"artist": "brick-mono", "role": "bass", "share_bp": 3000}
      ]
    }
  ]
}
EOF

# ---------------------------------------------------------------------------
# Link the running server as the instance `bench` and attach it to the project.
# ---------------------------------------------------------------------------
RUN /usr/local/bin/gel-start && \
    gel instance link \
        --dsn "gel://admin:gelbench@127.0.0.1:5656/main" \
        --non-interactive --trust-tls-cert --overwrite --quiet bench && \
    cd /home/user/project && \
    gel project init --link --server-instance bench --branch main --no-migrations && \
    gel -I bench query 'select 1' && \
    /usr/local/bin/gel-stop

LABEL Author="generate-harbor-tasks"
LABEL Description="Local Gel 6 server (binary + PostgreSQL wire protocols) with a linked, schema-less project at /home/user/project, a seed dataset, and the Python gel/psycopg clients preinstalled."

CMD ["/bin/bash", "-c", "trap : TERM INT; sleep infinity & wait"]

# Container contents:
#   - always required apt packages: curl wget ca-certificates git python3 python3-pip bash ripgrep
#     (python3-pytest intentionally omitted; pytest comes from PyPI)
#   - always required pip3 packages: pytest-json-ctrf (plus pytest)
#   - gel-6 apt package: gel-server-6 (Gel 6.x) and the `gel` CLI
#   - pip3: gel==3.1.0 (Python binary-protocol client), psycopg[binary]==3.3.4 (PostgreSQL wire protocol)
#   - gelsrv system user owning /var/lib/gel/{data,run}; data dir bootstrapped with admin password
#   - /usr/local/bin/gel-server-path: defensive gel-server binary resolver
#   - /usr/local/bin/gel-start: idempotent server start + readiness wait
#   - /usr/local/bin/gel-stop: server shutdown
#   - /home/user/project: Gel project linked to instance `bench` (gel.toml, gel.local.toml, dbschema/default.gel)
#   - /home/user/project/data/seed.json: deterministic catalog dataset (5 artists, 3 albums, 6 tracks)
#   - GEL_SQL_* env vars: connection parameters for the local SQL adapter

COPY test_initial_state.py /bootstrap/test_initial_state.py

# Setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
