FROM ubuntu:24.04

ARG POCHI_VERSION

# --- Base tooling -----------------------------------------------------------
RUN apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl wget ca-certificates gnupg git bash ripgrep procps locales \
        python3 python3-pip && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    curl -fsSL https://getpochi.com/install.sh | bash -s ${POCHI_VERSION} && \
    ln -s /root/.pochi/bin/pochi /usr/local/bin/pochi && \
    pip3 install --break-system-packages "pytest>=8" pytest-json-ctrf gel && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# --- Gel 6 server + CLI (apt packages from packages.geldata.com) ------------
# policy-rc.d keeps package post-install scripts from trying to start services,
# which would fail in a container without an init system.
RUN printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d && \
    chmod 0755 /usr/sbin/policy-rc.d && \
    mkdir -p /usr/local/share/keyrings && \
    curl --proto '=https' --tlsv1.2 -sSf \
        -o /usr/local/share/keyrings/gel-keyring.gpg \
        https://packages.geldata.com/keys/gel-keyring.gpg && \
    echo "deb [signed-by=/usr/local/share/keyrings/gel-keyring.gpg] https://packages.geldata.com/apt $(grep 'VERSION_CODENAME=' /etc/os-release | cut -d= -f2) main" \
        > /etc/apt/sources.list.d/gel.list && \
    apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        gel-6 gel-cli && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# gel-server refuses to run as root, so give it a dedicated unprivileged user
# and pre-create the data / runstate / log directories it owns.
RUN id -u gelsrv >/dev/null 2>&1 || \
        useradd --system --no-create-home --home-dir /var/lib/gel --shell /bin/bash gelsrv && \
    mkdir -p /var/lib/gel/data /var/lib/gel/run /var/log/gel && \
    chown -R gelsrv:gelsrv /var/lib/gel /var/log/gel && \
    chmod 0755 /var/lib/gel /var/lib/gel/data /var/lib/gel/run /var/log/gel

ENV LANG=C.UTF-8
# Local, credential-free development instance: the server trusts local
# connections and uses a generated self-signed certificate.
ENV GEL_DSN=gel://admin@127.0.0.1:5656/main
ENV GEL_CLIENT_SECURITY=insecure_dev_mode
# No network at evaluation time: never let the CLI phone home for version checks.
ENV GEL_RUN_VERSION_CHECK=never

RUN mkdir -p /home/user && chmod 0755 /home/user

RUN mkdir -p /home/user/mtsaas/dbschema/migrations /home/user/mtsaas/app /opt/seed && \
    touch /home/user/mtsaas/app/__init__.py

# --- Helper: idempotently start the local Gel server ------------------------
RUN cat >/usr/local/bin/gel-start.sh <<'EOF'
#!/bin/bash
# Start the local Gel server if it is not already accepting connections.
# Safe to run repeatedly; returns only once queries succeed.
cd / || exit 1
DATA_DIR=/var/lib/gel/data
RUN_DIR=/var/lib/gel/run
LOG_DIR=/var/log/gel
LOG_FILE="$LOG_DIR/server.log"

if gel query -F json 'select 1' >/dev/null 2>&1; then
  echo "gel server is already accepting connections"
  exit 0
fi

mkdir -p "$DATA_DIR" "$RUN_DIR" "$LOG_DIR"
chown -R gelsrv:gelsrv "$DATA_DIR" "$RUN_DIR" "$LOG_DIR"

# Never assume a fixed server binary name: it varies by install method and
# major version.
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
  SERVER_BIN="$(find /usr/lib /usr/libexec -maxdepth 5 -type f -name 'gel-server' 2>/dev/null | head -n 1)"
fi
if [ -z "$SERVER_BIN" ]; then
  echo "ERROR: no gel-server binary found (tried gel-server, gel-server-6, /usr/bin/gel-server*, /usr/lib/**/gel-server)" >&2
  exit 1
fi
echo "using server binary: $SERVER_BIN"

# --runstate-dir is always passed explicitly: the default lives under /run and
# is not writable by the unprivileged server user.
setsid su gelsrv -s /bin/bash -c "exec '$SERVER_BIN' --data-dir='$DATA_DIR' --runstate-dir='$RUN_DIR' --port=5656 --bind-address=127.0.0.1 --tls-cert-mode=generate_self_signed --default-auth-method=Trust --compiler-pool-size=1" >>"$LOG_FILE" 2>&1 &

