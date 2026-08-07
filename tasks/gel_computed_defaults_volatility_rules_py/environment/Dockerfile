FROM ubuntu:24.04

ARG POCHI_VERSION

# Base tooling. NOTE: python3-pytest is deliberately NOT installed from apt -- the
# distro pytest is too old for pytest-json-ctrf and would abort the whole suite with
# an INTERNALERROR. pytest comes from pip instead.
RUN apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl wget ca-certificates gnupg git python3 python3-pip bash ripgrep \
        util-linux procps && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    curl -fsSL https://getpochi.com/install.sh | bash -s ${POCHI_VERSION} && \
    ln -s /root/.pochi/bin/pochi /usr/local/bin/pochi && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Gel 6.11 server + Gel CLI from the official apt repository.
RUN curl -fsSL https://packages.geldata.com/keys/gel-keyring.gpg \
        -o /usr/share/keyrings/gel-keyring.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/gel-keyring.gpg] https://packages.geldata.com/apt noble main" \
        > /etc/apt/sources.list.d/gel.list && \
    apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        gel-server-6 gel-cli && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Python tooling: Gel async/sync client plus the test harness.
RUN pip3 install --break-system-packages --no-cache-dir \
        "pytest>=8" pytest-json-ctrf gel

# Unprivileged account for the database process (gel-server refuses to run as root)
# and a pre-bootstrapped data directory so the server starts quickly at runtime.
# An explicit --runstate-dir is mandatory: the default lives under root-owned /run.
RUN (id -u gel >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/gel --shell /bin/bash gel) && \
    mkdir -p /var/lib/gel/data /var/lib/gel/run && \
    chown -R gel:gel /var/lib/gel && \
    SERVER_BIN="$(command -v gel-server || command -v gel-server-6 || ls /usr/bin/gel-server* 2>/dev/null | head -n 1)" && \
    if [ -z "$SERVER_BIN" ]; then echo "FATAL: no gel-server binary found" >&2; exit 1; fi && \
    echo "bootstrapping with $SERVER_BIN" && \
    runuser -u gel -- "$SERVER_BIN" \
        --data-dir=/var/lib/gel/data \
        --runstate-dir=/var/lib/gel/run \
        --bootstrap-only \
        --security=insecure_dev_mode \
        --default-auth-method=Trust \
        --tls-cert-mode=generate_self_signed \
        --compiler-pool-size=2 \
        --max-backend-connections=20 && \
    chown -R gel:gel /var/lib/gel

# Idempotent launcher for the local server (resolves the binary defensively).
RUN cat >/usr/local/bin/gel-start.sh <<'EOF'
#!/bin/bash
# Start the local Gel server if it is not already accepting connections.
# Safe to call any number of times; blocks until the server is reachable.
GEL_HOST="${GEL_HOST:-127.0.0.1}"
GEL_PORT="${GEL_PORT:-5656}"
GEL_USER="${GEL_USER:-admin}"
GEL_BRANCH="${GEL_BRANCH:-main}"
DATA_DIR=/var/lib/gel/data
RUN_DIR=/var/lib/gel/run
LOG_FILE=/var/log/gel-server.log

is_ready() {
    gel -H "$GEL_HOST" -P "$GEL_PORT" --user "$GEL_USER" --branch "$GEL_BRANCH" \
        --tls-security insecure query "select 1" >/dev/null 2>&1
}

if is_ready; then
    echo "gel server already running on ${GEL_HOST}:${GEL_PORT}"
    exit 0
fi

SERVER_BIN="$(command -v gel-server || command -v gel-server-6 || ls /usr/bin/gel-server* 2>/dev/null | head -n 1)"
if [ -z "$SERVER_BIN" ]; then
    echo "FATAL: no gel-server binary found (tried gel-server, gel-server-6, /usr/bin/gel-server*)" >&2
    exit 1
fi

mkdir -p "$DATA_DIR" "$RUN_DIR"
chown -R gel:gel /var/lib/gel
touch "$LOG_FILE"

nohup runuser -u gel -- "$SERVER_BIN" \
    --data-dir="$DATA_DIR" \
    --runstate-dir="$RUN_DIR" \
    --port="$GEL_PORT" \
    --bind-address=127.0.0.1 \
    --security=insecure_dev_mode \
    --default-auth-method=Trust \
    --tls-cert-mode=generate_self_signed \
    --admin-ui=disabled \
    --compiler-pool-size=2 \
    --max-backend-connections=20 \
    >>"$LOG_FILE" 2>&1 &

for _ in $(seq 1 150); do
    if is_ready; then
        echo "gel server ready on ${GEL_HOST}:${GEL_PORT}"
        exit 0
    fi
    sleep 2
done

echo "FATAL: gel server did not become ready in time" >&2
tail -n 80 "$LOG_FILE" >&2
exit 1
EOF

RUN chmod 0755 /usr/local/bin/gel-start.sh

RUN mkdir -p /home/user && chmod 0755 /home/user

# Empty Gel project skeleton: manifest + an empty `module default` schema file.
RUN mkdir -p /home/user/gelproj/dbschema && \
    printf '[instance]\nserver-version = "6.11"\n' > /home/user/gelproj/gel.toml && \
    printf 'module default {\n}\n' > /home/user/gelproj/dbschema/default.gel

ENV GEL_HOST=127.0.0.1
ENV GEL_PORT=5656
ENV GEL_USER=admin
ENV GEL_BRANCH=main
ENV GEL_CLIENT_TLS_SECURITY=insecure

LABEL Author="generate-harbor-tasks"
LABEL Description="Local Gel 6.11 server, gel CLI and Gel Python client with an empty Gel project skeleton at /home/user/gelproj."

CMD ["/bin/bash", "-c", "trap : TERM INT; sleep infinity & wait"]

# Container contents:
#   - apt packages: curl wget ca-certificates gnupg git python3 python3-pip bash ripgrep util-linux procps
#     (python3-pytest intentionally omitted; pytest is installed from pip)
#   - pip3 packages: pytest>=8, pytest-json-ctrf, gel (Gel Python client)
#   - gel-server-6 (Gel 6.11) and gel-cli from packages.geldata.com
#   - /var/lib/gel/data: pre-bootstrapped database cluster owned by the `gel` system user
#   - /var/lib/gel/run: explicit runstate directory (never rely on the root-owned default)
#   - /usr/local/bin/gel-start.sh: idempotent, blocking local server launcher
#   - /home/user/gelproj: empty Gel project (gel.toml + dbschema/default.gel)
#   - GEL_* connection environment variables for the local, TLS-insecure dev server

COPY test_initial_state.py /bootstrap/test_initial_state.py

# Setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
