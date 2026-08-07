FROM ubuntu:24.04

ARG POCHI_VERSION

# --------------------------------------------------------------------------- #
# Base tooling: python (without the distro pytest), pochi CLI, pytest via pip.
# --------------------------------------------------------------------------- #
RUN apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl wget ca-certificates git python3 python3-pip bash ripgrep \
        gnupg jq tzdata procps coreutils && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    curl -fsSL https://getpochi.com/install.sh | bash -s ${POCHI_VERSION} && \
    ln -s /root/.pochi/bin/pochi /usr/local/bin/pochi && \
    pip3 install --break-system-packages --no-cache-dir "pytest>=8" pytest-json-ctrf && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# --------------------------------------------------------------------------- #
# Gel 7: server + CLI from the official apt repository.
# --------------------------------------------------------------------------- #
RUN mkdir -p /usr/local/share/keyrings && \
    curl --proto '=https' --tlsv1.2 -sSf -o /usr/local/share/keyrings/gel-keyring.gpg \
        https://packages.geldata.com/keys/gel-keyring.gpg && \
    echo "deb [signed-by=/usr/local/share/keyrings/gel-keyring.gpg] https://packages.geldata.com/apt noble main" \
        > /etc/apt/sources.list.d/gel.list && \
    apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        gel-cli gel-server-7 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# --------------------------------------------------------------------------- #
# Unprivileged service account + explicit data/runstate directories.
# `gel-server` refuses to run as root, and the default runstate path (/run/gel)
# is root-owned tmpfs, so both are pinned here and chown-ed up front.
# --------------------------------------------------------------------------- #
RUN groupadd --system gelsrv && \
    useradd --system --gid gelsrv --home-dir /var/lib/gel --shell /usr/sbin/nologin gelsrv && \
    mkdir -p /var/lib/gel/data /var/lib/gel/run /var/log/gel && \
    chown -R gelsrv:gelsrv /var/lib/gel /var/log/gel

# --------------------------------------------------------------------------- #
# Idempotent server launcher used by the container CMD and by the test suites.
# --------------------------------------------------------------------------- #
RUN cat >/usr/local/bin/gel-up <<'GELUP'
#!/bin/bash
# Start the local Gel server (idempotently) and block until it answers queries.
DATA_DIR=/var/lib/gel/data
RUN_DIR=/var/lib/gel/run
LOG_FILE=/var/log/gel/server.log

if gel query "select 1" >/dev/null 2>&1; then
    exit 0
fi

SERVER_BIN=""
for candidate in gel-server gel-server-7; do
    if command -v "$candidate" >/dev/null 2>&1; then
        SERVER_BIN="$(command -v "$candidate")"
        break
    fi
done
if [ -z "$SERVER_BIN" ]; then
    SERVER_BIN="$(ls /usr/bin/gel-server* 2>/dev/null | head -n 1)"
fi
if [ -z "$SERVER_BIN" ]; then
    echo "gel-up: no gel-server binary found (looked for gel-server, gel-server-7, /usr/bin/gel-server*)" >&2
    exit 1
fi

mkdir -p "$DATA_DIR" "$RUN_DIR" /var/log/gel
chown -R gelsrv:gelsrv "$DATA_DIR" "$RUN_DIR" /var/log/gel

su gelsrv -s /bin/bash -c "'$SERVER_BIN' \
    --data-dir='$DATA_DIR' \
    --runstate-dir='$RUN_DIR' \
    --bind-address=127.0.0.1 \
    --port=5656 \
    --security=insecure_dev_mode \
    --default-auth-method=Trust \
    --tls-cert-mode=generate_self_signed \
    --compiler-pool-size=2 \
    >>'$LOG_FILE' 2>&1 &"

for _ in $(seq 1 180); do
    if gel query "select 1" >/dev/null 2>&1; then
        exit 0
    fi
    sleep 1
done

echo "gel-up: the Gel server did not become ready in time; last log lines:" >&2
tail -n 60 "$LOG_FILE" >&2
exit 1
GELUP
RUN chmod 0755 /usr/local/bin/gel-up

# Client connection context for the `gel` CLI and any client library.
ENV GEL_HOST=127.0.0.1
ENV GEL_PORT=5656
ENV GEL_USER=admin
ENV GEL_BRANCH=main
ENV GEL_CLIENT_SECURITY=insecure_dev_mode

# --------------------------------------------------------------------------- #
# Bootstrap the database cluster once at build time.
# --------------------------------------------------------------------------- #
RUN su gelsrv -s /bin/bash -c "gel-server-7 \
        --data-dir=/var/lib/gel/data \
        --runstate-dir=/var/lib/gel/run \
        --bootstrap-only \
        --security=insecure_dev_mode \
        --default-auth-method=Trust \
        --tls-cert-mode=generate_self_signed \
        --compiler-pool-size=2"

