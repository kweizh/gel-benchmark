FROM ubuntu:24.04

ARG POCHI_VERSION
ARG GEL_MAJOR=6

# Local-only Gel setup: the server data/runstate live outside any declared volume,
# and an explicit --runstate-dir is always passed to gel-server (see gel-start.sh).
ENV GEL_DATA_DIR=/opt/gel/data \
    GEL_RUNSTATE_DIR=/opt/gel/run \
    GEL_SERVER_LOG=/var/log/gel-server.log \
    GEL_HOST=127.0.0.1 \
    GEL_PORT=5656 \
    GEL_USER=admin \
    GEL_BRANCH=main \
    GEL_CLIENT_TLS_SECURITY=insecure \
    GEL_RUN_VERSION_CHECK=never

RUN apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl wget ca-certificates git python3 python3-pip bash ripgrep \
        gnupg jq procps util-linux && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    mkdir -p /usr/local/share/keyrings && \
    curl --proto '=https' --tlsv1.2 -sSf \
        -o /usr/local/share/keyrings/gel-keyring.gpg \
        https://packages.geldata.com/keys/gel-keyring.gpg && \
    echo "deb [signed-by=/usr/local/share/keyrings/gel-keyring.gpg] https://packages.geldata.com/apt $(grep VERSION_CODENAME= /etc/os-release | cut -d= -f2) main" \
        > /etc/apt/sources.list.d/gel.list && \
    apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        gel-${GEL_MAJOR} && \
    curl -fsSL https://getpochi.com/install.sh | bash -s ${POCHI_VERSION} && \
    ln -s /root/.pochi/bin/pochi /usr/local/bin/pochi && \
    pip3 install --break-system-packages "pytest>=8" pytest-json-ctrf && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# gel-server refuses to run as root, so it runs as a dedicated unprivileged user
# whose data dir and runstate dir are pre-created and chowned.
RUN (id -u gel >/dev/null 2>&1 || \
        useradd --system --create-home --home-dir /opt/gel --shell /bin/bash gel) && \
    mkdir -p /opt/gel/data /opt/gel/run /opt/task-reference /var/lock && \
    touch /var/log/gel-server.log && \
    chown -R gel:gel /opt/gel /var/log/gel-server.log

# Idempotent, lock-protected starter for the local Gel server. The server binary
# name differs between images/versions, so it is resolved defensively.
RUN cat > /usr/local/bin/gel-start.sh <<'GELSTART'
#!/usr/bin/env bash
set -uo pipefail

DATA_DIR="${GEL_DATA_DIR:-/opt/gel/data}"
RUNSTATE_DIR="${GEL_RUNSTATE_DIR:-/opt/gel/run}"
SRV_PORT="${GEL_PORT:-5656}"
LOG="${GEL_SERVER_LOG:-/var/log/gel-server.log}"

exec 9>/var/lock/gel-start.lock
flock -w 900 9 || exit 1

resolve_server_binary() {
  local candidate
  for candidate in gel-server gel-server-6 gel-server-7 gel-server-8; do
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
  echo "FATAL: no gel-server binary found (looked for gel-server, gel-server-N)" >&2
  return 1
}

is_up() {
  gel query "select 1" >/dev/null 2>&1
}

if is_up; then
  exit 0
fi

SRV="$(resolve_server_binary)" || exit 1

mkdir -p "$DATA_DIR" "$RUNSTATE_DIR"
touch "$LOG"
chown -R gel:gel "$DATA_DIR" "$RUNSTATE_DIR" "$LOG" 2>/dev/null || true

# NOTE: fd 9 (the flock) is explicitly closed for the child, otherwise the
# long-running server would inherit it and hold the lock forever.
su gel -s /bin/bash -c "nohup \"$SRV\" \
  --data-dir=\"$DATA_DIR\" \
  --runstate-dir=\"$RUNSTATE_DIR\" \
  --port=$SRV_PORT \
  --bind-address=127.0.0.1 \
  --security=insecure_dev_mode \
  --default-auth-method=Trust \
  --tls-cert-mode=generate_self_signed \
  >>\"$LOG\" 2>&1 &" 9>&-

