FROM ubuntu:24.04

ARG POCHI_VERSION

RUN apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl wget ca-certificates git python3 python3-pip python3-pytest bash ripgrep gnupg procps && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    curl -fsSL https://getpochi.com/install.sh | bash -s ${POCHI_VERSION} && \
    ln -s /root/.pochi/bin/pochi /usr/local/bin/pochi && \
    pip3 install --break-system-packages pytest-json-ctrf gel==3.1.0 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Gel 6 (server + CLI) from the official Gel APT repository.
RUN mkdir -p /usr/local/share/keyrings && \
    curl --proto '=https' --tlsv1.2 -sSf \
        -o /usr/local/share/keyrings/gel-keyring.gpg \
        https://packages.geldata.com/keys/gel-keyring.gpg && \
    echo "deb [signed-by=/usr/local/share/keyrings/gel-keyring.gpg] https://packages.geldata.com/apt noble main" \
        > /etc/apt/sources.list.d/gel.list && \
    apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends gel-6 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Idempotent helper that brings the local Gel server up (it cannot run as root).
RUN cat > /usr/local/bin/start-gel <<'EOF'
#!/bin/bash
# Idempotently start the local Gel 6 server used by this task.
DATA_DIR=/var/lib/gel/data
RUN_DIR=/var/lib/gel/run
LOG_DIR=/var/log/gel
LOG_FILE="${LOG_DIR}/server.log"
HOST=127.0.0.1
PORT=5656

is_alive() {
  curl -sfk --max-time 3 "https://${HOST}:${PORT}/server/status/alive" >/dev/null 2>&1
}

port_open() {
  (exec 3<>"/dev/tcp/${HOST}/${PORT}") >/dev/null 2>&1
}

if is_alive; then
  exit 0
fi

mkdir -p "${RUN_DIR}" "${LOG_DIR}"
chown -R gel:gel "${RUN_DIR}" "${LOG_DIR}" 2>/dev/null

if ! port_open; then
  su gel -s /bin/bash -c "nohup /usr/bin/gel-server-6 --data-dir=${DATA_DIR} --runstate-dir=${RUN_DIR} --bind-address=${HOST} --port=${PORT} --tls-cert-mode=generate_self_signed --security=insecure_dev_mode --admin-ui=disabled >>${LOG_FILE} 2>&1 &"
fi

for _ in $(seq 1 180); do
  if is_alive; then
    exit 0
  fi
  sleep 1
done

echo "the local Gel server did not become ready; see ${LOG_FILE}" >&2
tail -n 30 "${LOG_FILE}" >&2
exit 1
EOF

RUN chmod 0755 /usr/local/bin/start-gel

# Bootstrap the database cluster while building so the container starts quickly.
RUN mkdir -p /var/lib/gel/data /var/lib/gel/run /var/log/gel && \
    chown -R gel:gel /var/lib/gel /var/log/gel && \
    su gel -s /bin/bash -c "/usr/bin/gel-server-6 --data-dir=/var/lib/gel/data --runstate-dir=/var/lib/gel/run --bootstrap-only --bootstrap-command=\"alter role admin set password := 'museum'\" --tls-cert-mode=generate_self_signed --security=insecure_dev_mode"

RUN mkdir -p /home/user && chmod 0755 /home/user

# Empty Gel project, linked to the local instance.
RUN mkdir -p /home/user/museum/dbschema && \
    printf '[instance]\nserver-version = "6"\n' > /home/user/museum/gel.toml && \
    printf 'module default {\n}\n' > /home/user/museum/dbschema/default.gel && \
    start-gel && \
    gel instance link --non-interactive --trust-tls-cert --dsn "gel://admin:museum@127.0.0.1:5656" museum && \
    cd /home/user/museum && \
    gel project init --link --server-instance museum --non-interactive && \
    gel query "select 1" && \
    pkill -x gel-server; \
    sleep 5; \
    test -f /home/user/museum/gel.toml

LABEL Author="generate-harbor-tasks"
LABEL Description="Gel 6 server and CLI with an empty, linked Gel project at /home/user/museum for schema introspection tooling."

CMD ["/bin/bash", "-c", "trap : TERM INT; sleep infinity & wait"]

# Container contents:
#   - always required apt packages: curl wget ca-certificates git python3 python3-pip python3-pytest bash ripgrep
#   - extra apt packages: gnupg, procps (APT repository key handling and process control)
#   - always required pip3 packages: pytest-json-ctrf
#   - pip3 package gel==3.1.0: the Python client used to query the local instance
#   - gel-6 apt package: Gel 6 server plus the gel CLI (no cloud/SaaS dependency)
#   - /var/lib/gel/data: pre-bootstrapped local database cluster (admin password 'museum')
#   - /usr/local/bin/start-gel: idempotent helper that starts the local server on 127.0.0.1:5656
#   - /home/user/museum: empty Gel project (gel.toml + dbschema/default.gel) linked to instance 'museum'

COPY test_initial_state.py /bootstrap/test_initial_state.py

# Setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
