FROM ubuntu:24.04

ARG POCHI_VERSION

# Base tooling, the Gel 7 server + CLI from the official apt repository, and the
# Python test tooling. `python3-pytest` is deliberately NOT installed: the distro
# pytest is too old for pytest-json-ctrf and aborts the whole run.
RUN apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl wget ca-certificates git python3 python3-pip bash ripgrep jq gnupg procps && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    mkdir -p /usr/local/share/keyrings && \
    curl --proto '=https' --tlsv1.2 -sSf \
        -o /usr/local/share/keyrings/gel-keyring.gpg \
        https://packages.geldata.com/keys/gel-keyring.gpg && \
    echo 'deb [signed-by=/usr/local/share/keyrings/gel-keyring.gpg] https://packages.geldata.com/apt noble main' \
        > /etc/apt/sources.list.d/gel.list && \
    apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends gel-7 && \
    curl -fsSL https://getpochi.com/install.sh | bash -s ${POCHI_VERSION} && \
    ln -s /root/.pochi/bin/pochi /usr/local/bin/pochi && \
    pip3 install --break-system-packages pytest pytest-json-ctrf && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN mkdir -p /home/user && chmod 0755 /home/user

# Idempotent helper that boots the local Gel server as the unprivileged `gel`
# user with an explicit, pre-chowned runstate directory.
RUN cat > /usr/local/bin/gel-start <<'GELSTART'
#!/usr/bin/env bash
# Start the local Gel server unless it is already accepting connections.
# Idempotent: safe to call any number of times.

GEL_DATA_DIR="${GEL_DATA_DIR:-/var/lib/gel/data}"
GEL_RUN_DIR="${GEL_RUN_DIR:-/var/lib/gel/run}"
GEL_LOG_FILE="${GEL_LOG_FILE:-/var/log/gel-server.log}"
GEL_SRV_USER="${GEL_SRV_USER:-gel}"
GEL_SRV_PORT="${GEL_SRV_PORT:-5656}"

probe() {
    gel query --host 127.0.0.1 --port "${GEL_SRV_PORT}" --user admin \
        --tls-security insecure --branch main "select 1" >/dev/null 2>&1
}

if probe; then
    exit 0
fi

# The server binary name differs between images and major versions, so resolve
# it defensively instead of hardcoding a single name.
SERVER_BIN=""
for candidate in gel-server gel-server-7 gel-server-6; do
    if command -v "${candidate}" >/dev/null 2>&1; then
        SERVER_BIN="$(command -v "${candidate}")"
        break
    fi
done
if [ -z "${SERVER_BIN}" ]; then
    SERVER_BIN="$(ls /usr/bin/gel-server* 2>/dev/null | head -n 1)"
fi
if [ -z "${SERVER_BIN}" ]; then
    echo "gel-start: could not find a gel-server binary" >&2
    exit 1
fi

mkdir -p "${GEL_DATA_DIR}" "${GEL_RUN_DIR}" "$(dirname "${GEL_LOG_FILE}")"
touch "${GEL_LOG_FILE}"
chown -R "${GEL_SRV_USER}:${GEL_SRV_USER}" "${GEL_DATA_DIR}" "${GEL_RUN_DIR}" "${GEL_LOG_FILE}"

# gel-server refuses to run as root, and the default runstate directory under
# /run is root-owned, hence the explicit --runstate-dir.
su "${GEL_SRV_USER}" -s /bin/bash -c "'${SERVER_BIN}' \
    --data-dir='${GEL_DATA_DIR}' \
    --runstate-dir='${GEL_RUN_DIR}' \
    --port='${GEL_SRV_PORT}' \
    --bind-address=127.0.0.1 \
    --security=insecure_dev_mode \
    --default-auth-method=Trust \
    --tls-cert-mode=generate_self_signed \
    --admin-ui=disabled \
    >>'${GEL_LOG_FILE}' 2>&1 &"

for _ in $(seq 1 240); do
    if probe; then
        exit 0
    fi
    sleep 2
done

