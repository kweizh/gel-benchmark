FROM ubuntu:24.04

ARG POCHI_VERSION

ENV DEBIAN_FRONTEND=noninteractive

# --------------------------------------------------------------------------
# Base tooling, Pochi CLI, python test tooling, Node.js 20, Gel 6 server + CLI
# --------------------------------------------------------------------------
RUN apt-get update -y && \
    apt-get install -y --no-install-recommends \
        curl wget ca-certificates gnupg git bash ripgrep procps util-linux \
        tzdata python3 python3-pip && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    curl -fsSL https://getpochi.com/install.sh | bash -s ${POCHI_VERSION} && \
    ln -s /root/.pochi/bin/pochi /usr/local/bin/pochi && \
    pip3 install --break-system-packages "pytest>=8,<9" pytest-json-ctrf && \
    curl -fsSL https://packages.geldata.com/keys/gel.asc | \
        gpg --dearmor -o /usr/share/keyrings/gel-keyring.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/gel-keyring.gpg] https://packages.geldata.com/apt noble main" \
        >/etc/apt/sources.list.d/gel.list && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get update -y && \
    apt-get install -y --no-install-recommends nodejs gel-server-6 gel-cli && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Connection settings shared by the `gel` CLI and the `gel` npm client.
# The local server uses trust authentication with a self-signed certificate.
ENV GEL_DSN=gel://admin@127.0.0.1:5656 \
    GEL_CLIENT_TLS_SECURITY=insecure \
    GEL_BRANCH=main \
    GEL_RUN_VERSION_CHECK=never

RUN mkdir -p /home/user && chmod 0755 /home/user

# --------------------------------------------------------------------------
# Helper scripts: start/stop the local Gel server.
# The server binary name differs between images/versions, so it is resolved
# defensively, and --runstate-dir is always passed explicitly because the
# server runs as the unprivileged `gel` user.
# --------------------------------------------------------------------------
RUN cat >/usr/local/bin/gel-start.sh <<'START_EOF'
#!/bin/bash
# Start the local Gel server unless it is already running, then wait until it
# accepts EdgeQL queries. Safe to call repeatedly.

DATA_DIR=/opt/gel/data
RUNSTATE_DIR=/opt/gel/run
LOG_FILE=/var/log/gel-server.log

probe() {
    gel query --wait-until-available=1s -F json-lines 'select 1' >/dev/null 2>&1
}

if probe; then
    echo "gel server already running"
    exit 0
fi

resolve_server_binary() {
    local candidate
    for candidate in gel-server gel-server-6 gel-server-7; do
        if command -v "$candidate" >/dev/null 2>&1; then
            command -v "$candidate"
            return 0
        fi
    done
    candidate="$(ls /usr/bin/gel-server* 2>/dev/null | head -n 1)"
    if [ -n "$candidate" ]; then
        echo "$candidate"
        return 0
    fi
    candidate="$(ls /usr/lib/*/gel-server-*/bin/gel-server 2>/dev/null | head -n 1)"
    if [ -n "$candidate" ]; then
        echo "$candidate"
        return 0
    fi
    return 1
}

SERVER_BIN="$(resolve_server_binary)"
if [ -z "$SERVER_BIN" ]; then
    echo "ERROR: unable to locate a gel-server binary (looked for gel-server, gel-server-N)" >&2
    exit 1
fi

mkdir -p "$DATA_DIR" "$RUNSTATE_DIR"
touch "$LOG_FILE"
chown -R gel:gel "$DATA_DIR" "$RUNSTATE_DIR" "$LOG_FILE" 2>/dev/null || true

setsid nohup runuser -u gel -- "$SERVER_BIN" \
    --data-dir="$DATA_DIR" \
    --runstate-dir="$RUNSTATE_DIR" \
    --port=5656 \
    --bind-address=127.0.0.1 \
    --default-auth-method=Trust \
    --tls-cert-mode=generate_self_signed \
    --compiler-pool-size=2 \
    >>"$LOG_FILE" 2>&1 </dev/null &

for _ in $(seq 1 240); do
    if probe; then
        echo "gel server ready"
        exit 0
    fi
    sleep 1
done

echo "ERROR: the Gel server did not become ready in time" >&2
tail -n 80 "$LOG_FILE" >&2
exit 1
START_EOF

RUN cat >/usr/local/bin/gel-stop.sh <<'STOP_EOF'
#!/bin/bash
# Stop the local Gel server and wait for a clean shutdown.
pkill -TERM -f 'gel-server' 2>/dev/null || true
for _ in $(seq 1 90); do
    if ! pgrep -f 'gel-server' >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
sleep 2
exit 0
STOP_EOF

RUN chmod 0755 /usr/local/bin/gel-start.sh /usr/local/bin/gel-stop.sh && \
    mkdir -p /opt/gel/data /opt/gel/run && \
    touch /var/log/gel-server.log && \
    chown -R gel:gel /opt/gel /var/log/gel-server.log

# --------------------------------------------------------------------------
# Project skeleton: /home/user/prefsvc
# --------------------------------------------------------------------------
RUN mkdir -p /home/user/prefsvc/dbschema /home/user/prefsvc/src

RUN cat >/home/user/prefsvc/gel.toml <<'TOML_EOF'
[instance]
server-version = "6.11"
TOML_EOF

RUN cat >/home/user/prefsvc/dbschema/default.gel <<'SDL_EOF'
module default {
  type PrefUser {
    required email: str {
      constraint exclusive;
    };
    required preferences: json {
      default := to_json('{}');
    };
  }
}
SDL_EOF

