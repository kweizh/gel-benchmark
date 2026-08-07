FROM ubuntu:24.04

ARG POCHI_VERSION

# Connection settings for both the `gel` CLI and the Python client.
# The instance is local-only: it binds 127.0.0.1 and speaks plain HTTP on 5656.
ENV GEL_HOST=127.0.0.1 \
    GEL_PORT=5656 \
    GEL_USER=admin \
    GEL_BRANCH=main \
    GEL_CLIENT_TLS_SECURITY=insecure

RUN apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl wget ca-certificates git python3 python3-pip bash ripgrep gnupg jq procps && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    curl -fsSL https://getpochi.com/install.sh | bash -s ${POCHI_VERSION} && \
    ln -s /root/.pochi/bin/pochi /usr/local/bin/pochi && \
    pip3 install --break-system-packages --no-cache-dir \
        "pytest>=8.0" pytest-json-ctrf requests "gel>=3.0,<4.0" && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Gel 7 server + CLI from the official apt repository.
# NOTE: the pip `gel` package ships a /usr/local/bin/gel shim that would shadow the
# real CLI at /usr/bin/gel (and /usr/local/bin comes first on PATH), so drop it.
RUN mkdir -p /usr/local/share/keyrings && \
    curl --proto '=https' --tlsv1.2 -sSf \
        -o /usr/local/share/keyrings/gel-keyring.gpg \
        https://packages.geldata.com/keys/gel-keyring.gpg && \
    echo "deb [signed-by=/usr/local/share/keyrings/gel-keyring.gpg] https://packages.geldata.com/apt $(grep VERSION_CODENAME= /etc/os-release | cut -d= -f2) main" \
        > /etc/apt/sources.list.d/gel.list && \
    apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends gel-7 && \
    rm -f /usr/local/bin/gel && \
    ln -s /usr/bin/gel /usr/local/bin/gel && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Helper: resolve the server binary defensively (its name varies by version and
# install method) and always pass an explicit --runstate-dir owned by the
# unprivileged account the server runs as (gel-server refuses to run as root).
RUN cat >/usr/local/bin/start-gel-server.sh <<'EOF'
#!/usr/bin/env bash
# Idempotent: starts the local Gel server and blocks until it answers queries.
GEL_DATA_DIR=/var/lib/gel/data
GEL_RUN_DIR=/var/lib/gel/run
GEL_LOG=/var/log/gel/server.log
GEL_SRV_USER=gel
PORT="${GEL_PORT:-5656}"

probe() {
    gel -H 127.0.0.1 -P "${PORT}" --tls-security insecure query 'select 1' >/dev/null 2>&1
}

resolve_server() {
    local candidate
    for candidate in gel-server gel-server-7 gel-server-6; do
        if command -v "${candidate}" >/dev/null 2>&1; then
            command -v "${candidate}"
            return 0
        fi
    done
    candidate="$(ls /usr/bin/gel-server* 2>/dev/null | head -n 1)"
    if [ -n "${candidate}" ]; then
        echo "${candidate}"
        return 0
    fi
    echo "ERROR: no gel-server binary found (looked for gel-server, gel-server-N, /usr/bin/gel-server*)" >&2
    return 1
}

if probe; then
    echo "gel server already running on 127.0.0.1:${PORT}"
    exit 0
fi

SERVER_BIN="$(resolve_server)" || exit 1

mkdir -p "${GEL_DATA_DIR}" "${GEL_RUN_DIR}" "$(dirname "${GEL_LOG}")"
touch "${GEL_LOG}"
chown -R "${GEL_SRV_USER}:${GEL_SRV_USER}" "${GEL_DATA_DIR}" "${GEL_RUN_DIR}" "$(dirname "${GEL_LOG}")"

su "${GEL_SRV_USER}" -s /bin/bash -c "'${SERVER_BIN}' \
    --data-dir='${GEL_DATA_DIR}' \
    --runstate-dir='${GEL_RUN_DIR}' \
    --port='${PORT}' \
    --bind-address=127.0.0.1 \
    --security=insecure_dev_mode \
    --http-endpoint-security=optional \
    --tls-cert-mode=generate_self_signed \
    --compiler-pool-mode=on_demand \
    --compiler-pool-size=2 \
    >>'${GEL_LOG}' 2>&1 &"

for _ in $(seq 1 300); do
    if probe; then
        echo "gel server ready on 127.0.0.1:${PORT}"
        exit 0
    fi
    sleep 1
done

echo "ERROR: the gel server did not become ready in time" >&2
tail -n 60 "${GEL_LOG}" >&2
exit 1
EOF

RUN cat >/usr/local/bin/stop-gel-server.sh <<'EOF'
#!/usr/bin/env bash
# Stops the local Gel server if it is running.
# Match on the data-dir argument rather than on "gel-server": the latter also
# matches this script's own path (and its parent shell), so pkill would kill
# the calling process and the command would exit 143.
PATTERN='data-dir=/var/lib/gel/data'
pkill -TERM -f "${PATTERN}" >/dev/null 2>&1
for _ in $(seq 1 60); do
    if ! pgrep -f "${PATTERN}" >/dev/null 2>&1; then
        echo "gel server stopped"
        exit 0
    fi
    sleep 1
done
pkill -KILL -f "${PATTERN}" >/dev/null 2>&1
echo "gel server force stopped"
exit 0
EOF

RUN chmod 0755 /usr/local/bin/start-gel-server.sh /usr/local/bin/stop-gel-server.sh && \
    (id -u gel >/dev/null 2>&1 || useradd --system --home-dir /var/lib/gel --shell /bin/bash gel) && \
    mkdir -p /var/lib/gel/data /var/lib/gel/run /var/log/gel && \
    chown -R gel:gel /var/lib/gel /var/log/gel

# Bootstrap the data directory at build time so the first runtime start is fast,
# then shut the server down cleanly.
RUN /usr/local/bin/start-gel-server.sh && \
    gel query 'select 1' && \
    gel query 'select count(schema::Migration)' && \
    /usr/local/bin/stop-gel-server.sh

RUN mkdir -p /home/user && chmod 0755 /home/user
RUN mkdir -p /home/user/gateway/data /home/user/gateway/dbschema

RUN cat >/home/user/gateway/data/seed.json <<'EOF'
{
  "teams": [
    { "name": "atlas", "region": "eu-west" },
    { "name": "borealis", "region": "us-east" },
    { "name": "cygnus", "region": "eu-west" }
  ],
  "services": [
    { "name": "auth-api", "tier": 1, "active": true, "team": "atlas" },
    { "name": "billing-api", "tier": 2, "active": true, "team": "borealis" },
    { "name": "cache-proxy", "tier": 3, "active": false, "team": "atlas" },
    { "name": "dns-relay", "tier": 2, "active": true, "team": "cygnus" },
    { "name": "edge-router", "tier": 1, "active": true, "team": "borealis" },
    { "name": "feed-worker", "tier": 3, "active": true, "team": "atlas" },
    { "name": "graph-sync", "tier": 2, "active": false, "team": "cygnus" },
    { "name": "heartbeat", "tier": 1, "active": true, "team": "borealis" },
    { "name": "index-builder", "tier": 3, "active": true, "team": "cygnus" }
  ]
}
EOF

RUN chmod 0644 /home/user/gateway/data/seed.json

LABEL Author="generate-harbor-tasks"
LABEL Description="Ubuntu 24.04 with a local Gel 7 server (loopback only, plain HTTP enabled), the gel CLI, the Python gel client and a service-registry seed fixture in /home/user/gateway."

CMD ["/bin/bash", "-c", "trap : TERM INT; sleep infinity & wait"]

# Container contents:
#   - always required apt packages: curl wget ca-certificates git python3 python3-pip bash ripgrep
#     (python3-pytest is deliberately NOT installed from apt: the distro pytest is too old for
#      pytest-json-ctrf and aborts the whole run with an INTERNALERROR)
#   - always required pip3 packages: pytest-json-ctrf (plus a modern pytest), requests, gel
#   - Gel 7 server (gel-7 / gel-server-7) and the gel CLI from packages.geldata.com
#   - /usr/local/bin/start-gel-server.sh: idempotent loopback-only server starter
#     (explicit --runstate-dir, defensive server-binary resolution, small compiler pool)
#   - /usr/local/bin/stop-gel-server.sh: graceful shutdown helper
#   - /var/lib/gel/data: data directory bootstrapped at build time
#   - /home/user/gateway: empty project directory (with an empty dbschema/)
#   - /home/user/gateway/data/seed.json: read-only service-registry fixture (3 teams, 9 services)

COPY test_initial_state.py /bootstrap/test_initial_state.py

# Setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