echo "gel-start: the Gel server did not become ready in time" >&2
tail -n 60 "${GEL_LOG_FILE}" >&2
exit 1
GELSTART

RUN chmod 0755 /usr/local/bin/gel-start && mkdir -p /opt/gel-bootstrap

# Loose starting schema of the logistics project.
RUN cat > /opt/gel-bootstrap/default.gel <<'SDL'
module default {
  type Warehouse {
    required code: str {
      constraint exclusive;
    }
    required name: str;
  }

  type Shipment {
    required seq: int64 {
      constraint exclusive;
    }
    tracking: str;
    weight_kg: float64;
    status: str;
    origin_code: str;
    origin: Warehouse;
  }
}
SDL

# Pre-incident dataset (dirty on purpose): it is dumped to
# backups/pre_incident.dump before the "botched maintenance script" runs.
RUN cat > /opt/gel-bootstrap/seed.edgeql <<'SEED'
insert Warehouse { code := 'WH-ALPHA', name := 'Alpha Depot' };
insert Warehouse { code := '  wh-bravo ', name := 'Bravo Depot' };
insert Warehouse { code := 'wh-charlie', name := 'Charlie Depot' };
insert Warehouse { code := 'WH-DELTA', name := 'Delta Depot' };
insert Warehouse { code := 'WH-ECHO', name := 'Echo Depot' };