for _ in $(seq 1 60); do
  if gel query -F json 'select 1' >/dev/null 2>&1; then
    echo "gel server is ready"
    exit 0
  fi
  sleep 1
done

echo "ERROR: the gel server did not become ready in time. Last log lines:" >&2
tail -n 80 "$LOG_FILE" >&2
exit 1
EOF

# --- Helper: stop the local Gel server --------------------------------------
RUN cat >/usr/local/bin/gel-stop.sh <<'EOF'
#!/bin/bash
# Stop the local Gel server if it is running.
cd / || exit 1
pkill -f 'gel-server' >/dev/null 2>&1
for _ in $(seq 1 60); do
  if ! gel query -F json 'select 1' >/dev/null 2>&1; then
    echo "gel server stopped"
    exit 0
  fi
  sleep 1
done
echo "WARNING: the gel server is still answering queries" >&2
exit 1
EOF

RUN chmod 0755 /usr/local/bin/gel-start.sh /usr/local/bin/gel-stop.sh

# --- Project skeleton -------------------------------------------------------
RUN cat >/home/user/mtsaas/gel.toml <<'EOF'
[instance]
server-version = "6.0"

[project]
schema-dir = "dbschema"
EOF

RUN cat >/home/user/mtsaas/dbschema/default.gel <<'EOF'
module default {
    type Tenant {
        required slug: str {
            constraint exclusive;
        }
        required name: str;
    }

    type Workspace {
        required name: str;
        required tenant: Tenant;
        required archived: bool {
            default := false;
        }
        constraint exclusive on ((.tenant, .name));
    }

    type Document {
        required title: str;
        required body: str;
        required workspace: Workspace;
        required created_at: datetime {
            default := datetime_of_statement();
        }
        index on (.title);
    }

    type Comment {
        required body: str;
        required author_email: str;
        required document: Document;
    }
}
EOF

RUN cat >/home/user/mtsaas/app/tenant_gateway.py <<'EOF'
"""Tenant-scoped data gateway for the multi-tenant document SaaS.

Everything below is an unimplemented stub.
"""


async def list_workspaces(tenant_slug, role):
    raise NotImplementedError("list_workspaces is not implemented yet")


async def get_document(tenant_slug, role, document_id):
    raise NotImplementedError("get_document is not implemented yet")


async def create_document(
    tenant_slug, role, workspace_name, title, body, comment_bodies, author_email
):
    raise NotImplementedError("create_document is not implemented yet")


async def rename_document(tenant_slug, role, document_id, new_title):
    raise NotImplementedError("rename_document is not implemented yet")


async def delete_document(tenant_slug, role, document_id):
    raise NotImplementedError("delete_document is not implemented yet")


async def archive_workspace(tenant_slug, role, workspace_name):
    raise NotImplementedError("archive_workspace is not implemented yet")


async def platform_document_counts(role):
    raise NotImplementedError("platform_document_counts is not implemented yet")
EOF

RUN cat >/opt/seed/seed.edgeql <<'EOF'
insert Tenant { slug := 'acme', name := 'Acme Corp' };
insert Tenant { slug := 'globex', name := 'Globex Inc' };
insert Tenant { slug := 'initech', name := 'Initech LLC' };

insert Workspace {
    name := 'alpha',
    tenant := assert_single((select Tenant filter .slug = 'acme'))
};
insert Workspace {
    name := 'beta',
    tenant := assert_single((select Tenant filter .slug = 'acme'))
};
insert Workspace {
    name := 'zeta-archived',
    archived := true,
    tenant := assert_single((select Tenant filter .slug = 'acme'))
};
insert Workspace {
    name := 'gamma',
    tenant := assert_single((select Tenant filter .slug = 'globex'))
};
insert Workspace {
    name := 'delta',
    tenant := assert_single((select Tenant filter .slug = 'globex'))
};
insert Workspace {
    name := 'omega',
    tenant := assert_single((select Tenant filter .slug = 'initech'))
};

