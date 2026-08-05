FROM geldata/gel:7.1

USER root
ENTRYPOINT []

ARG POCHI_VERSION

# The base image declares VOLUME /var/lib/gel/data, so the server data directory is
# relocated to a plain directory inside the image filesystem.
ENV GEL_SERVER_DATADIR=/var/lib/gel-data \
    GEL_SERVER_SECURITY=insecure_dev_mode \
    GEL_SERVER_BIND_ADDRESS=0.0.0.0 \
    GEL_SERVER_PORT=5656 \
    GEL_DOCKER_APPLY_MIGRATIONS=never \
    GEL_HOST=127.0.0.1 \
    GEL_PORT=5656 \
    GEL_USER=admin \
    GEL_BRANCH=main \
    GEL_CLIENT_TLS_SECURITY=insecure

RUN apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl wget ca-certificates git python3 python3-pip python3-pytest bash ripgrep \
        unzip xz-utils gcc python3-dev && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    curl -fsSL https://getpochi.com/install.sh | bash -s ${POCHI_VERSION} && \
    ln -s /root/.pochi/bin/pochi /usr/local/bin/pochi && \
    pip3 install --break-system-packages pytest pytest-json-ctrf gel && \
    mkdir -p /var/lib/gel-data /var/log && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

LABEL Author="generate-harbor-tasks"
LABEL Description="Gel 7.1 server and CLI running locally, with a Python project skeleton and a deterministic social-graph seed dataset in /home/user/socialgraph."

RUN mkdir -p /home/user && chmod 0755 /home/user

# Helper that boots the local Gel server and blocks until it answers queries.
RUN cat >/usr/local/bin/gel-start <<'GELSTART'
#!/usr/bin/env bash
# Start the local Gel server unless it is already accepting queries.
# Safe to run repeatedly; exits 0 as soon as the server is ready.
set -u

LOG_FILE=/var/log/gel-server.log
PID_FILE=/run/gel-server.pid
WAIT_SECONDS=${GEL_START_WAIT_SECONDS:-900}

server_ready() {
  gel --wait-until-available 1s query "select 1" >/dev/null 2>&1
}

if server_ready; then
  exit 0
fi

mkdir -p "${GEL_SERVER_DATADIR:-/var/lib/gel-data}" /var/log

