FROM ubuntu:24.04

ARG POCHI_VERSION

RUN apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl wget ca-certificates git python3 python3-pip python3-pytest bash ripgrep \
        gnupg procps && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    mkdir -p /usr/local/share/keyrings && \
    curl --proto '=https' --tlsv1.2 -sSf \
        -o /usr/local/share/keyrings/gel-keyring.gpg \
        https://packages.geldata.com/keys/gel-keyring.gpg && \
    echo "deb [signed-by=/usr/local/share/keyrings/gel-keyring.gpg] https://packages.geldata.com/apt noble main" \
        > /etc/apt/sources.list.d/gel.list && \
    apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends gel-6 && \
    curl -fsSL https://getpochi.com/install.sh | bash -s ${POCHI_VERSION} && \
    ln -s /root/.pochi/bin/pochi /usr/local/bin/pochi && \
    pip3 install --break-system-packages pytest-json-ctrf gel && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Local-only connection settings for the bundled server (no password: the server
# runs in insecure dev mode and is bound to the loopback interface).
ENV GEL_DSN="gel://admin@127.0.0.1:5656/main"
ENV GEL_CLIENT_TLS_SECURITY="insecure"

# Bootstrap the local database cluster at build time so no network access is
# needed later. `gel-server` refuses to run as root, hence the `gel` service user.
RUN mkdir -p /var/lib/gel/6/data /var/lib/gel/run /var/log/gel && \
    chown -R gel:gel /var/lib/gel /var/log/gel && \
    su gel -s /bin/bash -c "/usr/bin/gel-server-6 \
        --data-dir=/var/lib/gel/6/data \
        --runstate-dir=/var/lib/gel/run \
        --security=insecure_dev_mode \
        --tls-cert-mode=generate_self_signed \
        --compiler-pool-size=1 \
        --bootstrap-only"

# Idempotent helper that starts the local server and waits for readiness.
RUN cat >/usr/local/bin/gel-start <<'EOF' && chmod 0755 /usr/local/bin/gel-start
#!/bin/bash
# Start the bundled local Gel server if it is not already accepting
# connections, then block until it is ready. Safe to run repeatedly.
DATA_DIR=/var/lib/gel/6/data
RUN_DIR=/var/lib/gel/run
LOG_FILE=/var/log/gel/server.log

if gel query "select 1" >/dev/null 2>&1; then
    exit 0
fi

mkdir -p "$RUN_DIR" /var/log/gel
chown -R gel:gel /var/lib/gel /var/log/gel 2>/dev/null

su gel -s /bin/bash -c "nohup /usr/bin/gel-server-6 \
    --data-dir=$DATA_DIR \
    --runstate-dir=$RUN_DIR \
    --port=5656 \
    --bind-address=127.0.0.1 \
    --security=insecure_dev_mode \
    --tls-cert-mode=generate_self_signed \
    --compiler-pool-size=1 \
    --admin-ui=disabled >>$LOG_FILE 2>&1 &"

for _ in $(seq 1 180); do
    if gel query "select 1" >/dev/null 2>&1; then
        exit 0
    fi
    sleep 1
done

echo "The local Gel server did not become ready. Last log lines:" >&2
tail -n 40 "$LOG_FILE" >&2
exit 1
EOF

RUN mkdir -p /home/user && chmod 0755 /home/user

RUN mkdir -p /home/user/labreg/dbschema/migrations && \
    cat >/home/user/labreg/gel.toml <<'EOF'
[instance]
server-version = "6"
EOF

RUN cat >/home/user/labreg/dbschema/default.gel <<'EOF'
module default {
}
EOF

LABEL Author="generate-harbor-tasks"
LABEL Description="Ubuntu 24.04 with a bundled local Gel 6 server, the gel CLI, the Python gel client, and an empty Gel project at /home/user/labreg."

CMD ["/bin/bash", "-c", "trap : TERM INT; sleep infinity & wait"]

# Container contents:
#   - always required apt packages: curl wget ca-certificates git python3 python3-pip python3-pytest bash ripgrep
#   - extra apt packages: gnupg (Gel apt repo key), procps (process checks in gel-start)
#   - always required pip3 packages: pytest-json-ctrf
#   - pip3 package `gel`: Python client used by the task and by the tests
#   - gel-6 apt package: local Gel 6 server plus the `gel` CLI, bootstrapped into
#     /var/lib/gel/6/data at build time (no network needed at run time)
#   - /usr/local/bin/gel-start: idempotent local server start/readiness helper
#   - GEL_DSN / GEL_CLIENT_TLS_SECURITY: loopback connection settings for the CLI and clients
#   - /home/user/labreg: Gel project directory with gel.toml, an empty
#     dbschema/default.gel and an empty dbschema/migrations directory

COPY test_initial_state.py /bootstrap/test_initial_state.py

# Setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