insert Document {
    title := 'Alpha Charter',
    body := 'Founding charter of the alpha workspace.',
    workspace := assert_single((select Workspace filter .name = 'alpha'))
};
insert Document {
    title := 'Alpha Roadmap',
    body := 'Roadmap for the alpha workspace.',
    workspace := assert_single((select Workspace filter .name = 'alpha'))
};
insert Document {
    title := 'Beta Notes',
    body := 'Scratch notes for the beta workspace.',
    workspace := assert_single((select Workspace filter .name = 'beta'))
};
insert Document {
    title := 'Frozen Plan',
    body := 'Plan kept in the archived workspace.',
    workspace := assert_single((select Workspace filter .name = 'zeta-archived'))
};
insert Document {
    title := 'Gamma Spec',
    body := 'Specification owned by globex.',
    workspace := assert_single((select Workspace filter .name = 'gamma'))
};
insert Document {
    title := 'Gamma Budget',
    body := 'Budget owned by globex.',
    workspace := assert_single((select Workspace filter .name = 'gamma'))
};

insert Comment {
    body := 'charter note one',
    author_email := 'seed@acme.test',
    document := assert_single((select Document filter .title = 'Alpha Charter'))
};
insert Comment {
    body := 'charter note two',
    author_email := 'seed@acme.test',
    document := assert_single((select Document filter .title = 'Alpha Charter'))
};
insert Comment {
    body := 'roadmap note one',
    author_email := 'seed@acme.test',
    document := assert_single((select Document filter .title = 'Alpha Roadmap'))
};
insert Comment {
    body := 'frozen note one',
    author_email := 'seed@acme.test',
    document := assert_single((select Document filter .title = 'Frozen Plan'))
};
insert Comment {
    body := 'spec note one',
    author_email := 'seed@globex.test',
    document := assert_single((select Document filter .title = 'Gamma Spec'))
};
EOF

# --- Bootstrap the instance: first migration + seeded multi-tenant data -----
RUN /usr/local/bin/gel-start.sh && \
    cd /home/user/mtsaas && \
    gel migration create --non-interactive --allow-unsafe --schema-dir /home/user/mtsaas/dbschema && \
    gel migrate --schema-dir /home/user/mtsaas/dbschema && \
    gel migration status --schema-dir /home/user/mtsaas/dbschema && \
    gel query -f /opt/seed/seed.edgeql && \
    gel query -F json 'select count(Tenant)' && \
    gel query -F json 'select count(Workspace)' && \
    gel query -F json 'select count(Document)' && \
    gel query -F json 'select count(Comment)' && \
    ls -1 /home/user/mtsaas/dbschema/migrations/*.edgeql && \
    /usr/local/bin/gel-stop.sh

LABEL Author="generate-harbor-tasks"
LABEL Description="Gel 6 multi-tenant SaaS project at /home/user/mtsaas: policy-free schema, one applied migration, seeded tenants/workspaces/documents/comments, and an unimplemented async Python gateway stub."

CMD ["/bin/bash", "-c", "trap : TERM INT; sleep infinity & wait"]

# Container contents:
#   - apt packages: curl wget ca-certificates gnupg git bash ripgrep procps locales python3 python3-pip
#   - pip3 packages: pytest (>=8), pytest-json-ctrf, gel (Python client); python3-pytest is
#     deliberately NOT installed because the old apt pytest breaks pytest-json-ctrf
#   - Pochi CLI installed from getpochi.com
#   - Gel 6 server (apt package gel-6) and Gel CLI (apt package gel-cli)
#   - gelsrv system user owning /var/lib/gel/data, /var/lib/gel/run and /var/log/gel
#   - /usr/local/bin/gel-start.sh: idempotent server start (explicit --runstate-dir,
#     defensive server-binary resolution); /usr/local/bin/gel-stop.sh: server stop
#   - env GEL_DSN / GEL_CLIENT_SECURITY so the CLI and Python clients connect with no flags
#   - /home/user/mtsaas: Gel project (gel.toml, dbschema/default.gel without globals or
#     access policies, dbschema/migrations with the single baked migration, app/ package
#     holding the unimplemented tenant_gateway.py stub)
#   - /opt/seed/seed.edgeql: the baked multi-tenant seed data (3 tenants, 6 workspaces,
#     6 documents, 5 comments)

COPY test_initial_state.py /bootstrap/test_initial_state.py

# Setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
