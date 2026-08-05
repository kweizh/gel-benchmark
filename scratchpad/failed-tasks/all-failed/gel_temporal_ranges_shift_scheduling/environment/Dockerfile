FROM ubuntu:24.04

ARG POCHI_VERSION

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update -y && \
    apt-get install -y --no-install-recommends \
        curl wget ca-certificates git python3 python3-pip python3-pytest bash ripgrep \
        gnupg procps psmisc tzdata jq && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    mkdir -p /usr/local/share/keyrings && \
    curl --proto '=https' --tlsv1.2 -sSf \
        -o /usr/local/share/keyrings/gel-keyring.gpg \
        https://packages.geldata.com/keys/gel-keyring.gpg && \
    echo "deb [signed-by=/usr/local/share/keyrings/gel-keyring.gpg] https://packages.geldata.com/apt noble main" \
        > /etc/apt/sources.list.d/gel.list && \
    apt-get update -y && \
    apt-get install -y --no-install-recommends gel-6 && \
    curl -fsSL https://getpochi.com/install.sh | bash -s ${POCHI_VERSION} && \
    ln -s /root/.pochi/bin/pochi /usr/local/bin/pochi && \
    pip3 install --break-system-packages pytest-json-ctrf gel==3.1.0 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN mkdir -p /home/user && chmod 0755 /home/user

# Helper that starts the local Gel server. The server refuses to run as root, so
# it is launched as the unprivileged `gel` user created by the gel-6 package.
# Liveness is probed on the TCP port rather than by matching process names, so
# that an enclosing shell command mentioning "gel-server" cannot confuse it.
RUN printf '%s\n' \
    '#!/bin/bash' \
    '# Start the local Gel server and block until it accepts connections.' \
    '# Safe to call repeatedly.' \
    'DATA_DIR=/var/lib/gel/6/data' \
    'RUN_DIR=/run/gel' \
    'LOG_FILE=/var/log/gel-server.log' \
    'port_open() { (exec 3<>/dev/tcp/127.0.0.1/5656) >/dev/null 2>&1; }' \
    'ready() { curl -sk --max-time 5 https://127.0.0.1:5656/server/status/ready >/dev/null 2>&1; }' \
    'mkdir -p "$DATA_DIR" "$RUN_DIR" /var/log' \
    'chown -R gel:gel "$DATA_DIR" "$RUN_DIR"' \
    'touch "$LOG_FILE" && chmod 666 "$LOG_FILE"' \
    'if ready; then' \
    '  echo "Gel server already running on 127.0.0.1:5656"' \
    '  exit 0' \
    'fi' \
    'if ! port_open; then' \
    '  su gel -s /bin/bash -c "nohup gel-server-6 --data-dir=$DATA_DIR --runstate-dir=$RUN_DIR --security=insecure_dev_mode --default-auth-method=Trust --bind-address=127.0.0.1 --port=5656 --admin-ui=disabled --compiler-pool-size=2 --max-backend-connections=20 >> $LOG_FILE 2>&1 &"' \
    'fi' \
    'for _ in $(seq 1 300); do' \
    '  if ready; then' \
    '    echo "Gel server ready on 127.0.0.1:5656"' \
    '    exit 0' \
    '  fi' \
    '  sleep 1' \
    'done' \
    'echo "Gel server did not become ready in time; last log lines:" >&2' \
    'tail -n 60 "$LOG_FILE" >&2' \
    'exit 1' \
    > /usr/local/bin/gel-start && chmod 0755 /usr/local/bin/gel-start

RUN printf '%s\n' \
    '#!/bin/bash' \
    '# Gracefully stop the local Gel server.' \
    'pkill -TERM -u gel >/dev/null 2>&1' \
    'for _ in $(seq 1 90); do' \
    '  pgrep -u gel >/dev/null 2>&1 || exit 0' \
    '  sleep 1' \
    'done' \
    'pkill -KILL -u gel >/dev/null 2>&1' \
    'exit 0' \
    > /usr/local/bin/gel-stop && chmod 0755 /usr/local/bin/gel-stop

# Empty Gel project skeleton. `gel-server-6 --version` is deliberately resolved
# in its own layer so the bootstrap layer below never mentions the server binary.
RUN mkdir -p /home/user/shiftops/dbschema && \
    printf '[instance]\nserver-version = "%s"\n' \
        "$(gel-server-6 --version | sed -E 's/^[^0-9]*([0-9]+\.[0-9]+).*/\1/')" \
        > /home/user/shiftops/gel.toml && \
    printf 'module default {\n}\n' > /home/user/shiftops/dbschema/default.gel

ENV GEL_INSTANCE=shiftdb

# Bootstrap the local instance, register it under the alias `shiftdb`, and link
# the project skeleton to it.
RUN gel-start && \
    gel instance link \
        --dsn 'gel://admin@127.0.0.1:5656/main' \
        --non-interactive --trust-tls-cert shiftdb && \
    cd /home/user/shiftops && \
    gel project init --link --server-instance shiftdb --non-interactive && \
    gel query 'select 1' && \
    gel-stop

LABEL Author="generate-harbor-tasks"
LABEL Description="Ubuntu 24.04 with a local Gel 6 server, Gel CLI, the gel Python client, and an empty linked Gel project at /home/user/shiftops for the shift-scheduling task."

CMD ["/bin/bash", "-c", "trap : TERM INT; sleep infinity & wait"]

# Container contents:
#   - always required apt packages: curl wget ca-certificates git python3 python3-pip python3-pytest bash ripgrep
#   - always required pip3 packages: pytest-json-ctrf
#   - gel-6 apt package from packages.geldata.com (gel-server-6 6.x + Gel CLI 7.x)
#   - gel==3.1.0 Python client used by the task module and the verifier
#   - /usr/local/bin/gel-start, /usr/local/bin/gel-stop: start/stop the local Gel server
#   - local Gel instance bootstrapped in /var/lib/gel/6/data and linked as `shiftdb`
#   - GEL_INSTANCE=shiftdb so the CLI and the Python client resolve the connection
#   - /home/user/shiftops: linked Gel project with an empty dbschema/default.gel

COPY test_initial_state.py /bootstrap/test_initial_state.py

# Setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
