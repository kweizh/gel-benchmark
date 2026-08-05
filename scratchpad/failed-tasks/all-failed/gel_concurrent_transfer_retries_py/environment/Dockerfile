# Task environment for gel_concurrent_transfer_retries_py.
#
# The official Gel 7.1 image is used as the base so that the database server and
# the `gel` CLI are both available locally inside the single task container; no
# network access is needed at task or verification time.
FROM geldata/gel:7.1

USER root
# The base image ships an ENTRYPOINT that would swallow the container command.
ENTRYPOINT []

ARG POCHI_VERSION

# Server-side settings: local dev instance, Trust auth, self-signed TLS, data
# directory outside the base image's VOLUME so it can be baked into the image.
ENV GEL_SERVER_SECURITY=insecure_dev_mode
ENV GEL_SERVER_TLS_CERT_MODE=generate_self_signed
ENV GEL_SERVER_BINARY_ENDPOINT_SECURITY=optional
ENV GEL_SERVER_HTTP_ENDPOINT_SECURITY=optional
ENV GEL_SERVER_DATADIR=/var/lib/gel-task-data
ENV GEL_SERVER_PORT=5656
ENV GEL_DOCKER_LOG_LEVEL=info
# Client-side settings picked up by the `gel` CLI and every client library.
ENV GEL_HOST=localhost
ENV GEL_PORT=5656
ENV GEL_BRANCH=main
ENV GEL_USER=admin
ENV GEL_CLIENT_SECURITY=insecure_dev_mode

RUN apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl wget ca-certificates git bash ripgrep unzip jq procps \
        python3 python3-pip python3-pytest && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    curl -fsSL https://getpochi.com/install.sh | bash -s ${POCHI_VERSION} && \
    ln -s /root/.pochi/bin/pochi /usr/local/bin/pochi && \
    pip3 install --break-system-packages \
        pytest-json-ctrf requests gel==3.1.0 \
        fastapi "uvicorn[standard]" flask gunicorn && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN mkdir -p /home/user && chmod 0755 /home/user

# The pip package must never shadow the `gel` CLI shipped in the base image.
RUN if [ -e /usr/bin/gel ] && [ -e /usr/local/bin/gel ]; then \
        rm -f /usr/local/bin/gel; \
    fi ; \
    gel --version && python3 -c "import gel; print(gel.__file__)"

# Schema and seed data applied during the instance bootstrap.
RUN mkdir -p /gel-bootstrap.d && \
    cat >/gel-bootstrap.d/01-init-ledger.edgeql <<'EOF'
create type default::Account { create required property code: std::str { create constraint std::exclusive; }; create required property balance_cents: std::int64; };
insert default::Account { code := 'RSV-AUDIT-1', balance_cents := 900000 };
insert default::Account { code := 'RSV-AUDIT-2', balance_cents := 125000 };
insert default::Account { code := 'RSV-AUDIT-3', balance_cents := 0 };
insert default::Account { code := 'RSV-AUDIT-4', balance_cents := 7 };
for i in range_unpack(range(1, 1001)) union (insert default::Account { code := 'ACC-' ++ <str>i, balance_cents := 250000 });
EOF

# Idempotent helper that boots the local Gel server and waits until it is ready.
RUN cat >/usr/local/bin/start-gel.sh <<'EOF'
#!/usr/bin/env bash
# Start the bundled Gel server if it is not running yet and block until the
# instance answers its readiness endpoint. Safe to call any number of times.
READY_URL="http://localhost:5656/server/status/ready"
LOG_FILE="/var/log/gel-server.log"

server_ready() {
  curl -fsS -m 5 "${READY_URL}" >/dev/null 2>&1
}

if server_ready; then
  exit 0
fi

mkdir -p /var/log

if ! pgrep -f 'gel-server' >/dev/null 2>&1; then
  echo "starting local Gel server, logging to ${LOG_FILE}"
  if command -v setsid >/dev/null 2>&1; then
    setsid nohup /usr/local/bin/docker-entrypoint.sh server \
      >>"${LOG_FILE}" 2>&1 &
  else
    nohup /usr/local/bin/docker-entrypoint.sh server >>"${LOG_FILE}" 2>&1 &
  fi
fi

for _ in $(seq 1 300); do
  if server_ready; then
    exit 0
  fi
  sleep 1
done

echo "the local Gel server did not become ready within 300s" >&2
tail -n 80 "${LOG_FILE}" >&2 2>/dev/null
exit 1
EOF

RUN chmod 0755 /usr/local/bin/start-gel.sh

# Pre-existing project skeleton.
RUN mkdir -p /home/user/ledger/dbschema && \
    cat >/home/user/ledger/gel.toml <<'EOF'
[instance]
server-version = "7.1"

[project]
schema-dir = "dbschema"
EOF

RUN cat >/home/user/ledger/dbschema/default.gel <<'EOF'
module default {
    type Account {
        required code: str {
            constraint exclusive;
        }
        required balance_cents: int64;
    }
}
EOF

# Bake the initialized data directory (schema + seed data) into the image so the
# server only needs a few seconds to start at task and verification time. If the
# bootstrap cannot be completed during the build, the data directory is left
# empty and start-gel.sh performs the very same bootstrap at runtime.
RUN mkdir -p /var/log "${GEL_SERVER_DATADIR}" && \
    GEL_SERVER_BOOTSTRAP_ONLY=1 /usr/local/bin/docker-entrypoint.sh server \
        >/var/log/gel-bootstrap.log 2>&1 ; \
    if [ -n "$(ls -A "${GEL_SERVER_DATADIR}")" ]; then \
        echo "gel instance bootstrapped during build"; \
    else \
        echo "gel bootstrap deferred to runtime; build log follows"; \
        tail -n 60 /var/log/gel-bootstrap.log; \
        rm -rf "${GEL_SERVER_DATADIR:?}"/* ; \
    fi

LABEL Author="generate-harbor-tasks"
LABEL Description="Gel 7.1 server plus Python tooling, with a seeded Account schema and a bare project skeleton at /home/user/ledger for building a concurrency-safe transfer service."

CMD ["/bin/bash", "-c", "trap : TERM INT; sleep infinity & wait"]

# Container contents:
#   - base image geldata/gel:7.1 (Gel 7.1 server + `gel` CLI, Debian bookworm)
#   - always required apt packages: curl wget ca-certificates git python3
#     python3-pip python3-pytest bash ripgrep (plus unzip jq procps)
#   - always required pip3 packages: pytest-json-ctrf
#   - pip3 packages for the task and the tests: gel==3.1.0, requests, fastapi,
#     uvicorn[standard], flask, gunicorn
#   - /usr/local/bin/start-gel.sh: idempotent local Gel server starter
#   - /gel-bootstrap.d/01-init-ledger.edgeql: creates default::Account and seeds
#     4 RSV-AUDIT-* accounts plus ACC-1 ... ACC-1000 (250000 cents each)
#   - /var/lib/gel-task-data: instance data directory, bootstrapped at build time
#   - /home/user/ledger: project skeleton (gel.toml + dbschema/default.gel)

COPY test_initial_state.py /bootstrap/test_initial_state.py

# Setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
