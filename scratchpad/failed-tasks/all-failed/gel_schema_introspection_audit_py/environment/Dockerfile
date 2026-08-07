FROM ubuntu:24.04

ARG POCHI_VERSION

ENV LANG=C.UTF-8
ENV DEBIAN_FRONTEND=noninteractive

# ---------------------------------------------------------------------------
# Base tooling. NOTE: python3-pytest is deliberately NOT installed - the
# distro pytest is too old for pytest-json-ctrf and aborts the whole run with
# an INTERNALERROR. pytest comes from pip instead.
# ---------------------------------------------------------------------------
RUN apt-get update -y && \
    apt-get install -y --no-install-recommends \
        curl wget ca-certificates git python3 python3-pip bash ripgrep \
        gnupg procps ncurses-bin && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    curl -fsSL https://getpochi.com/install.sh | bash -s ${POCHI_VERSION} && \
    ln -s /root/.pochi/bin/pochi /usr/local/bin/pochi && \
    pip3 install --break-system-packages pytest pytest-json-ctrf gel && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Gel 6 server + CLI from the official apt repository.
# ---------------------------------------------------------------------------
RUN mkdir -p /usr/local/share/keyrings && \
    curl --proto '=https' --tlsv1.2 -sSf \
        -o /usr/local/share/keyrings/gel-keyring.gpg \
        https://packages.geldata.com/keys/gel-keyring.gpg && \
    echo "deb [signed-by=/usr/local/share/keyrings/gel-keyring.gpg] https://packages.geldata.com/apt noble main" \
        > /etc/apt/sources.list.d/gel.list && \
    apt-get update -y && \
    ( apt-get install -y --no-install-recommends gel-server-6 gel-cli || \
      apt-get install -y --no-install-recommends gel-6 gel-cli ) && \
    command -v gel && \
    ( command -v gel-server || ls /usr/bin/gel-server* || ls /usr/lib/*/gel-server-*/bin/gel-server ) && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Connection settings shared by the gel CLI and every Gel client library.
# ---------------------------------------------------------------------------
ENV GEL_HOST=127.0.0.1
ENV GEL_PORT=5656
ENV GEL_USER=admin
ENV GEL_BRANCH=main
ENV GEL_CLIENT_TLS_SECURITY=insecure

RUN mkdir -p /home/user && chmod 0755 /home/user

# ---------------------------------------------------------------------------
# Helper: resolve the local server binary defensively - its name differs
# between base images, install methods and Gel major versions.
# ---------------------------------------------------------------------------
RUN cat > /usr/local/bin/gel-resolve-server.sh <<'RESOLVE_EOF'
#!/bin/bash
# Print the absolute path of the local Gel server binary.
for candidate in gel-server gel-server-6 gel-server-7 gel-server-5; do
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
found="$(ls /usr/lib/*/gel-server-*/bin/gel-server 2>/dev/null | head -n 1)"
if [ -n "$found" ]; then
    echo "$found"
    exit 0
fi
echo "FATAL: no Gel server binary could be resolved" >&2
exit 1
RESOLVE_EOF

# ---------------------------------------------------------------------------
# Helper: start the local server (idempotent). An explicit --runstate-dir is
# mandatory because the default lives under root-owned /run while the server
# itself refuses to run as root.
# ---------------------------------------------------------------------------
RUN cat > /usr/local/bin/gel-start.sh <<'START_EOF'
#!/bin/bash
DATA_DIR=/var/lib/gel/data
RUN_DIR=/var/lib/gel/run
LOG_FILE=/var/log/gel-server.log

# Keep the readiness probes snappy; this only affects this script's own
# `gel query` calls.
export GEL_WAIT_UNTIL_AVAILABLE=1s

if gel query -F json "select 1" >/dev/null 2>&1; then
    echo "gel server already running"
    exit 0
fi

SERVER_BIN="$(/usr/local/bin/gel-resolve-server.sh)" || exit 1
echo "using server binary: ${SERVER_BIN}"

mkdir -p "${DATA_DIR}" "${RUN_DIR}"
if id -u gel >/dev/null 2>&1; then
    chown -R gel "${DATA_DIR}" "${RUN_DIR}" 2>/dev/null || true
fi
touch "${LOG_FILE}"

SERVER_CMD="${SERVER_BIN} --data-dir=${DATA_DIR} --runstate-dir=${RUN_DIR} --port=5656 --bind-address=127.0.0.1 --security=insecure_dev_mode --tls-cert-mode=generate_self_signed"

if [ "$(id -u)" = "0" ] && id -u gel >/dev/null 2>&1; then
    nohup su gel -s /bin/bash -c "${SERVER_CMD}" >>"${LOG_FILE}" 2>&1 &
