FROM ubuntu:24.04

ARG POCHI_VERSION

# Connection settings for the local Gel instance started by /usr/local/bin/start-gel.sh.
# The server runs with `--security=insecure_dev_mode` (trust auth, self-signed TLS)
# and only listens on the container loopback interface.
ENV GEL_DSN=gel://admin@127.0.0.1:5656
ENV GEL_CLIENT_TLS_SECURITY=insecure

# NOTE: `python3-pytest` is deliberately NOT installed from apt: the distro build is
# too old for `pytest-json-ctrf`, which then aborts the whole run with an
# INTERNALERROR. pytest comes from pip instead.
RUN apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl wget ca-certificates git python3 python3-pip bash ripgrep gnupg jq procps && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    curl -fsSL https://getpochi.com/install.sh | bash -s ${POCHI_VERSION} && \
    ln -s /root/.pochi/bin/pochi /usr/local/bin/pochi && \
    pip3 install --break-system-packages --no-cache-dir \
        "pytest>=8" pytest-json-ctrf "gel==3.1.0" && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Gel 6 server + bundled Gel CLI from the official package repository.
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

RUN mkdir -p /home/user && chmod 0755 /home/user

# Idempotent launcher for the local Gel server. The server binary name differs
# between images/versions, and it refuses both to run as root and to use the
# default root-owned runstate directory, so both are resolved explicitly here.
RUN mkdir -p /var/lib/gel && cat >/usr/local/bin/start-gel.sh <<'EOF'
#!/bin/bash
# Start the local Gel server (if not already running) and block until it answers
# queries. Safe to call any number of times.
set -uo pipefail

DATA_DIR=/var/lib/gel/task-data
RUN_DIR=/var/lib/gel/task-run
LOG_FILE=/var/log/gel-server.log
SERVER_USER=gel

resolve_server_bin() {
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
    return 1
}

server_ready() {
    gel query 'select 1' >/dev/null 2>&1
}

if server_ready; then
    echo "gel server already running"
    exit 0
fi

SERVER_BIN="$(resolve_server_bin)" || {
    echo "FATAL: no gel-server binary found (tried gel-server, gel-server-6, gel-server-7, /usr/bin/gel-server*)" >&2
    exit 1
}
echo "using server binary: $SERVER_BIN"

mkdir -p "$DATA_DIR" "$RUN_DIR"
touch "$LOG_FILE"
chown -R "$SERVER_USER":"$SERVER_USER" "$DATA_DIR" "$RUN_DIR" "$LOG_FILE"
chmod 700 "$DATA_DIR" "$RUN_DIR"

su "$SERVER_USER" -s /bin/bash -c "nohup '$SERVER_BIN' \
    --data-dir='$DATA_DIR' \
    --runstate-dir='$RUN_DIR' \
    --port=5656 \
    --bind-address=127.0.0.1 \
    --security=insecure_dev_mode \
    --tls-cert-mode=generate_self_signed \
    --admin-ui=disabled \
    --compiler-pool-mode=fixed \
    --compiler-pool-size=2 \
    --max-backend-connections=30 \
    >>'$LOG_FILE' 2>&1 &"

for _ in $(seq 1 240); do
    if server_ready; then
        echo "gel server is ready"
        exit 0
    fi
    sleep 1
done

echo "FATAL: the gel server did not become ready in time; last log lines:" >&2
tail -n 40 "$LOG_FILE" >&2
exit 1
EOF

RUN chmod 0755 /usr/local/bin/start-gel.sh && \
    mkdir -p /home/user/wikiapp/dbschema && \
    printf '[instance]\nserver-version = "6"\n' > /home/user/wikiapp/gel.toml && \
    printf 'module default {\n}\n' > /home/user/wikiapp/dbschema/default.gel

# Bootstrap the database cluster during the build so that starting the server at
# task time is fast, then shut it down cleanly again.
RUN /usr/local/bin/start-gel.sh && \
    cd /home/user/wikiapp && \
    gel query -F json 'select sys::get_version_as_str()' && \
    gel migration status && \
    pkill -TERM -u gel && \
    for _ in $(seq 1 60); do pgrep -u gel >/dev/null || break; sleep 1; done && \
    rm -rf /var/lib/gel/task-run && \
    echo "gel cluster bootstrapped"

LABEL Author="generate-harbor-tasks"
LABEL Description="Ubuntu 24.04 with a local Gel 6 server, the Gel CLI and the Gel Python client, plus an empty Gel project at /home/user/wikiapp for an optimistic-locking document store task."

CMD ["/bin/bash", "-c", "trap : TERM INT; sleep infinity & wait"]

# Container contents:
#   - apt packages: curl wget ca-certificates git python3 python3-pip bash ripgrep gnupg jq procps
#     (python3-pytest intentionally omitted; pytest is installed via pip3)
#   - pip3 packages: pytest, pytest-json-ctrf, gel==3.1.0 (Gel Python client)
#   - Pochi CLI at /usr/local/bin/pochi
#   - Gel 6 server (gel-server-6) and the bundled Gel CLI from packages.geldata.com
#   - /usr/local/bin/start-gel.sh: idempotent local Gel server launcher
#     (data dir /var/lib/gel/task-data, runstate dir /var/lib/gel/task-run, port 5656)
#   - /home/user/wikiapp: Gel project with gel.toml and an empty dbschema/default.gel
#     (no migrations, no solution files - the task must not be pre-solved)

COPY test_initial_state.py /bootstrap/test_initial_state.py

# Setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