for _ in $(seq 1 600); do
  if is_up; then
    exit 0
  fi
  sleep 1
done

echo "FATAL: the local Gel server did not become ready in time; last log lines:" >&2
tail -n 80 "$LOG" >&2
exit 1
GELSTART

RUN chmod 0755 /usr/local/bin/gel-start.sh

RUN mkdir -p /home/user && chmod 0755 /home/user

RUN mkdir -p /home/user/pricing-audit/dbschema /home/user/pricing-audit/scripts

RUN cat > /home/user/pricing-audit/gel.toml <<'GELTOML'
[project]
schema-dir = "dbschema"

[instance]
server-version = "6.11"
GELTOML

RUN cat > /home/user/pricing-audit/dbschema/default.gel <<'SDL'
module default {
  type Product {
    required sku: str {
      constraint exclusive;
    }
    required name: str;
    required price_cents: int64 {
      constraint min_value(1);
    }
    required stock: int64 {
      default := 0;
    }
  }
}
SDL

RUN cat > /opt/task-reference/seed.edgeql <<'SEED'
for p in {
  ('AX-100', 'Anvil', 1999, 12),
  ('AX-200', 'Bolt Set', 499, 340),
  ('BX-110', 'Cable Reel', 1250, 75),
  ('BX-220', 'Drill', 8999, 8),
  ('CX-130', 'Epoxy', 799, 51),
  ('CX-240', 'Fastener', 349, 620),
  ('DX-150', 'Gasket', 130, 275),
  ('SKU-FROZEN', 'Frozen Reference Widget', 5000, 1)
} union (
  insert Product {
    sku := p.0,
    name := p.1,
    price_cents := p.2,
    stock := p.3
  }
);
SEED

# Bootstrap the local instance: apply the first migration, seed the catalog and
# keep a pristine copy of the content-hashed 00001 migration (resolved by glob,
# never hardcoded) for later verification. The server is stopped gracefully so
# the committed data directory is clean.
RUN /usr/local/bin/gel-start.sh && \
    cd /home/user/pricing-audit && \
    gel migration create --non-interactive && \
    gel migrate && \
    gel query -f /opt/task-reference/seed.edgeql && \
    gel query -F json "select count(Product)" && \
    cp "$(ls /home/user/pricing-audit/dbschema/migrations/00001-*.edgeql | head -n 1)" \
        /opt/task-reference/00001-original.edgeql && \
    chmod 0444 /opt/task-reference/00001-original.edgeql && \
    pkill -TERM -f gel-server ; \
    for i in $(seq 1 90); do pgrep -f gel-server >/dev/null || break; sleep 1; done; \
    chown -R gel:gel /opt/gel && \
    chmod -R 0777 /home/user/pricing-audit && \
    test -f /opt/task-reference/00001-original.edgeql

LABEL Author="generate-harbor-tasks"
LABEL Description="Local Gel 6.11 server plus an initialized Gel project (/home/user/pricing-audit) with a seeded Product catalog and one applied migration, ready for a triggers/mutation-rewrite audit-trail task."

CMD ["/bin/bash", "-c", "trap : TERM INT; sleep infinity & wait"]

# Container contents:
#   - apt packages: curl wget ca-certificates git python3 python3-pip bash ripgrep gnupg jq procps util-linux
#     (python3-pytest is deliberately NOT installed: the distro pytest breaks pytest-json-ctrf)
#   - pip3 packages: pytest>=8, pytest-json-ctrf
#   - Gel 6.11 server + `gel` CLI from the official apt repository (packages.geldata.com)
#   - /usr/local/bin/gel-start.sh: idempotent, flock-protected local server starter
#     (always passes an explicit --runstate-dir and resolves the server binary defensively)
#   - /opt/gel/{data,run}: Gel data directory and runstate directory owned by the `gel` user
#   - /home/user/pricing-audit: initialized Gel project (gel.toml, dbschema/default.gel,
#     dbschema/migrations/00001-*.edgeql applied, empty scripts/ directory)
#   - /opt/task-reference/00001-original.edgeql: pristine copy of the first migration
#   - /opt/task-reference/seed.edgeql: the seed statement used to create the 8 products

COPY test_initial_state.py /bootstrap/test_initial_state.py

# Setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