else
    nohup ${SERVER_CMD} >>"${LOG_FILE}" 2>&1 &
fi

for attempt in $(seq 1 240); do
    if gel query -F json "select 1" >/dev/null 2>&1; then
        echo "gel server ready after ${attempt}s"
        exit 0
    fi
    sleep 1
done

echo "FATAL: the Gel server did not become ready" >&2
tail -n 80 "${LOG_FILE}" >&2
exit 1
START_EOF

# ---------------------------------------------------------------------------
# Helper: stop the local server gracefully. Kept in its own script so that no
# build/test shell command line contains the pattern being matched.
# ---------------------------------------------------------------------------
RUN cat > /usr/local/bin/gel-stop.sh <<'STOP_EOF'
#!/bin/bash
pkill -TERM -f "gel-server" >/dev/null 2>&1 || true
for attempt in $(seq 1 120); do
    if pgrep -f "gel-server" >/dev/null 2>&1; then
        sleep 1
    else
        break
    fi
done
exit 0
STOP_EOF

RUN chmod 0755 /usr/local/bin/gel-resolve-server.sh \
               /usr/local/bin/gel-start.sh \
               /usr/local/bin/gel-stop.sh

# ---------------------------------------------------------------------------
# The Gel project skeleton.
# ---------------------------------------------------------------------------
RUN mkdir -p /home/user/gel-audit/dbschema /home/user/gel-audit/schema_audit

RUN cat > /home/user/gel-audit/gel.toml <<'TOML_EOF'
[instance]
server-version = "6.0"

[project]
schema-dir = "dbschema"
TOML_EOF

RUN cat > /home/user/gel-audit/dbschema/default.gel <<'SDL_EOF'
module default {
  abstract annotation deprecated;

  scalar type Tier extending enum<Free, Pro, Enterprise>;

  global current_tenant_id: uuid;
  global activeRegion: str;
  required global audit_mode: bool {
    default := false;
  };
  global current_tenant := (
    select Tenant filter .id = global current_tenant_id
  );

  function slugify(value: str) -> str
    using (str_lower(value));

  function tier_rank(t: Tier) -> int64
    using (1 if t = Tier.Free else (2 if t = Tier.Pro else 3));

  alias ProjectDigest := (
    select Project {
      name,
      owner_email := .owner.email
    }
  );

  abstract type Timestamped {
    required created_at: datetime {
      default := datetime_of_statement();
    }
  }

  abstract type Named {
    required name: str;
    index on (.name);
  }

  type Tenant extending Named {
    required slug: str {
      constraint exclusive;
    }
    required tier: Tier {
      default := Tier.Free;
    }
  }

  type Account extending Timestamped, Named {
    required email: str {
      constraint exclusive;
      constraint min_len_value(3);
    }
    required tenant_id: uuid;
    lastSyncedAt: datetime;
    multi projects := .<owner[is Project];

    access policy tenant_isolation
      allow select, update read
      using (.tenant_id ?= global current_tenant_id);
    access policy audit_override
      allow all
      using (global audit_mode);
  }

  type Project extending Timestamped, Named {
    required owner: Account;
    required tenant_id: uuid;
    required code: str {
      constraint exclusive;
    }
    active: bool {
      default := true;
    }
    multi contributors: Account {
      required role: str;
    }
    label := .name ++ ' [' ++ .code ++ ']';
    index on (.code);
    constraint exclusive on ((.owner, .name));
    index on ((.owner, .name));

    access policy owner_only
      allow all
      using (.owner.tenant_id ?= global current_tenant_id);
  }

  type Team extending Named {
    required slug: str {
      constraint exclusive;
    }
    multi members: Account {
      joined_at: datetime;
    }
    required multi maintainers: Account;
  }

  type Tag {
    required label: str {
      constraint exclusive;
    }
    multi taggedProjects: Project;
  }

  type ChangeLog {
    required action: str;
    required subject_name: str;
    required at: datetime {
      default := datetime_of_statement();
    }
  }

  type Widget extending Timestamped {
    required sku: str {
      constraint exclusive;
    }
    weight_grams: int64 {
      constraint min_value(0);
    }

    trigger log_widget_insert after insert for each do (
      insert ChangeLog {
        action := 'insert',
        subject_name := __new__.sku
      }
    );
  }

  type LegacyWidget extending Timestamped {
    annotation deprecated := 'superseded by Widget';
    required sku: str {
      constraint exclusive;
    }
  }

  type audit_entry {
    required message: str {
      constraint exclusive;
    }
    required at: datetime {
      default := datetime_of_statement();
    }
  }

  type Invoice {
    required amount: decimal;
    required tenant_id: uuid;

    access policy tenant_visible
      allow select
      using (.tenant_id ?= global current_tenant_id);
  }

