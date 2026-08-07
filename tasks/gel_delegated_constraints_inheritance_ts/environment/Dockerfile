FROM ubuntu:24.04

ARG POCHI_VERSION

# ---------------------------------------------------------------------------
# Base tooling, Node.js 24, and the Gel 6 server + CLI (all from apt, no cloud)
# NOTE: `python3-pytest` is deliberately NOT installed from apt. The distro
# pytest is too old for `pytest-json-ctrf`, which then crashes the whole run
# with `AttributeError: 'TestReport' object has no attribute 'start'`.
# ---------------------------------------------------------------------------
RUN apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl wget ca-certificates gnupg git python3 python3-pip bash ripgrep \
        procps xz-utils && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    curl -fsSL https://deb.nodesource.com/setup_24.x | bash - && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends nodejs && \
    mkdir -p /usr/local/share/keyrings && \
    curl --proto '=https' --tlsv1.2 -sSf \
        -o /usr/local/share/keyrings/gel-keyring.gpg \
        https://packages.geldata.com/keys/gel-keyring.gpg && \
    echo "deb [signed-by=/usr/local/share/keyrings/gel-keyring.gpg] https://packages.geldata.com/apt noble main" \
        > /etc/apt/sources.list.d/gel.list && \
    apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends gel-6 && \
    curl -fsSL https://getpochi.com/install.sh | bash -s ${POCHI_VERSION} && \
    ln -s /root/.pochi/bin/pochi /usr/local/bin/pochi && \
    pip3 install --break-system-packages "pytest>=8" pytest-json-ctrf && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN mkdir -p /home/user && chmod 0755 /home/user

# ---------------------------------------------------------------------------
# Local Gel server lifecycle helper.
# Resolves the server binary defensively (its name/location varies by version
# and install method) and always passes an explicit --runstate-dir, because the
# default /run/gel is root-owned while gel-server refuses to run as root.
# ---------------------------------------------------------------------------
RUN cat >/usr/local/bin/gel-resolve-server-bin.sh <<'EOF'
#!/usr/bin/env bash
# Print the absolute path of the local gel-server binary, or fail loudly.
for candidate in gel-server gel-server-6 gel-server-7; do
    if command -v "$candidate" >/dev/null 2>&1; then
        command -v "$candidate"
        exit 0
    fi
done
for pattern in /usr/lib/*/gel-server-*/bin/gel-server /usr/bin/gel-server*; do
    resolved=$(ls $pattern 2>/dev/null | head -n 1)
    if [ -n "$resolved" ] && [ -x "$resolved" ]; then
        echo "$resolved"
        exit 0
    fi
done
echo "FATAL: no gel-server binary could be located on this image" >&2
exit 1
EOF
RUN cat >/usr/local/bin/gel-ensure-server.sh <<'EOF'
#!/usr/bin/env bash
# Idempotently start the local Gel server and block until it answers queries.
DATA_DIR=/var/lib/gel/data
RUNSTATE_DIR=/var/lib/gel/run
LOG_FILE=/var/log/gel-server.log

is_ready() {
    gel query "select 1" >/dev/null 2>&1
}

if is_ready; then
    echo "gel server already running"
    exit 0
fi

SERVER_BIN=$(bash /usr/local/bin/gel-resolve-server-bin.sh) || exit 1
echo "using gel server binary: ${SERVER_BIN}"

mkdir -p "$DATA_DIR" "$RUNSTATE_DIR"
touch "$LOG_FILE"
chown -R gel:gel "$DATA_DIR" "$RUNSTATE_DIR" "$LOG_FILE"

su gel -s /bin/bash -c "nohup '${SERVER_BIN}' \
    --data-dir='${DATA_DIR}' \
    --runstate-dir='${RUNSTATE_DIR}' \
    --bind-address=127.0.0.1 \
    --port=5656 \
    --tls-cert-mode=generate_self_signed \
    >>'${LOG_FILE}' 2>&1 &"

for _ in $(seq 1 180); do
    if is_ready; then
        echo "gel server is ready"
        exit 0
    fi
    sleep 1
done

