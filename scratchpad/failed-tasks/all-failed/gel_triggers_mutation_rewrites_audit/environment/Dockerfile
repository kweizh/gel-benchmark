FROM ubuntu:24.04

ARG POCHI_VERSION

RUN apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl wget ca-certificates git python3 python3-pip python3-pytest bash ripgrep procps && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    curl -fsSL https://getpochi.com/install.sh | bash -s ${POCHI_VERSION} && \
    ln -s /root/.pochi/bin/pochi /usr/local/bin/pochi && \
    pip3 install --break-system-packages pytest-json-ctrf gel && \
    mkdir -p /usr/local/share/keyrings && \
    curl --proto '=https' --tlsv1.2 -sSf \
        -o /usr/local/share/keyrings/gel-keyring.gpg \
        https://packages.geldata.com/keys/gel-keyring.gpg && \
    echo "deb [signed-by=/usr/local/share/keyrings/gel-keyring.gpg] https://packages.geldata.com/apt noble main" \
        > /etc/apt/sources.list.d/gel.list && \
    apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends gel-6 gel-cli && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Connection settings of the local, development-only Gel instance.
ENV GEL_HOST=127.0.0.1 \
    GEL_PORT=5656 \
    GEL_USER=admin \
    GEL_BRANCH=main \
    GEL_CLIENT_TLS_SECURITY=insecure \
    GEL_DATA_DIR=/var/lib/gel-data \
    GEL_RUNSTATE_DIR=/tmp/gel-runstate \
    GEL_SERVER_LOG=/tmp/gel-server.log

RUN mkdir -p /home/user && chmod 0755 /home/user

# gel-server refuses to run as root, so the server process runs as the
# unprivileged `gel` user while the task itself is carried out as root.
RUN (id -u gel >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/gel --shell /bin/bash gel) && \
    mkdir -p /var/lib/gel-data /tmp/gel-runstate && \
    touch /tmp/gel-server.log && \
    chown gel:gel /var/lib/gel-data /tmp/gel-runstate /tmp/gel-server.log && \
    chmod 0700 /var/lib/gel-data && \
    chmod 0755 /tmp/gel-runstate

RUN cat >/usr/local/bin/gel-up <<'EOS' && chmod 0755 /usr/local/bin/gel-up
#!/bin/sh
# Start the local Gel server unless it is already accepting queries.
# Idempotent: exits 0 as soon as a trivial query succeeds.
DATA_DIR="${GEL_DATA_DIR:-/var/lib/gel-data}"
RUNSTATE_DIR="${GEL_RUNSTATE_DIR:-/tmp/gel-runstate}"
LOG_FILE="${GEL_SERVER_LOG:-/tmp/gel-server.log}"
PORT="${GEL_PORT:-5656}"

SERVER_BIN="$(ls /usr/lib/*/gel-server-*/bin/gel-server 2>/dev/null | head -n 1)"
if [ -z "${SERVER_BIN}" ]; then
    echo "gel-up: no gel-server binary found" >&2
    exit 1
fi

ready() {
    gel query "select 1" >/dev/null 2>&1
}

if ready; then
    exit 0
fi

mkdir -p "${RUNSTATE_DIR}" 2>/dev/null
touch "${LOG_FILE}" 2>/dev/null
if [ "$(id -u)" = "0" ]; then
    chown gel:gel "${RUNSTATE_DIR}" "${LOG_FILE}" 2>/dev/null
fi

START_CMD="nohup '${SERVER_BIN}' --data-dir='${DATA_DIR}' --runstate-dir='${RUNSTATE_DIR}' --port='${PORT}' --bind-address=127.0.0.1 --security=insecure_dev_mode --default-auth-method=Trust --tls-cert-mode=generate_self_signed --admin-ui=disabled >>'${LOG_FILE}' 2>&1 &"

if [ "$(id -u)" = "0" ]; then
    su gel -s /bin/sh -c "${START_CMD}"
else
    /bin/sh -c "${START_CMD}"
fi

i=0
while [ "${i}" -lt 300 ]; do
    if ready; then
        exit 0
    fi
    i=$((i + 1))
    sleep 1
done

echo "gel-up: the local Gel server did not become ready in time; last log lines:" >&2
tail -n 40 "${LOG_FILE}" >&2
exit 1
EOS

RUN cat >/usr/local/bin/gel-down <<'EOS' && chmod 0755 /usr/local/bin/gel-down
#!/bin/sh
# Stop the local Gel server. Used while building this image.
pkill -TERM -u gel -f "gel-server-" 2>/dev/null
i=0
while [ "${i}" -lt 60 ]; do
    if ! pgrep -u gel -f "gel-server-" >/dev/null 2>&1; then
        exit 0
    fi
    i=$((i + 1))
    sleep 1
done
exit 0
EOS

# Initial project state: a plain Document/Comment schema with one applied
# migration and a handful of production rows -- no audit machinery at all.
RUN mkdir -p /home/user/auditdb/dbschema && cat >/home/user/auditdb/gel.toml <<'EOT'
[instance]
server-version = "6"
EOT

RUN cat >/home/user/auditdb/dbschema/default.gel <<'EOT'
module default {
  type Document {
    required slug: str {
      constraint exclusive;
    }
    required title: str;
    required body: str;
  }

  type Comment {
    required body: str;
    required document: Document;
  }
}
EOT

RUN cat >/tmp/seed.edgeql <<'EOT'
insert Document { slug := 'seed-alpha', title := 'Alpha Doc', body := 'alpha body' };
insert Document { slug := 'seed-beta', title := 'Beta Doc', body := 'beta body' };
insert Document { slug := 'seed-gamma', title := 'Gamma Doc', body := 'gamma body' };
insert Comment {
  body := 'alpha comment one',
  document := assert_single((select Document filter .slug = 'seed-alpha')),
};
insert Comment {
  body := 'alpha comment two',
  document := assert_single((select Document filter .slug = 'seed-alpha')),
};
insert Comment {
  body := 'beta comment one',
  document := assert_single((select Document filter .slug = 'seed-beta')),
};
EOT

# Bootstrap the instance, apply the first migration, load the seed rows and shut
# the server down again so the image ships a ready-to-use data directory.
RUN gel-up && \
    cd /home/user/auditdb && \
    gel migration create --non-interactive && \
    gel migrate && \
    gel query -f /tmp/seed.edgeql && \
    gel migration status && \
    rm -f /tmp/seed.edgeql && \
    gel-down

LABEL Author="generate-harbor-tasks"
LABEL Description="Gel 6 local instance with a seeded Document/Comment project at /home/user/auditdb, awaiting a schema-enforced audit engine."

CMD ["/bin/bash", "-c", "trap : TERM INT; sleep infinity & wait"]

# Container contents:
#   - always required apt packages: curl wget ca-certificates git python3 python3-pip python3-pytest bash ripgrep (plus procps for process control)
#   - always required pip3 packages: pytest-json-ctrf
#   - pip3 package `gel`: Python client used by the task and by the tests
#   - apt packages gel-6 / gel-cli from packages.geldata.com: local Gel 6 server and CLI
#   - /usr/local/bin/gel-up: idempotent helper that starts the local server (runs it as the `gel` user)
#   - /usr/local/bin/gel-down: stops the local server (used during the image build)
#   - /var/lib/gel-data: pre-bootstrapped data directory containing branch `main`
#   - /home/user/auditdb: Gel project (gel.toml, dbschema/default.gel, dbschema/migrations) with 3 Documents and 3 Comments seeded

COPY test_initial_state.py /bootstrap/test_initial_state.py

# Setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