  type Note {
    required body: str;

    access policy public_read
      allow select;
  }

  # === TEST INJECTION POINT: do not remove this line ===
}
SDL_EOF

RUN cat > /home/user/gel-audit/schema_audit/__init__.py <<'INIT_EOF'
"""schema_audit - audit the schema of the live Gel branch.

Nothing here is implemented yet.
"""

__all__ = ["build_audit", "main"]


async def build_audit(client):
    """Return the audit document for the schema of the client's branch."""
    raise NotImplementedError("schema_audit.build_audit is not implemented")


def main(argv):
    """Run the command line interface and return the process exit code."""
    raise NotImplementedError("schema_audit.main is not implemented")
INIT_EOF

RUN cat > /home/user/gel-audit/schema_audit/__main__.py <<'MAIN_EOF'
import sys

from schema_audit import main

raise SystemExit(main(sys.argv[1:]))
MAIN_EOF

# ---------------------------------------------------------------------------
# Bake the initial database state: bootstrap the instance, create and apply the
# first migration, then smoke-test every introspection query shape the task
# relies on and shut the server down cleanly.
# ---------------------------------------------------------------------------
RUN ( id -u gel >/dev/null 2>&1 || useradd --system --home-dir /var/lib/gel --shell /bin/bash gel ) && \
    mkdir -p /var/lib/gel/data /var/lib/gel/run && \
    chown -R gel /var/lib/gel && \
    /usr/local/bin/gel-start.sh && \
    cd /home/user/gel-audit && \
    gel migration create --non-interactive --allow-unsafe --schema-dir /home/user/gel-audit/dbschema && \
    gel migrate && \
    ls /home/user/gel-audit/dbschema/migrations/*.edgeql && \
    gel query -F json "select sys::get_current_branch()" && \
    gel query -F json "select sys::Branch { name, last_migration } filter .name = sys::get_current_branch()" && \
    gel query -F json "select schema::Migration { name, parents: { name } }" && \
    gel query -F json "select schema::Global { name, required, cardinality, expr, tname := .target.name } filter not .builtin and not .internal" && \
    gel query -F json "select schema::Alias { name } filter not .builtin and not .internal" && \
    gel query -F json "select schema::Function { name, volatility, rtname := .return_type.name, params: { name, idx := @index, tname := .type.name } } filter not .builtin and not .internal" && \
    gel query -F json "select schema::ObjectType { name, abstract, bases: { name }, annotations: { name, @value }, pointers: { name, ptype := .__type__.name, cardinality, required, readonly, expr, tname := .target.name, cnames := (select .constraints.name), [is schema::Link].pointers: { name, required, tname := .target.name, cnames := (select .constraints.name) } }, constraints: { name, subjectexpr }, indexes: { expr }, access_policies: { name, action, access_kinds, expr }, triggers: { name, timing, scope, kinds } } filter not .builtin and not .internal and not .from_alias and not .compound_type" && \
    gel query -F json "select count((select schema::ObjectType filter not .builtin and not .internal and not .from_alias and not .compound_type))" | python3 -c "import json,sys; data=json.load(sys.stdin); total=data[0] if isinstance(data, list) else data; print('audited object types:', total); sys.exit(0 if int(total) >= 10 else 1)" && \
    gel query -F json "select count((select schema::ObjectType filter .from_alias))" && \
    python3 -c "import gel; print('gel python client', gel.__name__)" && \
    /usr/local/bin/gel-stop.sh

LABEL Author="generate-harbor-tasks"
LABEL Description="Local Gel 6 server with a migrated, deliberately lint-unfriendly schema and a stubbed schema_audit Python package in /home/user/gel-audit."

CMD ["/bin/bash", "-c", "trap : TERM INT; sleep infinity & wait"]

# Container contents:
#   - always required apt packages: curl wget ca-certificates git python3 python3-pip bash ripgrep
#     (python3-pytest intentionally omitted; pytest comes from pip3)
#   - always required pip3 packages: pytest-json-ctrf (plus pytest and the gel Python client)
#   - Gel 6 server + gel CLI installed from packages.geldata.com (noble apt repo)
#   - /usr/local/bin/gel-resolve-server.sh: defensive server-binary resolution
#   - /usr/local/bin/gel-start.sh: idempotent server start with an explicit --runstate-dir
#   - /usr/local/bin/gel-stop.sh: graceful server shutdown
#   - /var/lib/gel/data: baked data directory with the applied migration
#   - /home/user/gel-audit: Gel project (gel.toml, dbschema/default.gel, dbschema/migrations)
#   - /home/user/gel-audit/schema_audit: stub package raising NotImplementedError

COPY test_initial_state.py /bootstrap/test_initial_state.py

# Setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
