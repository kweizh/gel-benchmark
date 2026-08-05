FROM ubuntu:24.04

ARG POCHI_VERSION

RUN apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl wget ca-certificates git python3 python3-pip python3-pytest bash ripgrep \
        util-linux procps ca-certificates && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    curl -fsSL https://getpochi.com/install.sh | bash -s ${POCHI_VERSION} && \
    ln -s /root/.pochi/bin/pochi /usr/local/bin/pochi && \
    pip3 install --break-system-packages pytest-json-ctrf gel==3.1.0 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN mkdir -p /home/user && chmod 0755 /home/user

# --- Gel CLI (installed system-wide so both root and the server user can use it) ---
RUN curl --proto '=https' --tlsv1.2 -sSf https://www.geldata.com/sh -o /tmp/gel-install.sh && \
    sh /tmp/gel-install.sh -y --no-modify-path && \
    mv /root/.local/bin/gel /usr/local/bin/gel && \
    chmod 0755 /usr/local/bin/gel && \
    rm -f /tmp/gel-install.sh && \
    gel --version

# --- Unprivileged owner for the database server process (it refuses to run as root) ---
RUN useradd --create-home --shell /bin/bash gelsrv && \
    chmod 0755 /home/gelsrv

RUN printf '%s\n' \
        '#!/bin/sh' \
        '# Bring the local Gel instance up (idempotent, no-op when it is already running).' \
        '# stdio is detached so the backgrounded server never holds a caller pipe open.' \
        'INSTANCE=geltask' \
        'LOG=/var/log/gel-instance-start.log' \
        'if [ "$(id -u)" = "0" ]; then' \
        '    setpriv --reuid=gelsrv --regid=gelsrv --init-groups env HOME=/home/gelsrv /usr/local/bin/gel instance start -I "$INSTANCE" >>"$LOG" 2>&1' \
        'else' \
        '    /usr/local/bin/gel instance start -I "$INSTANCE" >>"$LOG" 2>&1' \
        'fi' \
        'status=$?' \
        'if [ "$status" -ne 0 ]; then cat "$LOG"; fi' \
        'exit "$status"' \
        > /usr/local/bin/gel-start-instance && \
    chmod 0755 /usr/local/bin/gel-start-instance

# --- Create the local Gel 6.11 instance and publish its credentials system-wide ---
RUN setpriv --reuid=gelsrv --regid=gelsrv --init-groups env HOME=/home/gelsrv gel server install --version 6 && \
    setpriv --reuid=gelsrv --regid=gelsrv --init-groups env HOME=/home/gelsrv gel instance create geltask --version 6 && \
    mkdir -p /root/.config/edgedb/credentials /root/.config/gel/credentials && \
    cp "$(find /home/gelsrv/.config -path '*/credentials/geltask.json' | head -n 1)" /root/.config/edgedb/credentials/geltask.json && \
    cp /root/.config/edgedb/credentials/geltask.json /root/.config/gel/credentials/geltask.json

# --- Seed the Gel project: gel.toml, the Supplier-only schema and its single migration ---
RUN mkdir -p /home/user/catalog/dbschema && \
    printf '[instance]\nserver-version = "6.11"\n' > /home/user/catalog/gel.toml && \
    printf '%s\n' \
        'module default {' \
        '  type Supplier {' \
        '    required code: str {' \
        '      constraint exclusive;' \
        '    }' \
        '    required name: str;' \
        '  }' \
        '}' \
        > /home/user/catalog/dbschema/default.gel

RUN gel-start-instance && \
    cd /home/user/catalog && \
    gel project init --link --server-instance geltask --non-interactive && \
    gel migration create --non-interactive && \
    gel migrate && \
    gel migration status

# --- Shut the server down cleanly so no stale pid/lock/socket state is baked into the image ---
RUN setpriv --reuid=gelsrv --regid=gelsrv --init-groups env HOME=/home/gelsrv \
        gel instance stop -I geltask; \
    sleep 2; \
    rm -rf /home/gelsrv/.cache/edgedb/run/geltask; \
    rm -f /home/gelsrv/.local/share/edgedb/data/geltask/postmaster.pid; \
    rm -f /var/log/gel-instance-start.log; \
    true

LABEL Author="generate-harbor-tasks"
LABEL Description="Gel 6.11 local instance plus a linked Gel project at /home/user/catalog holding a Supplier-only schema, for building an idempotent bulk upsert ingestion pipeline with the Python client."

CMD ["/bin/bash", "-c", "trap : TERM INT; sleep infinity & wait"]

# Container contents:
#   - always required apt packages: curl wget ca-certificates git python3 python3-pip python3-pytest bash ripgrep
#   - always required pip3 packages: pytest-json-ctrf
#   - gel Python client 3.1.0 (pip3) used by the task and by both pytest suites
#   - Gel CLI at /usr/local/bin/gel and the Gel 6.11 portable server
#   - unprivileged user `gelsrv` owning the local instance `geltask` (the server cannot run as root)
#   - /usr/local/bin/gel-start-instance: idempotent helper that brings the instance up
#   - /root/.config/{edgedb,gel}/credentials/geltask.json: instance credentials for the root user
#   - /home/user/catalog: Gel project linked to `geltask`, with dbschema/default.gel (Supplier only)
#     and exactly one applied migration under dbschema/migrations

COPY test_initial_state.py /bootstrap/test_initial_state.py

# Setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