with w := assert_single((select Warehouse filter .code = 'WH-ALPHA'))
insert Shipment { seq := 1, tracking := 'GL-100001', weight_kg := 12.5, status := 'delivered', origin_code := 'WH-ALPHA', origin := w };
with w := assert_single((select Warehouse filter .code = 'WH-ALPHA'))
insert Shipment { seq := 2, tracking := 'GL-100002', weight_kg := 3.25, status := 'pending', origin_code := 'WH-ALPHA', origin := w };
with w := assert_single((select Warehouse filter .code = '  wh-bravo '))
insert Shipment { seq := 3, tracking := 'GL-100003', weight_kg := 7.0, status := 'in_transit', origin_code := 'WH-BRAVO', origin := w };
with w := assert_single((select Warehouse filter .code = 'wh-charlie'))
insert Shipment { seq := 4, tracking := 'GL-100004', weight_kg := 1.75, status := 'returned', origin_code := 'WH-CHARLIE', origin := w };
with w := assert_single((select Warehouse filter .code = 'WH-ECHO'))
insert Shipment { seq := 5, tracking := 'GL-100005', weight_kg := 22.0, status := 'delivered', origin_code := 'wh-zulu', origin := w };
with w := assert_single((select Warehouse filter .code = 'WH-ALPHA'))
insert Shipment { seq := 6, tracking := ' gl-100006 ', weight_kg := 5.5, status := 'delivered', origin_code := 'WH-ALPHA', origin := w };
with w := assert_single((select Warehouse filter .code = '  wh-bravo '))
insert Shipment { seq := 7, tracking := 'gl 100007', weight_kg := 8.0, status := 'pending', origin_code := 'wh-bravo ', origin := w };
with w := assert_single((select Warehouse filter .code = 'wh-charlie'))
insert Shipment { seq := 8, tracking := "  GL-100008\t", weight_kg := 2.25, status := 'in_transit', origin_code := 'WH-CHARLIE', origin := w };
with w := assert_single((select Warehouse filter .code = 'WH-ALPHA'))
insert Shipment { seq := 9, tracking := 'GL-100009', weight_kg := 4.0, status := 'delivered', origin_code := 'WH-ALPHA', origin := w };
with w := assert_single((select Warehouse filter .code = 'WH-ALPHA'))
insert Shipment { seq := 10, tracking := ' gl-100009 ', weight_kg := 6.0, status := 'pending', origin_code := 'WH-ALPHA', origin := w };
with w := assert_single((select Warehouse filter .code = 'WH-ECHO'))
insert Shipment { seq := 11, tracking := 'GL-100011', weight_kg := 0.0, status := 'pending', origin_code := 'WH-ECHO', origin := w };
with w := assert_single((select Warehouse filter .code = 'WH-ECHO'))
insert Shipment { seq := 12, tracking := 'gl-100011', weight_kg := 9.5, status := 'delivered', origin_code := 'WH-ECHO', origin := w };
with w := assert_single((select Warehouse filter .code = 'WH-ALPHA'))
insert Shipment { seq := 13, tracking := 'GL-100013', weight_kg := 3.0, status := 'pending', origin_code := 'WH-ALPHA', origin := w };
with w := assert_single((select Warehouse filter .code = 'WH-ALPHA'))
insert Shipment { seq := 14, tracking := 'GL-100013', weight_kg := 3.0, status := 'pending', origin_code := 'WH-ALPHA', origin := w };
with w := assert_single((select Warehouse filter .code = '  wh-bravo '))
insert Shipment { seq := 15, tracking := 'GL-100015', weight_kg := -4.5, status := 'delivered', origin_code := 'WH-BRAVO', origin := w };
with w := assert_single((select Warehouse filter .code = 'wh-charlie'))
insert Shipment { seq := 16, tracking := 'GL-100016', weight_kg := -0.25, status := 'returned', origin_code := 'WH-CHARLIE', origin := w };
with w := assert_single((select Warehouse filter .code = 'WH-ALPHA'))
insert Shipment { seq := 17, tracking := 'GL-100017', status := 'pending', origin_code := 'WH-ALPHA', origin := w };
with w := assert_single((select Warehouse filter .code = 'WH-ALPHA'))
insert Shipment { seq := 18, tracking := 'GL-100018', weight_kg := 0.0, status := 'delivered', origin_code := 'WH-ALPHA', origin := w };
with w := assert_single((select Warehouse filter .code = '  wh-bravo '))
insert Shipment { seq := 19, tracking := 'GL-100019', weight_kg := 1.5, status := 'TRANSIT', origin_code := 'WH-BRAVO', origin := w };
with w := assert_single((select Warehouse filter .code = '  wh-bravo '))
insert Shipment { seq := 20, tracking := 'GL-100020', weight_kg := 2.5, status := 'In-Transit', origin_code := 'WH-BRAVO', origin := w };
with w := assert_single((select Warehouse filter .code = 'wh-charlie'))
insert Shipment { seq := 21, tracking := 'GL-100021', weight_kg := 3.5, status := 'Complete', origin_code := 'WH-CHARLIE', origin := w };
with w := assert_single((select Warehouse filter .code = 'wh-charlie'))
insert Shipment { seq := 22, tracking := 'GL-100022', weight_kg := 4.5, status := 'done', origin_code := 'WH-CHARLIE', origin := w };
with w := assert_single((select Warehouse filter .code = 'WH-ALPHA'))
insert Shipment { seq := 23, tracking := 'GL-100023', weight_kg := 5.5, status := 'awaiting', origin_code := 'WH-ALPHA', origin := w };
with w := assert_single((select Warehouse filter .code = 'WH-ECHO'))
insert Shipment { seq := 24, tracking := 'GL-100024', weight_kg := 6.5, status := 'RETURNED_TO_SENDER', origin_code := 'WH-ECHO', origin := w };
with w := assert_single((select Warehouse filter .code = 'WH-ECHO'))
insert Shipment { seq := 25, tracking := 'GL-100025', weight_kg := 7.5, origin_code := 'WH-ECHO', origin := w };
with w := assert_single((select Warehouse filter .code = 'WH-ALPHA'))
insert Shipment { seq := 26, tracking := 'GL-100026', weight_kg := 8.5, status := ' Delivered ', origin_code := 'WH-ALPHA', origin := w };
insert Shipment { seq := 27, tracking := 'GL-100027', weight_kg := 9.0, status := 'pending', origin_code := ' wh-charlie ' };
insert Shipment { seq := 28, tracking := 'GL-100028', weight_kg := 10.0, status := 'delivered', origin_code := 'WH-ECHO' };
insert Shipment { seq := 29, tracking := 'GL-100029', weight_kg := 11.0, status := 'pending', origin_code := 'WH-ZULU' };
insert Shipment { seq := 30, tracking := 'GL-100030', weight_kg := 12.0, status := 'returned' };
SEED

