FROM ubuntu:24.04

ARG POCHI_VERSION

# ---------------------------------------------------------------------------
# Base tooling: python/pytest, node 20, the Gel CLI and the Gel 6 server.
# NOTE: python3-pytest (apt) is deliberately NOT installed; the distro build is
# too old for pytest-json-ctrf, so pytest comes from pip instead.
# ---------------------------------------------------------------------------
RUN apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl wget ca-certificates gnupg git python3 python3-pip bash ripgrep procps && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends nodejs && \
    curl -fsSL https://getpochi.com/install.sh | bash -s ${POCHI_VERSION} && \
    ln -s /root/.pochi/bin/pochi /usr/local/bin/pochi && \
    pip3 install --break-system-packages pytest pytest-json-ctrf && \
    mkdir -p /usr/local/share/keyrings && \
    curl --proto '=https' --tlsv1.2 -sSf -o /usr/local/share/keyrings/gel-keyring.gpg \
        https://packages.geldata.com/keys/gel-keyring.gpg && \
    echo "deb [signed-by=/usr/local/share/keyrings/gel-keyring.gpg] https://packages.geldata.com/apt noble main" \
        > /etc/apt/sources.list.d/gel.list && \
    apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        gel-cli gel-server-6 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN mkdir -p /home/user && chmod 0755 /home/user

# Connection settings for the local instance (development-only, loopback bound).
ENV GEL_DSN=gel://admin@127.0.0.1:5656/main
ENV GEL_CLIENT_TLS_SECURITY=insecure

# ---------------------------------------------------------------------------
# Unprivileged account that owns the server process, its data dir and its
# runstate dir (gel-server refuses to run as root and the default runstate
# location under /run is root-owned).
# ---------------------------------------------------------------------------
RUN useradd --system --create-home --home-dir /var/lib/gelsrv --shell /bin/bash gelsrv && \
    mkdir -p /var/lib/gelvault/data /var/lib/gelvault/run /opt/vault-baseline && \
    touch /var/log/gel-server.log && \
    chown -R gelsrv:gelsrv /var/lib/gelvault /var/log/gel-server.log

# ---------------------------------------------------------------------------
# Idempotent server launcher used by the executor and by the test fixtures.
# ---------------------------------------------------------------------------
RUN cat > /usr/local/bin/start-gel.sh <<'SCRIPT' && chmod 0755 /usr/local/bin/start-gel.sh
#!/bin/bash
# Start the local Gel server if it is not already answering queries.
# Safe to run repeatedly.
DATA_DIR=/var/lib/gelvault/data
RUN_DIR=/var/lib/gelvault/run
LOG_FILE=/var/log/gel-server.log

if gel query "select 1" >/dev/null 2>&1; then
    exit 0
fi

SERVER_BIN=""
for candidate in gel-server gel-server-6; do
    if command -v "$candidate" >/dev/null 2>&1; then
        SERVER_BIN="$(command -v "$candidate")"
        break
    fi
done
if [ -z "$SERVER_BIN" ]; then
    SERVER_BIN="$(ls /usr/bin/gel-server* 2>/dev/null | head -n 1)"
fi
if [ -z "$SERVER_BIN" ]; then
    echo "start-gel.sh: no gel-server binary found (looked for gel-server, gel-server-6, /usr/bin/gel-server*)" >&2
    exit 1
fi

mkdir -p "$RUN_DIR" "$DATA_DIR"
touch "$LOG_FILE"
if [ "$(id -u)" = "0" ]; then
    chown -R gelsrv:gelsrv "$RUN_DIR" "$DATA_DIR" "$LOG_FILE"
    su gelsrv -s /bin/bash -c "nohup $SERVER_BIN --data-dir=$DATA_DIR --runstate-dir=$RUN_DIR --bind-address=127.0.0.1 --port=5656 --tls-cert-mode=generate_self_signed --security=insecure_dev_mode --admin-ui=disabled --default-branch=main >>$LOG_FILE 2>&1 &"
