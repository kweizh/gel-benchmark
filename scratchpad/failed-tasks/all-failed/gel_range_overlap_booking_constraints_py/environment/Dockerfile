FROM ubuntu:24.04

ARG POCHI_VERSION

# --- base tooling, Gel 6 server + Gel CLI, Python client and test tooling -----
# NOTE: python3-pytest (apt) is deliberately NOT installed: the distro pytest is
# too old for pytest-json-ctrf, which crashes the whole run with an INTERNALERROR.
RUN apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl wget ca-certificates git python3 python3-pip bash ripgrep \
        gnupg jq procps tzdata && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    mkdir -p /usr/local/share/keyrings && \
    curl --proto '=https' --tlsv1.2 -sSf \
        -o /usr/local/share/keyrings/gel-keyring.gpg \
        https://packages.geldata.com/keys/gel-keyring.gpg && \
    echo "deb [signed-by=/usr/local/share/keyrings/gel-keyring.gpg] https://packages.geldata.com/apt noble main" \
        > /etc/apt/sources.list.d/gel.list && \
    apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        gel-cli gel-server-6 && \
    curl -fsSL https://getpochi.com/install.sh | bash -s ${POCHI_VERSION} && \
    ln -s /root/.pochi/bin/pochi /usr/local/bin/pochi && \
    pip3 install --break-system-packages pytest pytest-json-ctrf gel==3.1.0 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# --- connection settings for the local instance (no secrets involved) ---------
ENV GEL_DSN="gel://admin@127.0.0.1:5656/main"
ENV GEL_CLIENT_TLS_SECURITY="insecure"

RUN mkdir -p /home/user && chmod 0755 /home/user

# --- unprivileged account that owns the database (gel-server refuses root) ----
RUN (id -u gelsrv >/dev/null 2>&1 || \
        useradd --system --create-home --home-dir /var/lib/gelsrv --shell /bin/bash gelsrv) && \
    mkdir -p /var/lib/geldata/data /var/lib/geldata/run /var/log/gel && \
    chown -R gelsrv:gelsrv /var/lib/geldata /var/log/gel

# --- defensive resolution of the server binary name --------------------------
RUN cat >/usr/local/bin/gel-server-path <<'EOF' && chmod 0755 /usr/local/bin/gel-server-path
#!/bin/bash
# Print the absolute path of the local gel-server binary. The executable name
# varies between base images, install methods and Gel major versions, so try a
# list of candidates instead of hardcoding one.
for candidate in gel-server gel-server-6 gel-server-7; do
    resolved=$(command -v "$candidate" 2>/dev/null)
    if [ -n "$resolved" ]; then
        echo "$resolved"
        exit 0
    fi
done
resolved=$(ls /usr/bin/gel-server* 2>/dev/null | head -n 1)
if [ -n "$resolved" ]; then
    echo "$resolved"
    exit 0
fi
resolved=$(ls /usr/lib/*/gel-server-*/bin/gel-server 2>/dev/null | head -n 1)
if [ -n "$resolved" ]; then
    echo "$resolved"
    exit 0
fi
echo "FATAL: no gel-server binary found (looked for gel-server, gel-server-6, gel-server-7)" >&2
exit 1
EOF

# --- idempotent start helper (explicit runstate dir, non-root server user) ----
RUN cat >/usr/local/bin/gel-start.sh <<'EOF' && chmod 0755 /usr/local/bin/gel-start.sh
#!/bin/bash
# Start the local Gel instance if it is not already accepting queries.
DATA_DIR=/var/lib/geldata/data
RUNSTATE_DIR=/var/lib/geldata/run
LOG_FILE=/var/log/gel/server.log
SRV_USER=gelsrv

mkdir -p "$RUNSTATE_DIR" "$(dirname "$LOG_FILE")" || exit 1
touch "$LOG_FILE" || exit 1
chown "$SRV_USER:$SRV_USER" "$RUNSTATE_DIR" "$LOG_FILE" || exit 1

