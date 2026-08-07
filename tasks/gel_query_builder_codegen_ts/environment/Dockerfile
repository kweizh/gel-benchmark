FROM ubuntu:24.04

ARG POCHI_VERSION

ENV DEBIAN_FRONTEND=noninteractive

# --- Base tooling -----------------------------------------------------------
RUN apt-get update -y && \
    apt-get install -y --no-install-recommends \
        curl wget ca-certificates git python3 python3-pip bash ripgrep \
        gnupg jq procps xz-utils sudo && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    curl -fsSL https://getpochi.com/install.sh | bash -s ${POCHI_VERSION} && \
    ln -s /root/.pochi/bin/pochi /usr/local/bin/pochi && \
    pip3 install --break-system-packages --no-cache-dir pytest pytest-json-ctrf && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# --- Node.js 20 -------------------------------------------------------------
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# --- Gel 6 server + CLI -----------------------------------------------------
RUN mkdir -p /usr/local/share/keyrings && \
    curl --proto '=https' --tlsv1.2 -sSf \
        -o /usr/local/share/keyrings/gel-keyring.gpg \
        https://packages.geldata.com/keys/gel-keyring.gpg && \
    echo "deb [signed-by=/usr/local/share/keyrings/gel-keyring.gpg] https://packages.geldata.com/apt $(grep 'VERSION_CODENAME=' /etc/os-release | cut -d= -f2) main" \
        > /etc/apt/sources.list.d/gel.list && \
    apt-get update -y && \
    apt-get install -y --no-install-recommends gel-6 gel-cli && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# --- Connection settings for the local instance -----------------------------
ENV GEL_HOST=127.0.0.1 \
    GEL_PORT=5656 \
    GEL_USER=admin \
    GEL_BRANCH=main \
    GEL_CLIENT_TLS_SECURITY=insecure

# --- Idempotent starter for the local Gel instance --------------------------
RUN cat >/usr/local/bin/gel-start.sh <<'EOF'
#!/bin/bash
# Starts the local Gel instance if it is not already answering queries.
# Safe to run any number of times.
export GEL_HOST="${GEL_HOST:-127.0.0.1}"
export GEL_PORT="${GEL_PORT:-5656}"
export GEL_USER="${GEL_USER:-admin}"
export GEL_BRANCH="${GEL_BRANCH:-main}"
export GEL_CLIENT_TLS_SECURITY="${GEL_CLIENT_TLS_SECURITY:-insecure}"

DATA_DIR=/var/lib/gel/data
RUN_DIR=/var/lib/gel/run
LOG_FILE=/var/log/gel-server.log

ready() {
    gel query "select 1" >/dev/null 2>&1
}

if ready; then
    exit 0
fi

SERVER_BIN=""
for candidate in gel-server gel-server-6 gel-server-7; do
    if command -v "$candidate" >/dev/null 2>&1; then
        SERVER_BIN="$(command -v "$candidate")"
        break
    fi
done
if [ -z "$SERVER_BIN" ]; then
    SERVER_BIN="$(ls /usr/bin/gel-server* 2>/dev/null | head -n 1)"
fi
if [ -z "$SERVER_BIN" ]; then
    SERVER_BIN="$(ls /usr/lib/*/gel-server-*/bin/gel-server 2>/dev/null | head -n 1)"
fi
if [ -z "$SERVER_BIN" ]; then
    echo "gel-start.sh: could not locate a gel-server binary" >&2
    exit 1
fi

mkdir -p "$RUN_DIR"
chown gel:gel "$RUN_DIR"
touch "$LOG_FILE"

su gel -s /bin/bash -c "exec '$SERVER_BIN' --data-dir='$DATA_DIR' --runstate-dir='$RUN_DIR' --bind-address=127.0.0.1 --port=5656 --security=insecure_dev_mode --default-auth-method=Trust --tls-cert-mode=generate_self_signed" >>"$LOG_FILE" 2>&1 &

for _ in $(seq 1 180); do
    if ready; then
        exit 0
    fi
    sleep 1
done

echo "gel-start.sh: the Gel server did not become ready in time" >&2
tail -n 60 "$LOG_FILE" >&2
exit 1
EOF

RUN chmod 0755 /usr/local/bin/gel-start.sh

# --- Project skeleton -------------------------------------------------------
RUN mkdir -p /home/user && chmod 0755 /home/user
RUN mkdir -p /home/user/catalog/dbschema /home/user/catalog/src /home/user/catalog/data

RUN cat >/home/user/catalog/gel.toml <<'EOF'
[instance]
server-version = "6"
EOF

RUN cat >/home/user/catalog/dbschema/default.gel <<'EOF'
module default {
  type Author {
    required name: str {
      constraint exclusive;
    }
    required country: str;
  }
}
EOF

RUN cat >/home/user/catalog/package.json <<'EOF'
{
  "name": "catalog",
  "private": true,
  "version": "1.0.0",
  "type": "commonjs",
  "dependencies": {
    "gel": "2.2.0"
  },
  "devDependencies": {
    "@gel/generate": "0.7.1",
    "@types/node": "20.19.43",
    "tsx": "4.23.8",
    "typescript": "5.9.3"
  }
}
EOF

RUN cat >/home/user/catalog/tsconfig.json <<'EOF'
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "commonjs",
    "moduleResolution": "node",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "noEmit": true,
    "forceConsistentCasingInFileNames": true,
    "types": ["node"]
  },
  "include": ["src/**/*.ts"]
}
EOF