else
    nohup "$SERVER_BIN" --data-dir="$DATA_DIR" --runstate-dir="$RUN_DIR" \
        --bind-address=127.0.0.1 --port=5656 --tls-cert-mode=generate_self_signed \
        --security=insecure_dev_mode --admin-ui=disabled --default-branch=main \
        >>"$LOG_FILE" 2>&1 &
fi

for _ in $(seq 1 180); do
    if gel query "select 1" >/dev/null 2>&1; then
        exit 0
    fi
    sleep 1
done

echo "start-gel.sh: the Gel server did not become ready in time" >&2
tail -n 50 "$LOG_FILE" >&2
exit 1
SCRIPT

# ---------------------------------------------------------------------------
# The unsolved Vault project: gel.toml, baseline schema (no globals, no access
# policies), package.json without a build script, and no TypeScript sources.
# ---------------------------------------------------------------------------
RUN mkdir -p /home/user/vault/dbschema && cat > /home/user/vault/gel.toml <<'TOML'
[instance]
server-version = "6.11"
TOML

RUN cat > /home/user/vault/dbschema/default.gel <<'SDL'
module default {
  scalar type Role extending enum<Owner, Editor, Viewer>;

  type Actor {
    required email: str {
      constraint exclusive;
    }
    required display_name: str;
  }

  type Workspace {
    required name: str {
      constraint exclusive;
    }
  }

  type Membership {
    required actor: Actor;
    required workspace: Workspace;
    required role: Role;
    constraint exclusive on ((.actor, .workspace));
  }

  type Document {
    required title: str {
      constraint exclusive;
    }
    required body: str;
    required workspace: Workspace;
    required owner: Actor;
    required archived: bool {
      default := false;
    }
  }

  type ActivityLog {
    required action: str;
    required actor_email: str;
    required doc_title: str;
    required at: datetime {
      default := datetime_of_statement();
    }
  }
}
SDL

RUN cat > /home/user/vault/package.json <<'JSON'
{
  "name": "vault",
  "version": "1.0.0",
  "private": true,
  "description": "Vault document collaboration service",
  "scripts": {},
  "dependencies": {
    "gel": "2.2.0"
  },
  "devDependencies": {
    "@types/node": "20.17.24",
    "typescript": "5.7.3"
  }
}
JSON

RUN cat > /home/user/vault/README.md <<'MD'
# Vault

A small document-collaboration service backed by a local Gel instance.

* `dbschema/default.gel` – the schema (already migrated).
* `dbschema/migrations/` – the migration history.
* `/usr/local/bin/start-gel.sh` – starts the local Gel server if it is not running.
MD

# Bake the npm dependencies into the image so no network access is needed later.
RUN cd /home/user/vault && npm install --no-audit --no-fund