# --------------------------------------------------------------------------- #
# Starter project skeleton.
# --------------------------------------------------------------------------- #
RUN mkdir -p /home/user && chmod 0755 /home/user
RUN mkdir -p /home/user/usage-report/dbschema /home/user/usage-report/data

RUN cat >/home/user/usage-report/gel.toml <<'GELTOML'
[instance]
server-version = "7.1"
GELTOML

RUN cat >/home/user/usage-report/dbschema/default.gel <<'SCHEMA'
module default {
  # Starter model. Tenants are billed against their own local calendar, but the
  # timezone and anchor date are not modelled yet, and usage is not stored here
  # at all.
  type Tenant {
    required code: str {
      constraint exclusive;
    }
    required display_name: str;
  }
}
SCHEMA

RUN cat >/home/user/usage-report/data/tenants.csv <<'TENANTS'
code,display_name,tz,billing_anchor
acme-us,Acme Corp,America/New_York,2024-03-08
globex-de,Globex GmbH,Europe/Berlin,2024-10-25
initech-in,Initech Pvt Ltd,Asia/Kolkata,2024-02-27
TENANTS

RUN cat >/home/user/usage-report/data/sessions.csv <<'SESSIONS'
session_key,tenant_code,started_at,ended_at
s-us-01,acme-us,2024-03-08T06:30:00Z,2024-03-08T07:30:00Z
s-us-02,acme-us,2024-03-09T04:45:00Z,2024-03-09T06:15:00Z
s-us-03,acme-us,2024-03-10T06:30:00Z,2024-03-10T07:30:00Z
s-us-04,acme-us,2024-03-11T13:00:00Z,2024-03-11T17:30:00Z
s-us-05,acme-us,2024-03-12T03:00:00Z,2024-03-12T05:00:00Z
s-us-06,acme-us,2024-03-13T02:00:00Z,2024-03-13T03:30:00Z
s-us-07,acme-us,2024-03-14T15:00:00Z,2024-03-14T16:00:00Z
s-de-01,globex-de,2024-10-25T08:00:00Z,2024-10-25T09:30:00Z
s-de-02,globex-de,2024-10-25T22:30:00Z,2024-10-25T23:30:00Z
s-de-03,globex-de,2024-10-27T00:30:00Z,2024-10-27T01:30:00Z
s-de-04,globex-de,2024-10-28T07:00:00Z,2024-10-28T11:00:00Z
s-de-05,globex-de,2024-10-29T23:30:00Z,2024-10-30T00:15:00Z
s-de-06,globex-de,2024-10-29T05:00:00Z,2024-10-29T05:45:00Z
s-in-01,initech-in,2024-02-27T18:45:00Z,2024-02-27T19:45:00Z
s-in-02,initech-in,2024-02-28T18:40:00Z,2024-02-28T20:10:00Z
s-in-03,initech-in,2024-02-29T10:00:00Z,2024-02-29T12:30:00Z
s-in-04,initech-in,2024-02-29T19:00:00Z,2024-02-29T20:00:00Z
s-in-05,initech-in,2024-02-26T20:00:00Z,2024-02-26T21:00:00Z
SESSIONS

# --------------------------------------------------------------------------- #
# Apply the starter migration so the container boots with a schema history that
# is already in sync. The migration filename is content-hashed, so it is never
# referenced literally anywhere.
# --------------------------------------------------------------------------- #
RUN /usr/local/bin/gel-up && \
    cd /home/user/usage-report && \
    gel migration create --non-interactive && \
    gel migrate && \
    gel migration status && \
    ls -1 /home/user/usage-report/dbschema/migrations/

LABEL Author="generate-harbor-tasks"
LABEL Description="Ubuntu 24.04 with a local Gel 7 server, the gel CLI and a starter usage-report project for timezone/duration billing reports."

CMD ["/bin/bash", "-c", "trap : TERM INT; /usr/local/bin/gel-up || true; sleep infinity & wait"]

# Container contents:
#   - always required apt packages: curl wget ca-certificates git python3 python3-pip bash ripgrep
#     (python3-pytest is deliberately NOT installed: the distro build breaks pytest-json-ctrf)
#   - extra apt packages: gnupg jq tzdata procps coreutils
#   - always required pip3 packages: pytest (>=8) and pytest-json-ctrf
#   - Gel 7: `gel-cli` (gel CLI) and `gel-server-7` from packages.geldata.com (noble)
#   - service account `gelsrv` owning /var/lib/gel/{data,run} and /var/log/gel
#   - /usr/local/bin/gel-up: idempotent local server launcher with an explicit --runstate-dir
#   - GEL_HOST/GEL_PORT/GEL_USER/GEL_BRANCH/GEL_CLIENT_SECURITY preconfigure the CLI
#   - /home/user/usage-report: starter project (gel.toml, dbschema/default.gel,
#     one applied migration, data/tenants.csv, data/sessions.csv)