RUN cat >/home/user/catalog/src/cli.ts <<'EOF'
// Catalog analytics CLI - not implemented yet.
process.stderr.write("catalog: no subcommand implemented\n");
process.exit(2);
EOF

RUN cat >/home/user/catalog/data/resources.json <<'EOF'
[
  { "kind": "article", "title": "Analytical Engines", "author": "Ada Lovelace", "minutes": 13, "level": "beginner", "word_count": 2400 },
  { "kind": "article", "title": "Bug Hunting", "author": "Grace Hopper", "minutes": 20, "level": "intermediate", "word_count": 5200 },
  { "kind": "article", "title": "Compilers Explained", "author": "Grace Hopper", "minutes": 35, "level": "advanced", "word_count": 9100 },
  { "kind": "article", "title": "Kernel Modules", "author": "Linus Torvalds", "minutes": 45, "level": "advanced", "word_count": 7300 },
  { "kind": "video", "title": "Punch Cards 101", "author": "Ada Lovelace", "minutes": 18, "level": "beginner", "has_captions": true },
  { "kind": "video", "title": "Cobol In Practice", "author": "Grace Hopper", "minutes": 50, "level": "intermediate", "has_captions": false },
  { "kind": "video", "title": "Git Internals", "author": "Linus Torvalds", "minutes": 65, "level": "advanced", "has_captions": true },
  { "kind": "video", "title": "Debugging Deep Dive", "author": "Ada Lovelace", "minutes": 30, "level": "intermediate", "has_captions": true },
  { "kind": "video", "title": "Type Systems Tour", "author": "Rita O'Malley", "minutes": 40, "level": "beginner", "has_captions": false }
]
EOF

RUN cat >/tmp/seed_authors.edgeql <<'EOF'
insert Author { name := "Ada Lovelace", country := "UK" };
insert Author { name := "Grace Hopper", country := "USA" };
insert Author { name := "Linus Torvalds", country := "Finland" };
insert Author { name := "Radia Perlman", country := "USA" };
insert Author { name := "Rita O'Malley", country := "Ireland" };
EOF

# --- Offline npm dependencies ----------------------------------------------
RUN cd /home/user/catalog && \
    npm install --no-audit --no-fund && \
    test -f node_modules/gel/package.json && \
    test -f node_modules/@gel/generate/package.json && \
    test -f node_modules/tsx/package.json && \
    test -f node_modules/typescript/package.json

# --- Bootstrap the local instance, apply the seed migration, seed authors and
# --- pre-generate the (Author-only) query builder ---------------------------
RUN id -u gel >/dev/null 2>&1 || useradd --system --home-dir /var/lib/gel --shell /usr/sbin/nologin gel; \
    mkdir -p /var/lib/gel/data /var/lib/gel/run && \
    chown -R gel:gel /var/lib/gel && \
    SERVER_BIN="$(command -v gel-server || command -v gel-server-6 || ls /usr/bin/gel-server* 2>/dev/null | head -n 1)" && \
    test -n "$SERVER_BIN" && \
    su gel -s /bin/bash -c "'$SERVER_BIN' --data-dir=/var/lib/gel/data --runstate-dir=/var/lib/gel/run --bootstrap-only --default-branch=main --security=insecure_dev_mode --default-auth-method=Trust --tls-cert-mode=generate_self_signed" && \
    /usr/local/bin/gel-start.sh && \
    cd /home/user/catalog && \
    gel migration create --non-interactive && \
    gel migrate && \
    gel query -f /tmp/seed_authors.edgeql && \
    npx @gel/generate edgeql-js --force-overwrite && \
    test -f dbschema/edgeql-js/index.ts && \
    ls dbschema/migrations/*.edgeql && \
    rm -f /tmp/seed_authors.edgeql && \
    pkill -u gel -f gel-server || true

LABEL Author="generate-harbor-tasks"
LABEL Description="Gel 6 local instance plus a strict-TypeScript Node project (/home/user/catalog) with pinned gel, @gel/generate, tsx and typescript packages pre-installed for query-builder code generation tasks."

CMD ["/bin/bash", "-c", "trap : TERM INT; sleep infinity & wait"]

# Container contents:
#   - always required apt packages: curl wget ca-certificates git python3 python3-pip bash ripgrep
#     (python3-pytest is deliberately NOT installed; pytest comes from pip3 so that
#      pytest-json-ctrf stays compatible)
#   - always required pip3 packages: pytest, pytest-json-ctrf
#   - Node.js 20 (NodeSource) for the TypeScript project
#   - Gel 6 server + CLI from the official apt repository, bootstrapped at
#     /var/lib/gel/data (branch "main", Trust auth, insecure dev mode on 127.0.0.1:5656)
#   - /usr/local/bin/gel-start.sh: idempotent server starter with an explicit,
#     pre-chowned --runstate-dir
#   - /home/user/catalog: Node + TypeScript project (gel.toml, dbschema/default.gel with
#     only the Author type, one applied migration, data/resources.json catalog input,
#     an unimplemented src/cli.ts stub, tsconfig.json with strict mode, node_modules)
#   - /home/user/catalog/dbschema/edgeql-js: query builder generated from the seeded
#     Author-only schema (must be regenerated by the executor)
#   - 5 seeded Author objects and no catalog resources

COPY test_initial_state.py /bootstrap/test_initial_state.py

# Setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
