FROM ubuntu:24.04

ARG POCHI_VERSION

# ---------------------------------------------------------------------------
# Base tooling: python/pytest stack, Node.js 22, Gel 6 server + Gel CLI.
# NOTE: python3-pytest (Debian/Ubuntu) is deliberately NOT installed; the
# distro pytest is too old for pytest-json-ctrf and aborts the whole run.
# ---------------------------------------------------------------------------
RUN apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl wget ca-certificates git gnupg bash ripgrep jq procps \
        python3 python3-pip && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends nodejs && \
    mkdir -p /usr/local/share/keyrings && \
    curl --proto '=https' --tlsv1.2 -sSf \
        -o /usr/local/share/keyrings/gel-keyring.gpg \
        https://packages.geldata.com/keys/gel-keyring.gpg && \
    echo "deb [signed-by=/usr/local/share/keyrings/gel-keyring.gpg] https://packages.geldata.com/apt $(grep VERSION_CODENAME= /etc/os-release | cut -d= -f2) main" \
        > /etc/apt/sources.list.d/gel.list && \
    apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        gel-server-6 gel-cli && \
    curl -fsSL https://getpochi.com/install.sh | bash -s ${POCHI_VERSION} && \
    ln -s /root/.pochi/bin/pochi /usr/local/bin/pochi && \
    pip3 install --break-system-packages pytest pytest-json-ctrf && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN mkdir -p /home/user && chmod 0755 /home/user

# ---------------------------------------------------------------------------
# Unprivileged account for the database process (gel-server refuses to run as
# root) plus its data and runstate directories.
# ---------------------------------------------------------------------------
RUN useradd --system --create-home --home-dir /var/lib/gelsrv --shell /bin/bash gelsrv && \
    mkdir -p /var/lib/gel/data /var/lib/gel/run && \
    chown -R gelsrv:gelsrv /var/lib/gel && \
    touch /var/log/gel-server.log && \
    chown gelsrv:gelsrv /var/log/gel-server.log

# ---------------------------------------------------------------------------
# Idempotent start helper for the local instance.
# ---------------------------------------------------------------------------
RUN cat > /usr/local/bin/gel-start.sh <<'EOF'
#!/usr/bin/env bash
# Idempotently start the local Gel instance used by this task.
# Safe to call repeatedly: it returns as soon as the instance answers queries.
set -uo pipefail

INSTANCE_NAME="local_gel"
DATA_DIR="/var/lib/gel/data"
RUNSTATE_DIR="/var/lib/gel/run"
LOG_FILE="/var/log/gel-server.log"
SERVER_USER="gelsrv"
PORT="5656"

is_ready() {
  gel query -I "${INSTANCE_NAME}" "select 1" >/dev/null 2>&1
}

resolve_server_bin() {
  local candidate fallback
  for candidate in gel-server gel-server-6 gel-server-7; do
    if command -v "${candidate}" >/dev/null 2>&1; then
      command -v "${candidate}"
      return 0
    fi
  done
  fallback="$(ls /usr/bin/gel-server* 2>/dev/null | head -n 1)"
  if [ -n "${fallback}" ]; then
    echo "${fallback}"
    return 0
  fi
  echo "ERROR: no gel-server binary found (tried gel-server, gel-server-6, gel-server-7, /usr/bin/gel-server*)." >&2
  return 1
}

if is_ready; then
  echo "Gel instance '${INSTANCE_NAME}' is already running on port ${PORT}."
  exit 0
fi

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: $0 must be run as root." >&2
  exit 1
fi

SERVER_BIN="$(resolve_server_bin)" || exit 1

mkdir -p "${DATA_DIR}" "${RUNSTATE_DIR}"
chown -R "${SERVER_USER}:${SERVER_USER}" /var/lib/gel
touch "${LOG_FILE}"
chown "${SERVER_USER}:${SERVER_USER}" "${LOG_FILE}"

# gel-server must never fall back to the root-owned default runstate
# directory, so --runstate-dir is always passed explicitly.
SERVER_CMD="${SERVER_BIN} \
  --data-dir=${DATA_DIR} \
  --runstate-dir=${RUNSTATE_DIR} \
  --port=${PORT} \
  --bind-address=127.0.0.1 \
  --security=insecure_dev_mode \
  --compiler-pool-mode=on_demand \
  --compiler-pool-size=2 \
  --max-backend-connections=10 \
  --log-level=warn"

setsid su "${SERVER_USER}" -s /bin/bash -c "${SERVER_CMD}" >>"${LOG_FILE}" 2>&1 &

for _ in $(seq 1 180); do
  if is_ready; then
    echo "Gel instance '${INSTANCE_NAME}' is ready on port ${PORT}."
    exit 0
  fi
  sleep 1
done

echo "ERROR: the Gel instance did not become ready in time. Last log lines:" >&2
tail -n 40 "${LOG_FILE}" >&2
exit 1
EOF

RUN chmod 0755 /usr/local/bin/gel-start.sh

