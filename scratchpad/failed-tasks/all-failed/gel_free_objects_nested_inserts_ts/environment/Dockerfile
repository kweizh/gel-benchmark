FROM ubuntu:24.04

ARG POCHI_VERSION

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

RUN apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl wget ca-certificates git python3 python3-pip bash ripgrep \
        gnupg jq procps util-linux xz-utils && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
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
    pip3 install --break-system-packages --no-cache-dir "pytest>=8" pytest-json-ctrf && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

LABEL Author="generate-harbor-tasks"
LABEL Description="Local Gel 6 server plus a Node.js 22 / TypeScript project skeleton for building a transactional catalog ingest CLI."

# Connection settings for both the `gel` CLI and the `gel` npm client.
ENV GEL_CREDENTIALS_FILE=/etc/gel/credentials.json \
    GEL_CLIENT_SECURITY=insecure_dev_mode \
    GEL_RUN_VERSION_CHECK=never \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_FUND=false \
    NPM_CONFIG_AUDIT=false

# Idempotent launcher for the local Gel server. `gel-server` refuses to run as
# root, and the runstate directory must be writable by the server user, so both
# are set explicitly here. The server binary name differs between releases, so
# it is resolved defensively.
RUN mkdir -p /usr/local/bin && cat > /usr/local/bin/start-gel.sh <<'EOS'
#!/bin/bash
# Starts the local Gel server if it is not already accepting connections.
if gel query "select 1" >/dev/null 2>&1; then
  exit 0
fi

SRV=""
for candidate in gel-server gel-server-6; do
  if command -v "$candidate" >/dev/null 2>&1; then
    SRV="$(command -v "$candidate")"
    break
  fi
done
if [ -z "$SRV" ]; then
  SRV="$(ls /usr/bin/gel-server* 2>/dev/null | head -n 1)"
fi
if [ -z "$SRV" ]; then
  echo "start-gel.sh: no gel-server binary found on this system" >&2
  exit 1
fi

mkdir -p /var/lib/gel-task/data /var/lib/gel-task/run
chown -R gel:gel /var/lib/gel-task

nohup setpriv --reuid=gel --regid=gel --init-groups "$SRV" \
  --data-dir=/var/lib/gel-task/data \
  --runstate-dir=/var/lib/gel-task/run \
  --security=insecure_dev_mode \
  --tls-cert-mode=generate_self_signed \
  --compiler-pool-size=1 \
  --port=5656 \
  --bind-address=127.0.0.1 >>/var/log/gel-server.log 2>&1 &

for _ in $(seq 1 180); do
  if gel query "select 1" >/dev/null 2>&1; then
    echo "gel server ready"
    exit 0
  fi
  sleep 1
done

echo "start-gel.sh: the Gel server did not become ready in time" >&2
tail -n 50 /var/log/gel-server.log >&2
exit 1
EOS

RUN chmod +x /usr/local/bin/start-gel.sh

# Bootstrap the database cluster at build time so the server starts fast and
# always has the same admin credentials.
RUN mkdir -p /var/lib/gel-task/data /var/lib/gel-task/run /etc/gel && \
    chown -R gel:gel /var/lib/gel-task && \
    setpriv --reuid=gel --regid=gel --init-groups /usr/bin/gel-server-6 \
        --data-dir=/var/lib/gel-task/data \
        --runstate-dir=/var/lib/gel-task/run \
        --security=insecure_dev_mode \
        --tls-cert-mode=generate_self_signed \
        --compiler-pool-size=1 \
        --bootstrap-command="alter role admin set password := 'secret'" \
        --bootstrap-only && \
    printf '%s\n' '{"host":"127.0.0.1","port":5656,"user":"admin","password":"secret","branch":"main","database":"main","tls_security":"insecure"}' \
        > /etc/gel/credentials.json && \
    chmod 0644 /etc/gel/credentials.json

RUN mkdir -p /home/user && chmod 0755 /home/user

RUN mkdir -p /home/user/catalog-ingest/src /home/user/catalog-ingest/dbschema

RUN cat > /home/user/catalog-ingest/gel.toml <<'EOF'
[instance]
server-version = "6"
EOF

RUN cat > /home/user/catalog-ingest/package.json <<'EOF'
{
  "name": "catalog-ingest",
  "private": true,
  "version": "1.0.0",
  "type": "module",
  "dependencies": {
    "gel": "2.2.0"
  },
  "devDependencies": {
    "@types/node": "22.20.1",
    "tsx": "4.23.9",
    "typescript": "5.9.3"
  }
}
EOF

RUN cat > /home/user/catalog-ingest/tsconfig.json <<'EOF'
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ES2022",
    "moduleResolution": "bundler",
    "strict": true,
    "skipLibCheck": true,
    "esModuleInterop": true,
    "types": ["node"]
  },
  "include": ["src/**/*.ts"]
}
EOF

RUN cat > /home/user/catalog-ingest/README.md <<'EOF'
# catalog-ingest

Node.js 22 + TypeScript project skeleton wired to the local Gel 6 server.

* Start the database (idempotent): `/usr/local/bin/start-gel.sh`
* Query it from the shell: `gel query -F json 'select 1'`
* Dependencies (`gel`, `tsx`, `typescript`, `@types/node`) are already installed
  in `node_modules`; the container has no network access.
EOF

RUN cd /home/user/catalog-ingest && npm install --no-audit --no-fund --loglevel=error

CMD ["/bin/bash", "-c", "trap : TERM INT; sleep infinity & wait"]

# Container contents:
#   - always required apt packages: curl wget ca-certificates git python3 python3-pip bash ripgrep
#     (python3-pytest is intentionally NOT installed: the distro pytest is too old for pytest-json-ctrf)
#   - always required pip3 packages: pytest (>=8), pytest-json-ctrf
#   - Pochi CLI installed from https://getpochi.com/install.sh
#   - Node.js 22 (NodeSource) with npm
#   - gel-6 apt package: `gel` CLI + `gel-server-6` local server binary
#   - /var/lib/gel-task/data: pre-bootstrapped Gel data directory (admin/secret, branch `main`)
#   - /etc/gel/credentials.json + GEL_CREDENTIALS_FILE: connection info for CLI and npm client
#   - /usr/local/bin/start-gel.sh: idempotent local server launcher
#   - /home/user/catalog-ingest: project skeleton (gel.toml, package.json, tsconfig.json,
#     README.md, empty src/ and dbschema/, node_modules with gel + tsx + typescript)

COPY test_initial_state.py /bootstrap/test_initial_state.py

# Setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