if gel query "select 1" >/dev/null 2>&1; then
    echo "gel: instance already running"
    exit 0
fi

SERVER_BIN=$(/usr/local/bin/gel-server-path) || exit 1
su "$SRV_USER" -s /bin/bash -c "nohup '$SERVER_BIN' \
    --data-dir='$DATA_DIR' \
    --runstate-dir='$RUNSTATE_DIR' \
    --port=5656 \
    --bind-address=127.0.0.1 \
    --tls-cert-mode=generate_self_signed \
    --default-auth-method=Trust \
    --admin-ui=disabled >>'$LOG_FILE' 2>&1 &"

for _ in $(seq 1 120); do
    if gel query "select 1" >/dev/null 2>&1; then
        echo "gel: instance ready on 127.0.0.1:5656"
        exit 0
    fi
    sleep 1
done

echo "gel: instance did not become ready in time; last log lines:" >&2
tail -n 40 "$LOG_FILE" >&2
exit 1
EOF

# --- initialize the data directory at build time -----------------------------
RUN SERVER_BIN=$(/usr/local/bin/gel-server-path) && \
    su gelsrv -s /bin/bash -c "'$SERVER_BIN' \
        --bootstrap-only \
        --data-dir=/var/lib/geldata/data \
        --runstate-dir=/var/lib/geldata/run \
        --tls-cert-mode=generate_self_signed \
        --default-auth-method=Trust"

# --- half-finished project skeleton ------------------------------------------
RUN mkdir -p /home/user/booking/dbschema

RUN cat >/home/user/booking/gel.toml <<'EOF'
[instance]
server-version = "6.11"
EOF

RUN cat >/home/user/booking/dbschema/default.gel <<'EOF'
module default {
}
EOF

RUN cat >/home/user/booking/README.md <<'EOF'
# booking

Reservation backend for shared resources (meeting rooms, lab benches, ...).

Status: the Gel project is wired up but empty — the schema module has no types
yet and there is no application code.

Intended data model (not implemented yet):

* bookable resources, identified by a short unique code, with a display name and
  a capacity;
* reservations attached to a resource, each covering one temporal interval plus
  the name of whoever booked it, exposing the interval's start, end and length
  in minutes;
* an interval is half-open: it covers its start instant and stops just before
  its end instant, so back-to-back reservations are fine while any shared
  instant is a conflict. Malformed intervals (empty, reversed, open-ended) must
  never reach storage.

The local database is started with `/usr/local/bin/gel-start.sh`; connection
settings come from the environment.
EOF

LABEL Author="generate-harbor-tasks"
LABEL Description="Ubuntu 24.04 with a local Gel 6.11 server, Gel CLI and the Python gel client, plus an empty /home/user/booking Gel project for a resource reservation service."

CMD ["/bin/bash", "-c", "trap : TERM INT; sleep infinity & wait"]

# Container contents:
#   - always required apt packages: curl wget ca-certificates git python3 python3-pip bash ripgrep
#     (python3-pytest intentionally omitted; pytest comes from pip3)
#   - always required pip3 packages: pytest-json-ctrf (plus a modern pytest)
#   - gel-server-6 (Gel 6.11) and gel-cli installed from packages.geldata.com/apt
#   - Python client: gel==3.1.0
#   - /var/lib/geldata/data: bootstrapped Gel data directory owned by the 'gelsrv' user
#   - /var/lib/geldata/run: pre-created, chowned runstate directory
#   - /usr/local/bin/gel-server-path: defensive gel-server binary resolution
#   - /usr/local/bin/gel-start.sh: idempotent local instance starter (port 5656, Trust auth)
#   - /home/user/booking: Gel project skeleton (gel.toml, empty dbschema/default.gel, README.md)

COPY test_initial_state.py /bootstrap/test_initial_state.py

# Setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