running=0
if [ -s "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  running=1
fi

if [ "$running" -eq 0 ]; then
  echo "gel-start: launching the Gel server (logs: $LOG_FILE)" >&2
  nohup /usr/local/bin/docker-entrypoint.sh server >>"$LOG_FILE" 2>&1 &
  echo $! >"$PID_FILE"
fi

deadline=$(( $(date +%s) + WAIT_SECONDS ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  if server_ready; then
    exit 0
  fi
  sleep 2
done

echo "gel-start: the Gel server did not become ready within ${WAIT_SECONDS}s." >&2
if [ -f "$LOG_FILE" ]; then
  tail -n 80 "$LOG_FILE" >&2
fi
exit 1
GELSTART

RUN chmod 0755 /usr/local/bin/gel-start && \
    mkdir -p /home/user/socialgraph/dbschema /home/user/socialgraph/data

RUN cat >/home/user/socialgraph/gel.toml <<'GELTOML'
[project]
schema-dir = "dbschema"
GELTOML

# Deterministic seed dataset: 200 members and 1914 directed connections, generated
# with fixed integer arithmetic (no randomness) so the file is byte-stable.
RUN cat >/tmp/gen_seed.py <<'GENSEED'
import datetime
import json

N_MEMBERS = 200
N_LINKED = 198

PATTERNS = [(7, 3), (13, 11), (29, 5), (37, 19), (53, 31), (61, 7), (71, 23), (83, 41)]
ROLES = ["collaborator", "mentor", "reviewer", "co_author"]
BASE = datetime.datetime(2024, 1, 1, 8, 0, 0, tzinfo=datetime.timezone.utc)


def handle(i):
    return "mbr_%03d" % i


def display_name(i):
    return "Member %03d" % i


def weight_of(i, j, k):
    return ((i + j * 3 + k) % 20) + 1


def role_of(i, j, k):
    return ROLES[(i + j + k) % len(ROLES)]


def confirmed_of(i, j, k):
    return ((i * j + k) % 5) < 3


def established_of(i, j):
    stamp = BASE + datetime.timedelta(days=(i % 350), minutes=(j % 60) * 7)
    return stamp.isoformat()


members = [{"handle": handle(i), "display_name": display_name(i)} for i in range(N_MEMBERS)]

edges = {}
order = []
for i in range(N_LINKED):
    for k, (a, b) in enumerate(PATTERNS):
        j = (i * a + b) % N_LINKED
        if j == i or (i, j) in edges:
            continue
        edges[(i, j)] = {
            "from": handle(i),
            "to": handle(j),
            "weight": weight_of(i, j, k),
            "role": role_of(i, j, k),
            "established": established_of(i, j),
            "confirmed": confirmed_of(i, j, k),
        }
        order.append((i, j))

extra = []
for idx, (i, j) in enumerate(order):
    if (j, i) in edges:
        continue
    if idx % 6 == 0:
        edges[(i, j)]["confirmed"] = True
        edges[(j, i)] = {
            "from": handle(j),
            "to": handle(i),
            "weight": weight_of(j, i, 2),
            "role": role_of(j, i, 1),
            "established": established_of(j, i),
            "confirmed": True,
        }
        extra.append((j, i))
    elif idx % 7 == 0:
        edges[(i, j)]["confirmed"] = True
        edges[(j, i)] = {
            "from": handle(j),
            "to": handle(i),
            "weight": weight_of(j, i, 3),
            "role": role_of(j, i, 2),
            "established": established_of(j, i),
            "confirmed": False,
        }
        extra.append((j, i))

order.extend(extra)
connections = [edges[key] for key in order]

with open("/home/user/socialgraph/data/seed.json", "w", encoding="utf-8") as fh:
    json.dump({"members": members, "connections": connections}, fh, indent=1)
    fh.write("\n")
GENSEED

RUN python3 /tmp/gen_seed.py && \
    rm -f /tmp/gen_seed.py && \
    python3 -c "import json; d=json.load(open('/home/user/socialgraph/data/seed.json')); assert len(d['members'])==200, len(d['members']); assert len(d['connections'])==1914, len(d['connections'])" && \
    chmod -R 0755 /home/user/socialgraph

CMD ["/bin/bash", "-c", "trap : TERM INT; sleep infinity & wait"]

# Container contents:
#   - base image geldata/gel:7.1: the Gel 7.1 server binaries plus the `gel` CLI
#   - always required apt packages: curl wget ca-certificates git python3 python3-pip
#     python3-pytest bash ripgrep (plus gcc/python3-dev so pip can build the Gel client
#     if no wheel matches the platform)
#   - always required pip3 packages: pytest-json-ctrf
#   - pip3 package `gel`: the Gel Python client used by the task and by the tests
#   - /usr/local/bin/gel-start: idempotent helper that boots the local Gel server (data
#     directory relocated to /var/lib/gel-data, insecure dev mode, Trust auth on
#     127.0.0.1:5656, branch `main`) and blocks until it answers queries
#   - connection settings exported for the CLI and all client libraries: GEL_HOST,
#     GEL_PORT, GEL_USER, GEL_BRANCH, GEL_CLIENT_TLS_SECURITY
#   - /home/user/socialgraph: project skeleton (gel.toml, empty dbschema/)
#   - /home/user/socialgraph/data/seed.json: deterministic dataset with 200 members and
#     1914 directed connections carrying weight/role/established/confirmed metadata

COPY test_initial_state.py /bootstrap/test_initial_state.py

# Setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