echo "FATAL: the local Gel server did not become ready in time" >&2
tail -n 80 "$LOG_FILE" >&2
exit 1
EOF
RUN chmod 0755 /usr/local/bin/gel-resolve-server-bin.sh /usr/local/bin/gel-ensure-server.sh

# ---------------------------------------------------------------------------
# Bootstrap the database cluster once at build time (admin password + branch).
# ---------------------------------------------------------------------------
RUN id -u gel >/dev/null 2>&1 || useradd -r -M -s /bin/bash -d /var/lib/gel gel
RUN mkdir -p /var/lib/gel/data /var/lib/gel/run && \
    chown -R gel:gel /var/lib/gel && \
    SERVER_BIN=$(bash /usr/local/bin/gel-resolve-server-bin.sh) && \
    su gel -s /bin/bash -c "'${SERVER_BIN}' \
        --data-dir=/var/lib/gel/data \
        --runstate-dir=/var/lib/gel/run \
        --tls-cert-mode=generate_self_signed \
        --bootstrap-only \
        --bootstrap-command=\"ALTER ROLE admin SET password := 'gelpassword'\""

# Connection settings for the bundled, local-only Gel instance. These are not
# secrets: the server listens on 127.0.0.1 inside this container only.
ENV GEL_HOST=127.0.0.1 \
    GEL_PORT=5656 \
    GEL_USER=admin \
    GEL_PASSWORD=gelpassword \
    GEL_BRANCH=main \
    GEL_CLIENT_TLS_SECURITY=insecure

# ---------------------------------------------------------------------------
# Project scaffold: an empty Gel project plus pre-installed npm dependencies so
# that the task can be solved fully offline.
# ---------------------------------------------------------------------------
RUN mkdir -p /home/user/assetreg/dbschema /home/user/assetreg/src
RUN cat >/home/user/assetreg/gel.toml <<'EOF'
[instance]
server-version = "6.11"
EOF
RUN cat >/home/user/assetreg/dbschema/default.gel <<'EOF'
module default {
}
EOF
RUN cat >/home/user/assetreg/src/.gitkeep <<'EOF'
EOF
RUN cat >/home/user/assetreg/package.json <<'EOF'
{
  "name": "assetreg",
  "version": "1.0.0",
  "private": true,
  "description": "Fleet asset compliance registry ingest tool",
  "dependencies": {
    "gel": "2.2.0"
  },
  "devDependencies": {
    "@types/node": "^24",
    "tsx": "4.23.9",
    "typescript": "^5.9.0"
  }
}
EOF
RUN cd /home/user/assetreg && \
    npm install --no-audit --no-fund --loglevel=error && \
    npm config set offline true && \
    chmod -R 0777 /home/user/assetreg

LABEL Author="generate-harbor-tasks"
LABEL Description="Gel 6 (server + CLI) with Node.js 24 and a pre-installed offline npm project for a delegated-constraint/inheritance schema and TypeScript ingest task."

CMD ["/bin/bash", "-c", "trap : TERM INT; sleep infinity & wait"]

# Container contents:
#   - always required apt packages: curl wget ca-certificates git python3 python3-pip bash ripgrep
#     (python3-pytest intentionally omitted; a modern pytest comes from pip3)
#   - always required pip3 packages: pytest, pytest-json-ctrf
#   - Node.js 24 (NodeSource) for the TypeScript ingest CLI
#   - gel-6 apt package: local gel-server 6.11 + gel CLI, no cloud/SaaS usage
#   - /usr/local/bin/gel-resolve-server-bin.sh: defensive gel-server binary lookup
#   - /usr/local/bin/gel-ensure-server.sh: idempotent local server start + readiness wait
#   - /var/lib/gel/data: bootstrapped database cluster (admin password preset)
#   - /var/lib/gel/run: explicit runstate dir owned by the unprivileged `gel` user
#   - /home/user/assetreg: empty Gel project (gel.toml, dbschema/default.gel, src/)
#   - /home/user/assetreg/node_modules: gel client + typescript + tsx, pre-installed

COPY test_initial_state.py /bootstrap/test_initial_state.py

# Setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
