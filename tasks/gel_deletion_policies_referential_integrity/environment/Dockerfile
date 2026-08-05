FROM ubuntu:24.04

ARG POCHI_VERSION
ARG GEL_MAJOR_VERSION=6

ENV DEBIAN_FRONTEND=noninteractive

# Base tooling required by every Harbor task, plus the Gel server/CLI packages
# from the official Gel apt repository and the Gel Python client.
RUN apt-get update -y && \
    apt-get install -y --no-install-recommends \
        curl wget ca-certificates git python3 python3-pip python3-pytest bash ripgrep \
        gnupg apt-transport-https procps tzdata && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    mkdir -p /usr/local/share/keyrings && \
    curl --proto '=https' --tlsv1.2 -sSf \
        -o /usr/local/share/keyrings/gel-keyring.gpg \
        https://packages.geldata.com/keys/gel-keyring.gpg && \
    echo "deb [signed-by=/usr/local/share/keyrings/gel-keyring.gpg] https://packages.geldata.com/apt noble main" \
        > /etc/apt/sources.list.d/gel.list && \
    apt-get update -y && \
    apt-get install -y --no-install-recommends \
        gel-server-${GEL_MAJOR_VERSION} gel-cli && \
    curl -fsSL https://getpochi.com/install.sh | bash -s ${POCHI_VERSION} && \
    ln -s /root/.pochi/bin/pochi /usr/local/bin/pochi && \
    pip3 install --break-system-packages pytest-json-ctrf "gel==3.1.0" && \
    ln -sf /usr/bin/gel /usr/local/bin/gel && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Local-only connection settings for both the CLI and the Python client.
ENV GEL_DSN=gel://admin@127.0.0.1:5656/main
ENV GEL_CLIENT_TLS_SECURITY=insecure
ENV GEL_RUN_VERSION_CHECK=never
ENV GEL_SERVER_DATADIR=/var/lib/gel/data

# The Gel server refuses to run as root, so it runs as the unprivileged
# "gel" system user. The data directory is bootstrapped at build time so that
# starting the server later only takes a few seconds.
RUN (id -u gel >/dev/null 2>&1 || useradd --system --shell /bin/bash --home-dir /var/lib/gel gel) && \
    mkdir -p /var/lib/gel/data /var/lib/gel/run /var/log/gel && \
    chown -R gel:gel /var/lib/gel /var/log/gel && \
    runuser -u gel -- /usr/bin/gel-server-${GEL_MAJOR_VERSION} \
        --data-dir=/var/lib/gel/data \
        --runstate-dir=/var/lib/gel/run \
        --security=insecure_dev_mode \
        --default-auth-method=Trust \
        --tls-cert-mode=generate_self_signed \
        --bootstrap-only

# Helper that starts the local Gel server in the background (idempotent) and
# returns only once the server accepts connections.
RUN printf '%s\n' \
    '#!/bin/bash' \
    '# Start the local Gel server in the background and wait until it is ready.' \
    'LOGFILE=/var/log/gel/server.log' \
    'GEL_BIN=/usr/bin/gel-server-'"${GEL_MAJOR_VERSION}" \
    'if gel query --wait-until-available=1s "select 1" >/dev/null 2>&1; then' \
    '  echo "Gel server already running."' \
    '  exit 0' \
    'fi' \
    'mkdir -p /var/lib/gel/run /var/log/gel && chown -R gel:gel /var/lib/gel /var/log/gel' \
    'setsid runuser -u gel -- "$GEL_BIN" \' \
    '  --data-dir=/var/lib/gel/data \' \
    '  --runstate-dir=/var/lib/gel/run \' \
    '  --port=5656 \' \
    '  --bind-address=127.0.0.1 \' \
    '  --security=insecure_dev_mode \' \
    '  --default-auth-method=Trust \' \
    '  --tls-cert-mode=generate_self_signed \' \
    '  >>"$LOGFILE" 2>&1 &' \
    'for _ in $(seq 1 180); do' \
    '  if gel query --wait-until-available=1s "select 1" >/dev/null 2>&1; then' \
    '    echo "Gel server is ready on 127.0.0.1:5656."' \
    '    exit 0' \
    '  fi' \
    '  sleep 1' \
    'done' \
    'echo "Gel server failed to become ready." >&2' \
    'tail -n 50 "$LOGFILE" >&2' \
    'exit 1' \
    > /usr/local/bin/gel-serve && chmod 0755 /usr/local/bin/gel-serve

RUN mkdir -p /home/user && chmod 0755 /home/user

# Gel project skeleton: manifest plus an empty default module. The schema,
# the migration history and the purge routine are up to the executor.
RUN mkdir -p /home/user/docmgr/dbschema && \
    printf '%s\n' '[instance]' 'server-version = "6.11"' > /home/user/docmgr/gel.toml && \
    printf '%s\n' 'module default {' '}' > /home/user/docmgr/dbschema/default.gel

WORKDIR /home/user/docmgr

LABEL Author="generate-harbor-tasks"
LABEL Description="Ubuntu 24.04 with a local Gel 6 server, the Gel CLI, the Gel Python client and an empty Gel project skeleton at /home/user/docmgr."

CMD ["/bin/bash", "-c", "trap : TERM INT; sleep infinity & wait"]

# Container contents:
#   - always required apt packages: curl wget ca-certificates git python3 python3-pip python3-pytest bash ripgrep
#   - always required pip3 packages: pytest-json-ctrf
#   - gel-server-6 + gel-cli from https://packages.geldata.com/apt (local server, no cloud)
#   - gel==3.1.0 Python client for the tests and the purge routine
#   - /var/lib/gel/data: Gel data directory, bootstrapped at build time, owned by the "gel" user
#   - /usr/local/bin/gel-serve: helper that starts the local server and waits for readiness
#   - /home/user/docmgr: Gel project skeleton (gel.toml + dbschema/default.gel)

COPY test_initial_state.py /bootstrap/test_initial_state.py

# Setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
