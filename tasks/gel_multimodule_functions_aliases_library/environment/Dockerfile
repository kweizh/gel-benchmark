FROM ubuntu:24.04

ARG POCHI_VERSION

RUN apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl wget ca-certificates git python3 python3-pip python3-pytest bash ripgrep \
        gnupg locales && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    curl -fsSL https://getpochi.com/install.sh | bash -s ${POCHI_VERSION} && \
    ln -s /root/.pochi/bin/pochi /usr/local/bin/pochi && \
    pip3 install --break-system-packages pytest-json-ctrf gel==3.1.0 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Gel 6 server + CLI from the official Gel apt repository.
RUN mkdir -p /usr/local/share/keyrings && \
    curl --proto '=https' --tlsv1.2 -sSf \
        -o /usr/local/share/keyrings/gel-keyring.gpg \
        https://packages.geldata.com/keys/gel-keyring.gpg && \
    echo "deb [signed-by=/usr/local/share/keyrings/gel-keyring.gpg] https://packages.geldata.com/apt $(grep VERSION_CODENAME= /etc/os-release | cut -d= -f2) main" \
        > /etc/apt/sources.list.d/gel.list && \
    apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends gel-6 && \
    ln -s /usr/bin/gel-server-6 /usr/local/bin/gel-server && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# The Gel server refuses to run as root, so it runs as the "gel" system user
# created by the gel-server-6 package. Bootstrap its data directory at build
# time so the first start at runtime is fast and needs no network access.
ENV GEL_DSN="gel://admin@127.0.0.1:5656/main"
ENV GEL_CLIENT_TLS_SECURITY="insecure"

RUN mkdir -p /var/lib/gel/data /var/lib/gel/run /var/log/gel && \
    chown -R gel:gel /var/lib/gel /var/log/gel && \
    su gel -s /bin/bash -c "/usr/bin/gel-server-6 \
        --data-dir=/var/lib/gel/data \
        --runstate-dir=/var/lib/gel/run \
        --bootstrap-only \
        --security=insecure_dev_mode \
        --tls-cert-mode=generate_self_signed \
        --log-level=warn"

RUN printf '%s\n' \
    '#!/bin/bash' \
    '# Idempotently start the local Gel server and wait until it accepts queries.' \
    'export GEL_DSN="${GEL_DSN:-gel://admin@127.0.0.1:5656/main}"' \
    'export GEL_CLIENT_TLS_SECURITY="${GEL_CLIENT_TLS_SECURITY:-insecure}"' \
    'if gel query "select 1" >/dev/null 2>&1; then' \
    '  echo "Gel server is already running on 127.0.0.1:5656."' \
    '  exit 0' \
    'fi' \
    'mkdir -p /var/log/gel /var/lib/gel/run && chown gel:gel /var/log/gel /var/lib/gel/run' \
    'su gel -s /bin/bash -c "setsid nohup /usr/bin/gel-server-6 \' \
    '    --data-dir=/var/lib/gel/data \' \
    '    --runstate-dir=/var/lib/gel/run \' \
    '    --bind-address=127.0.0.1 \' \
    '    --port=5656 \' \
    '    --security=insecure_dev_mode \' \
    '    --tls-cert-mode=generate_self_signed \' \
    '    --compiler-pool-mode=on_demand \' \
    '    --compiler-pool-size=2 \' \
    '    --log-level=warn >>/var/log/gel/server.log 2>&1 &"' \
    'for _ in $(seq 1 180); do' \
    '  if gel query "select 1" >/dev/null 2>&1; then' \
    '    echo "Gel server is up on 127.0.0.1:5656."' \
    '    exit 0' \
    '  fi' \
    '  sleep 1' \
    'done' \
    'echo "Timed out waiting for the local Gel server." >&2' \
    'tail -n 60 /var/log/gel/server.log >&2' \
    'exit 1' \
    > /usr/local/bin/gel-start && \
    chmod 0755 /usr/local/bin/gel-start

RUN mkdir -p /home/user && chmod 0755 /home/user

RUN mkdir -p /home/user/pricing/dbschema && \
    printf '%s\n' \
        '[instance]' \
        'server-version = "6.11"' \
        '' \
        '[project]' \
        'schema-dir = "dbschema"' \
        > /home/user/pricing/gel.toml && \
    printf '%s\n' \
        'module default {' \
        '}' \
        > /home/user/pricing/dbschema/default.gel

LABEL Author="generate-harbor-tasks"
LABEL Description="Gel 6 server and CLI with a bare local project at /home/user/pricing for building a multi-module in-database function/alias library."

CMD ["/bin/bash", "-c", "trap : TERM INT; sleep infinity & wait"]

# Container contents:
#   - always required apt packages: curl wget ca-certificates git python3 python3-pip python3-pytest bash ripgrep
#   - always required pip3 packages: pytest-json-ctrf
#   - gel-6 apt package: Gel 6 server (/usr/bin/gel-server-6, symlinked to /usr/local/bin/gel-server) and the gel CLI
#   - gel==3.1.0 Python client used by the initial- and final-state pytest suites
#   - /var/lib/gel/data: pre-bootstrapped local Gel data directory owned by the "gel" system user
#   - /usr/local/bin/gel-start: idempotent helper that starts the local server and waits for readiness
#   - GEL_DSN / GEL_CLIENT_TLS_SECURITY: connection settings for the CLI and client libraries
#   - /home/user/pricing: bare Gel project (gel.toml + dbschema/default.gel, no migrations yet)

COPY test_initial_state.py /bootstrap/test_initial_state.py

# Setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
