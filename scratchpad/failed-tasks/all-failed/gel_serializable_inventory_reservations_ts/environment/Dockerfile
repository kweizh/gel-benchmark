FROM ubuntu:24.04

ARG POCHI_VERSION

ENV DEBIAN_FRONTEND=noninteractive
ENV NPM_CONFIG_UPDATE_NOTIFIER=false
ENV NPM_CONFIG_FUND=false

# ---------------------------------------------------------------------------
# Base tooling: python, node 22, the Gel 6 server + CLI, and the Pochi CLI.
# ---------------------------------------------------------------------------
RUN apt-get update -y && \
    apt-get install -y --no-install-recommends \
        curl wget ca-certificates git python3 python3-pip python3-pytest bash ripgrep \
        gnupg procps util-linux tzdata && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    mkdir -p /usr/local/share/keyrings && \
    curl --proto '=https' --tlsv1.2 -sSf \
        -o /usr/local/share/keyrings/gel-keyring.gpg \
        https://packages.geldata.com/keys/gel-keyring.gpg && \
    echo "deb [signed-by=/usr/local/share/keyrings/gel-keyring.gpg] https://packages.geldata.com/apt noble main" \
        > /etc/apt/sources.list.d/gel.list && \
    apt-get update -y && \
    apt-get install -y --no-install-recommends gel-6 && \
    curl -fsSL https://getpochi.com/install.sh | bash -s ${POCHI_VERSION} && \
    ln -s /root/.pochi/bin/pochi /usr/local/bin/pochi && \
    pip3 install --break-system-packages pytest-json-ctrf && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Bootstrap a local Gel 6 instance at build time so that no network access is
# needed later. `gel-server` refuses to run as root, hence the `gel` system
# user created by the package.
# ---------------------------------------------------------------------------
RUN mkdir -p /var/lib/gel/data /var/run/gel /var/log/gel && \
    chown -R gel:gel /var/lib/gel /var/run/gel /var/log/gel && \
    setpriv --reuid=gel --regid=gel --init-groups \
        /usr/bin/gel-server-6 \
        --data-dir=/var/lib/gel/data \
        --runstate-dir=/var/run/gel \
        --bootstrap-only \
        --security=insecure_dev_mode \
        --default-auth-method=Trust \
        --tls-cert-mode=generate_self_signed \
        > /var/log/gel/bootstrap.log 2>&1

RUN cat >/usr/local/bin/start-gel <<'EOF'
#!/bin/bash
# Idempotently start the local Gel 6 instance and block until it answers.
DATA_DIR=/var/lib/gel/data
RUNSTATE_DIR=/var/run/gel
LOG_FILE=/var/log/gel/server.log

is_ready() {
    curl -fsSk -o /dev/null "https://127.0.0.1:5656/server/status/ready" 2>/dev/null
}

if is_ready; then
    exit 0
fi

mkdir -p "$RUNSTATE_DIR" /var/log/gel
chown -R gel:gel "$DATA_DIR" "$RUNSTATE_DIR" /var/log/gel 2>/dev/null

if ! pgrep -f gel-server >/dev/null 2>&1; then
    setpriv --reuid=gel --regid=gel --init-groups \
        /usr/bin/gel-server-6 \
        --data-dir="$DATA_DIR" \
        --runstate-dir="$RUNSTATE_DIR" \
        --port=5656 \
        --bind-address=127.0.0.1 \
        --security=insecure_dev_mode \
        --tls-cert-mode=generate_self_signed \
        --admin-ui=disabled \
        >>"$LOG_FILE" 2>&1 &
fi

for _ in $(seq 1 180); do
    if is_ready; then
        exit 0
    fi
    sleep 1
done

echo "gel-server did not become ready in time; last log lines:" >&2
tail -n 40 "$LOG_FILE" >&2
exit 1
EOF

RUN chmod 0755 /usr/local/bin/start-gel

# Connection settings for both the `gel` CLI and the `gel` npm client.
ENV GEL_DSN="gel://admin@127.0.0.1:5656/main"
ENV GEL_CLIENT_TLS_SECURITY="insecure"

RUN mkdir -p /home/user && chmod 0755 /home/user

# ---------------------------------------------------------------------------
# Pre-created, fully vendored TypeScript project skeleton.
# ---------------------------------------------------------------------------
RUN mkdir -p /home/user/inventory/src /home/user/inventory/dbschema

RUN cat >/home/user/inventory/gel.toml <<'EOF'
[instance]
server-version = "6"
EOF

RUN cat >/home/user/inventory/dbschema/default.gel <<'EOF'
module default {
}
EOF

RUN cat >/home/user/inventory/package.json <<'EOF'
{
  "name": "inventory",
  "version": "1.0.0",
  "private": true,
  "description": "Concurrency-safe inventory reservation service backed by Gel",
  "dependencies": {
    "gel": "2.2.0"
  },
  "devDependencies": {
    "@types/node": "22.18.8",
    "tsx": "4.20.6",
    "typescript": "5.9.3"
  }
}
EOF

RUN cat >/home/user/inventory/tsconfig.json <<'EOF'
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022"],
    "module": "CommonJS",
    "moduleResolution": "node",
    "types": ["node"],
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true,
    "resolveJsonModule": true,
    "rootDir": "src",
    "outDir": "dist"
  },
  "include": ["src/**/*.ts"]
}
EOF

RUN cat >/home/user/inventory/.gitignore <<'EOF'
node_modules/
dist/
EOF

# Vendor every npm dependency now; the container is offline afterwards.
RUN cd /home/user/inventory && npm install --no-audit --no-fund && npm cache clean --force

LABEL Author="generate-harbor-tasks"
LABEL Description="Offline Gel 6 instance plus a vendored Node.js 22 / TypeScript project skeleton at /home/user/inventory for building a concurrency-safe inventory reservation service."

CMD ["/bin/bash", "-c", "trap : TERM INT; sleep infinity & wait"]

# Container contents:
#   - always required apt packages: curl wget ca-certificates git python3 python3-pip python3-pytest bash ripgrep
#   - extra apt packages: gnupg procps util-linux tzdata (Gel repo key handling + `start-gel` helper)
#   - always required pip3 packages: pytest-json-ctrf
#   - Node.js 22 (NodeSource) for the TypeScript service
#   - Gel 6 server (`gel-server-6`) and Gel CLI (`gel`) from packages.geldata.com,
#     bootstrapped at build time into /var/lib/gel/data (branch `main`, role `admin`, Trust auth)
#   - /usr/local/bin/start-gel: idempotent helper that starts the local instance and waits for
#     readiness; the container entrypoint runs it so the instance is up before the CMD
#   - GEL_DSN / GEL_CLIENT_TLS_SECURITY point both the CLI and the npm client at the local instance
#   - /home/user/inventory: Gel project skeleton (gel.toml, empty dbschema/default.gel, src/)
#     with package.json/tsconfig.json and a fully vendored node_modules (gel 2.2.0, tsx 4.20.6,
#     typescript 5.9.3, @types/node 22.18.8) so no network access is required

COPY test_initial_state.py /bootstrap/test_initial_state.py

# Setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