# ---------------------------------------------------------------------------
# Bootstrap the branch: apply the baseline migration and seed the fixtures.
# ---------------------------------------------------------------------------
RUN cat > /tmp/vault-seed.edgeql <<'SEED'
insert Workspace { name := "alpha" };
insert Workspace { name := "beta" };
insert Workspace { name := "gamma" };
insert Workspace { name := "delta" };
insert Actor { email := "ana@vault.test", display_name := "Ana Ortiz" };
insert Actor { email := "evan@vault.test", display_name := "Evan Reed" };
insert Actor { email := "vera@vault.test", display_name := "Vera Cruz" };
insert Actor { email := "bruno@vault.test", display_name := "Bruno Lima" };
insert Actor { email := "nomad@vault.test", display_name := "Nina Nomad" };
insert Membership {
  actor := assert_single((select Actor filter .email = "ana@vault.test")),
  workspace := assert_single((select Workspace filter .name = "alpha")),
  role := Role.Owner
};
insert Membership {
  actor := assert_single((select Actor filter .email = "evan@vault.test")),
  workspace := assert_single((select Workspace filter .name = "alpha")),
  role := Role.Editor
};
insert Membership {
  actor := assert_single((select Actor filter .email = "vera@vault.test")),
  workspace := assert_single((select Workspace filter .name = "alpha")),
  role := Role.Viewer
};
insert Membership {
  actor := assert_single((select Actor filter .email = "bruno@vault.test")),
  workspace := assert_single((select Workspace filter .name = "beta")),
  role := Role.Owner
};
insert Membership {
  actor := assert_single((select Actor filter .email = "ana@vault.test")),
  workspace := assert_single((select Workspace filter .name = "gamma")),
  role := Role.Editor
};
insert Membership {
  actor := assert_single((select Actor filter .email = "evan@vault.test")),
  workspace := assert_single((select Workspace filter .name = "delta")),
  role := Role.Viewer
};
insert Document {
  title := "alpha-roadmap",
  body := "Roadmap v1",
  workspace := assert_single((select Workspace filter .name = "alpha")),
  owner := assert_single((select Actor filter .email = "ana@vault.test"))
};
insert Document {
  title := "alpha-notes",
  body := "Notes v1",
  workspace := assert_single((select Workspace filter .name = "alpha")),
  owner := assert_single((select Actor filter .email = "evan@vault.test"))
};
insert Document {
  title := "alpha-archive-2019",
  body := "Archived plans",
  workspace := assert_single((select Workspace filter .name = "alpha")),
  owner := assert_single((select Actor filter .email = "ana@vault.test")),
  archived := true
};
insert Document {
  title := "beta-charter",
  body := "Charter v1",
  workspace := assert_single((select Workspace filter .name = "beta")),
  owner := assert_single((select Actor filter .email = "bruno@vault.test"))
};
SEED

RUN cat > /tmp/vault-bootstrap.sh <<'BOOT' && chmod 0755 /tmp/vault-bootstrap.sh && /tmp/vault-bootstrap.sh && rm -f /tmp/vault-bootstrap.sh /tmp/vault-seed.edgeql
#!/bin/bash
set -euo pipefail
/usr/local/bin/start-gel.sh
cd /home/user/vault
gel migration create --non-interactive
gel migrate
gel query -f /tmp/vault-seed.edgeql > /dev/null
# Migration filenames are content-hashed, so resolve the baseline one by glob.
baseline="$(ls /home/user/vault/dbschema/migrations/00001-*.edgeql | head -n 1)"
cp "$baseline" /opt/vault-baseline/00001.edgeql
gel query "select { docs := count(Document), actors := count(Actor), workspaces := count(Workspace), memberships := count(Membership) }"
pkill -u gelsrv -f gel-server || true
sleep 5
BOOT

LABEL Author="generate-harbor-tasks"
LABEL Description="Gel 6.11 + Node.js 20 environment with an unprotected, pre-seeded Vault project for building role-aware access policies driven from a TypeScript CLI."

CMD ["/bin/bash", "-c", "trap : TERM INT; sleep infinity & wait"]

# Container contents:
#   - apt packages: curl wget ca-certificates gnupg git python3 python3-pip bash ripgrep procps nodejs (v20)
#   - pip3 packages: pytest, pytest-json-ctrf
#   - Gel: gel-cli + gel-server-6 (6.11) from packages.geldata.com, data dir /var/lib/gelvault/data
#   - /usr/local/bin/start-gel.sh: idempotent launcher (explicit --runstate-dir, runs as the gelsrv user)
#   - /home/user/vault: unsolved project (gel.toml, dbschema/default.gel without policies or globals,
#     dbschema/migrations/00001-*.edgeql applied, package.json with no build script, baked node_modules)
#   - /opt/vault-baseline/00001.edgeql: pristine copy of the baseline migration
#   - seeded branch "main": 4 workspaces, 5 actors, 6 memberships, 4 documents, 0 activity log rows

COPY test_initial_state.py /bootstrap/test_initial_state.py

# Setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