# ---------------------------------------------------------------------------
# Pre-seeded Gel + TypeScript project skeleton.
# ---------------------------------------------------------------------------
RUN mkdir -p /home/user/catalog-sync/dbschema /home/user/catalog-sync/examples

RUN cat > /home/user/catalog-sync/gel.toml <<'EOF'
[instance]
server-version = "6.11"
EOF

RUN cat > /home/user/catalog-sync/dbschema/default.gel <<'EOF'
module default {
  # The supplier catalog is not modelled yet.
}
EOF

RUN cat > /home/user/catalog-sync/package.json <<'EOF'
{
  "name": "catalog-sync",
  "version": "1.0.0",
  "private": true,
  "description": "Supplier catalog synchronisation against the local Gel instance.",
  "scripts": {
    "build": "tsc -p ."
  },
  "dependencies": {
    "gel": "2.2.0"
  },
  "devDependencies": {
    "@types/node": "22.13.1",
    "typescript": "5.7.3"
  }
}
EOF

RUN cat > /home/user/catalog-sync/tsconfig.json <<'EOF'
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "commonjs",
    "moduleResolution": "node",
    "lib": ["ES2022"],
    "types": ["node"],
    "rootDir": "src",
    "outDir": "dist",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true
  },
  "include": ["src/**/*.ts"]
}
EOF

RUN cat > /home/user/catalog-sync/examples/batch-example.json <<'EOF'
[
  {
    "sku": "EXAMPLE-1",
    "name": "Example Widget",
    "price_cents": 1250,
    "stock": 7,
    "category": "examples",
    "tags": ["demo", "sample"]
  },
  {
    "sku": "EXAMPLE-2",
    "name": "Example Gadget",
    "price_cents": 3400,
    "stock": 0,
    "category": "examples"
  }
]
EOF

# Bake the npm dependency tree into the image so the task needs no network.
RUN cd /home/user/catalog-sync && npm install --no-audit --no-fund

# ---------------------------------------------------------------------------
# Bootstrap the database cluster, register it as instance 'local_gel' and link
# the project directory to it, so client libraries and the CLI discover the
# connection without any environment variable. The server is stopped again at
# the end: the task environment starts with the instance down.
# ---------------------------------------------------------------------------
RUN GEL_SERVER_BIN="$(command -v gel-server || command -v gel-server-6 || ls /usr/bin/gel-server* 2>/dev/null | head -n 1)" && \
    echo "using server binary: ${GEL_SERVER_BIN}" && \
    setsid su gelsrv -s /bin/bash -c "${GEL_SERVER_BIN} \
        --data-dir=/var/lib/gel/data \
        --runstate-dir=/var/lib/gel/run \
        --port=5656 \
        --bind-address=127.0.0.1 \
        --security=insecure_dev_mode \
        --compiler-pool-mode=on_demand \
        --compiler-pool-size=2 \
        --max-backend-connections=10 \
        --log-level=warn" >>/var/log/gel-server.log 2>&1 & \
    for _ in $(seq 1 180); do \
        gel query --dsn "gel://admin@127.0.0.1:5656" --tls-security insecure "select 1" >/dev/null 2>&1 && break; \
        sleep 1; \
    done && \
    gel instance link --dsn "gel://admin@127.0.0.1:5656" --non-interactive --trust-tls-cert --overwrite local_gel && \
    cd /home/user/catalog-sync && \
    gel project init --link --server-instance local_gel --non-interactive && \
    gel query -I local_gel "select 1" && \
    gel migration status && \
    pkill -u gelsrv -f gel-server; \
    sleep 5; \
    rm -rf /var/lib/gel/run/* && \
    test -f /root/.config/edgedb/credentials/local_gel.json -o -f /root/.config/gel/credentials/local_gel.json

LABEL Author="generate-harbor-tasks"
LABEL Description="Gel 6 local instance + Gel CLI + Node.js 22 with a pre-seeded, project-linked TypeScript catalog-sync skeleton in /home/user/catalog-sync."

CMD ["/bin/bash", "-c", "trap : TERM INT; sleep infinity & wait"]

# Container contents:
#   - apt packages: curl wget ca-certificates git gnupg bash ripgrep jq procps python3 python3-pip
#     (python3-pytest intentionally omitted; pytest comes from pip)
#   - pip3 packages: pytest, pytest-json-ctrf
#   - Node.js 22 (NodeSource) with npm
#   - Gel 6 server (gel-server-6) and Gel CLI (gel-cli) from packages.geldata.com
#   - Pochi CLI
#   - gelsrv system user owning /var/lib/gel/{data,run}; bootstrapped cluster registered
#     as the 'local_gel' instance (insecure dev mode, Trust auth, port 5656, stopped)
#   - /usr/local/bin/gel-start.sh: idempotent starter for the local instance
#   - /home/user/catalog-sync: Gel project linked to 'local_gel' with gel.toml,
#     dbschema/default.gel (catalog not modelled yet), package.json/tsconfig.json,
#     examples/batch-example.json and a pre-installed node_modules tree
#     (gel@2.2.0, typescript@5.7.3)

COPY test_initial_state.py /bootstrap/test_initial_state.py

# Setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