RUN cat >/home/user/prefsvc/package.json <<'PKG_EOF'
{
  "name": "prefsvc",
  "version": "1.0.0",
  "private": true,
  "description": "User preference merge-patch service backed by Gel",
  "scripts": {
    "typecheck": "tsc --noEmit"
  },
  "dependencies": {
    "gel": "2.2.0"
  },
  "devDependencies": {
    "@types/node": "^20.14.0",
    "tsx": "4.23.7",
    "typescript": "5.9.3"
  }
}
PKG_EOF

RUN cat >/home/user/prefsvc/tsconfig.json <<'TSC_EOF'
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022"],
    "module": "commonjs",
    "moduleResolution": "node",
    "types": ["node"],
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true,
    "noEmit": true
  },
  "include": ["src"]
}
TSC_EOF

RUN cat >/home/user/prefsvc/src/prefs.ts <<'PREFS_EOF'
/**
 * Preference service.
 *
 * Nothing is implemented yet: see the task instructions for the exports and
 * the behaviour that are expected here.
 */
export function notImplemented(what: string): never {
  throw new Error("prefsvc: " + what + " is not implemented");
}
PREFS_EOF

RUN cat >/home/user/prefsvc/src/cli.ts <<'CLI_EOF'
import { notImplemented } from "./prefs";

notImplemented("the preference service command line");
CLI_EOF

# Install every npm dependency at build time; evaluation runs fully offline.
RUN cd /home/user/prefsvc && \
    npm install --no-audit --no-fund && \
    npm cache clean --force && \
    ./node_modules/.bin/tsx --version && \
    npm run --silent typecheck

# --------------------------------------------------------------------------
# Bake the database state: apply the first migration and seed nine users.
# --------------------------------------------------------------------------
RUN cat >/tmp/seed.edgeql <<'SEED_EOF'
for item in json_array_unpack(to_json($$
[
  {"email": "ada@example.com",
   "prefs": {"ui": {"theme": "dark", "sidebar": {"width": 320}}, "editor": {"tab_size": 2}}},
  {"email": "linus@example.com",
   "prefs": {"notifications": {"email": {"digest": "weekly"},
                               "push": {"enabled": true, "quiet_hours": [22, 7]}}}},
  {"email": "grace@example.com", "prefs": {}},
  {"email": "alan@example.com",
   "prefs": {"ui": {"density": "compact", "pinned": ["inbox", "drafts"]},
             "privacy": {"analytics": false}}},
  {"email": "edsger@example.com",
   "prefs": {"editor": {"keymap": "vim", "rulers": [100], "soft_wrap": false}}},
  {"email": "barbara@example.com", "prefs": {"ui": {"theme": "solarized"}}},
  {"email": "dennis@example.com",
   "prefs": {"notifications": {"batch_size": 5}, "privacy": {"share_profile": true}}},
  {"email": "hedy@example.com",
   "prefs": {"ui": {"theme": "light", "sidebar": {"visible": false}},
             "notifications": {"batch_size": 10}}},
  {"email": "katherine@example.com", "prefs": {}}
]
$$))
union (
  insert PrefUser {
    email := <str>item['email'],
    preferences := item['prefs']
  }
);
SEED_EOF

RUN /usr/local/bin/gel-start.sh && \
    cd /home/user/prefsvc && \
    gel migration create --non-interactive --schema-dir=/home/user/prefsvc/dbschema && \
    gel migration apply --schema-dir=/home/user/prefsvc/dbschema && \
    gel query --file /tmp/seed.edgeql && \
    test "$(gel query -F json-lines 'select count(PrefUser)')" = "9" && \
    test "$(gel query -F json-lines 'select count(schema::Migration)')" = "1" && \
    ls -1 /home/user/prefsvc/dbschema/migrations/*.edgeql && \
    gel migration status --schema-dir=/home/user/prefsvc/dbschema && \
    rm -f /tmp/seed.edgeql && \
    /usr/local/bin/gel-stop.sh

LABEL Author="generate-harbor-tasks"
LABEL Description="Local Gel 6 server with a seeded PrefUser branch plus an offline TypeScript project (/home/user/prefsvc) for a JSON merge-patch preference service."

CMD ["/bin/bash", "-c", "trap : TERM INT; sleep infinity & wait"]

# Container contents:
#   - always required apt packages: curl wget ca-certificates git python3 python3-pip bash ripgrep
#     (python3-pytest is intentionally NOT installed; a modern pytest comes from pip3)
#   - always required pip3 packages: pytest, pytest-json-ctrf
#   - Pochi CLI installed from https://getpochi.com/install.sh
#   - gel-server-6 (Gel 6.11) and gel-cli from https://packages.geldata.com/apt (noble)
#   - Node.js 20 from deb.nodesource.com for the TypeScript client project
#   - /usr/local/bin/gel-start.sh: resolves the gel-server binary defensively, runs it as the
#     unprivileged `gel` user with an explicit --runstate-dir, and waits for readiness
#   - /usr/local/bin/gel-stop.sh: graceful shutdown used while baking the image
#   - /opt/gel/data: baked data directory (branch `main`, first migration applied, 9 seeded users)
#   - /opt/gel/run: pre-created runstate directory owned by `gel`
#   - /home/user/prefsvc: gel.toml, dbschema/default.gel + dbschema/migrations, package.json,
#     tsconfig.json, unimplemented src/prefs.ts and src/cli.ts stubs
#   - /home/user/prefsvc/node_modules: gel@2.2.0, tsx, typescript, @types/node (installed offline)

COPY test_initial_state.py /bootstrap/test_initial_state.py

# Setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