# The "botched maintenance script" that damaged the working branch after the
# backup was taken.
RUN cat > /opt/gel-bootstrap/damage.edgeql <<'DAMAGE'
delete Shipment filter .seq in {2, 6, 13, 21, 24, 27};
update Shipment filter .origin.code = 'WH-ECHO' set { origin := {} };
delete Warehouse filter .code = 'WH-ECHO';
update Shipment filter .seq in {3, 8} set { origin := {} };
DAMAGE

RUN cat > /opt/gel-bootstrap/credentials.json <<'CREDS'
{
  "host": "127.0.0.1",
  "port": 5656,
  "user": "admin",
  "tls_security": "insecure",
  "branch": "main"
}
CREDS

RUN cat > /opt/gel-bootstrap/gel.toml <<'TOML'
[instance]
server-version = "7"
TOML

# Bootstrap the instance, the linked project, the seeded pre-incident backup and
# the damaged working branch.
RUN cat > /opt/gel-bootstrap/bake.sh <<'BAKE'
#!/usr/bin/env bash
set -eux

PROJECT_DIR=/home/user/recovery
ASSETS=/opt/gel-bootstrap

mkdir -p "${PROJECT_DIR}/dbschema" "${PROJECT_DIR}/backups"
cp "${ASSETS}/default.gel" "${PROJECT_DIR}/dbschema/default.gel"
cp "${ASSETS}/gel.toml" "${PROJECT_DIR}/gel.toml"

for home in /root /home/user; do
    mkdir -p "${home}/.config/gel/credentials"
    cp "${ASSETS}/credentials.json" "${home}/.config/gel/credentials/recovery.json"
done

gel-start

cd "${PROJECT_DIR}"

export HOME=/root
gel project init --link --server-instance recovery --non-interactive
gel migration create --non-interactive
gel migrate
gel query -f "${ASSETS}/seed.edgeql" >/dev/null
gel dump "${PROJECT_DIR}/backups/pre_incident.dump"
gel query -f "${ASSETS}/damage.edgeql" >/dev/null

# Link the project for a /home/user HOME as well, so the CLI works no matter
# which home directory the session uses.
export HOME=/home/user
gel project init --link --server-instance recovery --non-interactive

export HOME=/root
test "$(gel query -F json 'select count(Shipment)')" = '[24]'
test "$(gel query -F json 'select count(Warehouse)')" = '[4]'
test -s "${PROJECT_DIR}/backups/pre_incident.dump"

chmod -R a+rwX /home/user

# Shut the server down cleanly so the baked data directory is consistent.
pkill -TERM -u gel -f gel-server || true
sleep 8
BAKE

RUN bash /opt/gel-bootstrap/bake.sh && rm -rf /opt/gel-bootstrap

LABEL Author="generate-harbor-tasks"
LABEL Description="Gel 7 disaster-recovery task: damaged working branch, baked pre-incident dump and a linked Gel project in /home/user/recovery."

CMD ["/bin/bash", "-c", "trap : TERM INT; sleep infinity & wait"]

# Container contents:
#   - always required apt packages: curl wget ca-certificates git python3 python3-pip bash ripgrep
#     (python3-pytest intentionally omitted; pytest comes from pip3)
#   - extra apt packages: jq gnupg procps
#   - always required pip3 packages: pytest pytest-json-ctrf
#   - Gel 7 server + CLI from https://packages.geldata.com/apt (package `gel-7`)
#   - /usr/local/bin/gel-start: idempotent local Gel server launcher (non-root, explicit runstate dir)
#   - /var/lib/gel/data: baked Gel data directory with branch `main`
#   - /home/user/recovery: Gel project linked to the local instance `recovery`
#   - /home/user/recovery/dbschema: loose starting schema plus its single migration
#   - /home/user/recovery/backups/pre_incident.dump: the provided pre-incident backup

COPY test_initial_state.py /bootstrap/test_initial_state.py

# Setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
