# syntax=docker/dockerfile:1
# Task environment for gel_json_document_merge_engine.
#
# The Gel 7.1 server runs *inside* this container (no docker-in-docker, no cloud service):
# the official geldata/gel:7.1 image already bundles both the server and the `gel` CLI, so it
# is used as the base image and extended with python3 / pytest / the Gel Python client.
FROM geldata/gel:7.1

ARG POCHI_VERSION

# The parent image declares VOLUME /var/lib/gel/data, so writes to that path during the build
# would be discarded; a dedicated data directory keeps the seeded database inside the image.
ENV GEL_SERVER_DATADIR=/opt/geldata \
    GEL_SERVER_SECURITY=insecure_dev_mode \
    GEL_SERVER_TLS_CERT_MODE=generate_self_signed \
    GEL_SERVER_BIND_ADDRESS=127.0.0.1 \
    GEL_SERVER_PORT=5656 \
    GEL_SERVER_COMPILER_POOL_MODE=on_demand \
    GEL_SERVER_COMPILER_POOL_SIZE=2 \
    GEL_DOCKER_APPLY_MIGRATIONS=never \
    GEL_DOCKER_LOG_LEVEL=warning \
    GEL_DSN=gel://admin@127.0.0.1:5656 \
    GEL_CLIENT_TLS_SECURITY=insecure \
    GEL_WAIT_UNTIL_AVAILABLE=90s \
    PIP_ROOT_USER_ACTION=ignore \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl wget ca-certificates git python3 python3-pip python3-pytest bash ripgrep \
        procps psmisc && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    curl -fsSL https://getpochi.com/install.sh | bash -s ${POCHI_VERSION} && \
    ln -s /root/.pochi/bin/pochi /usr/local/bin/pochi && \
    pip3 install --break-system-packages pytest-json-ctrf gel==3.1.0 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN mkdir -p /home/user && chmod 0755 /home/user
# The container entrypoint of the base image creates and chowns the data directory itself.
RUN mkdir -p /opt/seed /var/log

# --------------------------------------------------------------------------------------
# Project skeleton: gel.toml + SDL schema (the migration is created/applied further down).
# --------------------------------------------------------------------------------------
RUN mkdir -p /home/user/settings-engine/dbschema && \
    cat >/home/user/settings-engine/gel.toml <<'TOMLEOF'
[instance]
server-version = "7.1"

[project]
schema-dir = "dbschema"
TOMLEOF

RUN cat >/home/user/settings-engine/dbschema/default.gel <<'GELEOF'
module default {
    # One stored JSON settings document plus its bookkeeping.
    type SettingsLayer {
        required key: str {
            constraint exclusive;
        }
        required tier: str;
        required doc: json;
        required active: bool {
            default := true;
        }
        required revision: int64 {
            default := 0;
        }
    }

    # A settings target together with its chain of layers.
    type SettingsRecord {
        required slug: str {
            constraint exclusive;
        }
        required label: str;
        multi layers: SettingsLayer {
            precedence: int64;
        }
    }
}
GELEOF

# --------------------------------------------------------------------------------------
# Idempotent server starter, used by the agent and by both test suites.
# --------------------------------------------------------------------------------------
RUN cat >/usr/local/bin/gel-start.sh <<'STARTEOF'
#!/bin/bash
# Starts the container-local Gel server if it is not running yet and blocks until it is
# ready to accept queries. Safe to run repeatedly.

export GEL_SERVER_DATADIR="${GEL_SERVER_DATADIR:-/opt/geldata}"
export GEL_SERVER_SECURITY="${GEL_SERVER_SECURITY:-insecure_dev_mode}"
export GEL_SERVER_TLS_CERT_MODE="${GEL_SERVER_TLS_CERT_MODE:-generate_self_signed}"
export GEL_SERVER_BIND_ADDRESS="${GEL_SERVER_BIND_ADDRESS:-127.0.0.1}"
export GEL_SERVER_PORT="${GEL_SERVER_PORT:-5656}"
export GEL_SERVER_COMPILER_POOL_MODE="${GEL_SERVER_COMPILER_POOL_MODE:-on_demand}"
export GEL_SERVER_COMPILER_POOL_SIZE="${GEL_SERVER_COMPILER_POOL_SIZE:-2}"
export GEL_DOCKER_APPLY_MIGRATIONS="${GEL_DOCKER_APPLY_MIGRATIONS:-never}"
export GEL_DOCKER_LOG_LEVEL="${GEL_DOCKER_LOG_LEVEL:-warning}"
export GEL_DSN="${GEL_DSN:-gel://admin@127.0.0.1:5656}"
export GEL_CLIENT_TLS_SECURITY="${GEL_CLIENT_TLS_SECURITY:-insecure}"

LOGFILE=/var/log/gel-server.log

is_ready() {
    curl -fsS -m 5 "http://127.0.0.1:${GEL_SERVER_PORT}/server/status/ready" >/dev/null 2>&1 \
        || GEL_WAIT_UNTIL_AVAILABLE=2s gel query "select 1" >/dev/null 2>&1
}

if is_ready; then
    exit 0
fi

if ! pgrep -f "gel-server" >/dev/null 2>&1; then
    mkdir -p /var/log
    nohup /usr/local/bin/docker-entrypoint.sh server >>"${LOGFILE}" 2>&1 &
fi

for _ in $(seq 1 180); do
    if is_ready; then
        exit 0
    fi
    sleep 2
done

echo "gel-start.sh: the Gel server did not become ready in time" >&2
tail -n 80 "${LOGFILE}" >&2 2>/dev/null
exit 1
STARTEOF

RUN chmod 0755 /usr/local/bin/gel-start.sh

# --------------------------------------------------------------------------------------
# Seed data: the settings catalog the task operates on.
# --------------------------------------------------------------------------------------
RUN cat >/opt/seed/seed.py <<'PYEOF'
"""Seeds the settings catalog used by the gel_json_document_merge_engine task."""

import json

import gel

CURATED_LAYERS = [
    {"key": "base.global", "tier": "global", "active": True, "revision": 3, "doc": {"theme": "light", "limits": {"cpu": 2, "mem": 512, "nested": {"deep": {"a": 1, "b": 2}}}, "features": ["a", "b"], "flags": {"beta": False, "gamma": True}, "keep": "global", "": "empty-key-global", "1": "one", "2": "two", "10": "ten", "id": "global-id", "__type__": "global-type"}},
    {"key": "user.alice", "tier": "user", "active": True, "revision": 0, "doc": {"theme": "solar", "limits": {"cpu": 8}, "keep": None, "extra": {"x": {"y": {"z": True}}}, "id": 5}},
    {"key": "group.engineering", "tier": "group", "active": True, "revision": 0, "doc": {"theme": "dark", "limits": {"mem": 1024, "nested": {"deep": {"b": None, "c": 3}}}, "features": ["c"], "flags": None, "日本語": "group", "emoji🙂": "group"}},
    {"key": "override.disabled", "tier": "group", "active": False, "revision": 41, "doc": {"theme": "MUST-NOT-APPLY", "limits": None}},
    {"key": "user.bob", "tier": "user", "active": True, "revision": 0, "doc": {"limits": {}, "features": {}, "flags": {"beta": {}}, "theme": {"nested": True}, "": None, "missing": None, "1": None}},
    {"key": "base.empty", "tier": "global", "active": True, "revision": 0, "doc": {}},
    {"key": "noop.disabled", "tier": "user", "active": False, "revision": 0, "doc": {"a": 1}},
    {"key": "ghost.a", "tier": "group", "active": False, "revision": 0, "doc": {"ghost": 1}},
    {"key": "ghost.b", "tier": "user", "active": False, "revision": 0, "doc": {"ghost": 2}},
    {"key": "patch.base", "tier": "global", "active": True, "revision": 0, "doc": {"a": {"b": 1, "c": [1, 2]}, "z": "zed"}},
    {"key": "patch.user", "tier": "user", "active": True, "revision": 0, "doc": {"a": {"b": 2}}},
    {"key": "patch.shadow", "tier": "group", "active": False, "revision": 0, "doc": {"shadow": True}},
    {"key": "race.base", "tier": "global", "active": True, "revision": 0, "doc": {"seed": "kept", "counterparts": {"pre": 1}}},
    {"key": "race.user", "tier": "user", "active": True, "revision": 0, "doc": {}},
]

CURATED_RECORDS = [
    {"slug": "acme-alice", "label": "Acme / Alice", "chain": [["base.global", 10], ["user.alice", 50], ["group.engineering", 200], ["override.disabled", 999]]},
    {"slug": "acme-bob", "label": "Acme / Bob", "chain": [["base.global", 10], ["user.bob", 20]]},
    {"slug": "solo-empty", "label": "Solo Empty", "chain": [["base.empty", 1], ["noop.disabled", 2]]},
    {"slug": "ghost-town", "label": "Ghost Town", "chain": [["ghost.a", 1], ["ghost.b", 2]]},
    {"slug": "orphan-record", "label": "Orphan", "chain": []},
    {"slug": "patch-target", "label": "Patch Target", "chain": [["patch.base", 10], ["patch.user", 90], ["patch.shadow", 95]]},
    {"slug": "race-target", "label": "Race Target", "chain": [["race.base", 10], ["race.user", 20]]},
]

FILLER_COUNT = 300

INSERT_LAYERS = """
for item in json_array_unpack(to_json(<str>$data)) union (
    insert SettingsLayer {
        key := <str>item['key'],
        tier := <str>item['tier'],
        doc := item['doc'],
        active := <bool>item['active'],
        revision := <int64>item['revision']
    }
)
"""

INSERT_RECORD = """
insert SettingsRecord {
    slug := <str>$slug,
    label := <str>$label,
    layers := assert_distinct((
        for entry in json_array_unpack(to_json(<str>$chain)) union (
            select SettingsLayer { @precedence := <int64>entry[1] }
            filter .key = <str>entry[0]
        )
    ))
}
"""

INSERT_RECORD_WITHOUT_LAYERS = """
insert SettingsRecord { slug := <str>$slug, label := <str>$label }
"""


def build_dataset():
    layers = [dict(item) for item in CURATED_LAYERS]
    records = [dict(item) for item in CURATED_RECORDS]
    for index in range(FILLER_COUNT):
        name = "fill-%04d" % index
        layers.append({"key": name + ".g", "tier": "global", "active": True, "revision": 0, "doc": {"g": {"i": index}}})
        layers.append({"key": name + ".u", "tier": "user", "active": True, "revision": 0, "doc": {"u": {"i": index}, "g": {"x": True}}})
        layers.append({"key": name + ".x", "tier": "user", "active": False, "revision": 0, "doc": {"x": 1}})
        records.append({"slug": name, "label": "Filler " + name[5:], "chain": [[name + ".g", 10], [name + ".u", 20], [name + ".x", 30]]})
    return layers, records


layers, records = build_dataset()
client = gel.create_client()
client.execute("delete SettingsRecord;")
client.execute("delete SettingsLayer;")
client.query(INSERT_LAYERS, data=json.dumps(layers, ensure_ascii=False))
for record in records:
    if record["chain"]:
        client.query(
            INSERT_RECORD,
            slug=record["slug"],
            label=record["label"],
            chain=json.dumps(record["chain"]),
        )
    else:
        client.query(
            INSERT_RECORD_WITHOUT_LAYERS,
            slug=record["slug"],
            label=record["label"],
        )

layer_count = client.query_single("select count(SettingsLayer)")
record_count = client.query_single("select count(SettingsRecord)")
print("seeded layers=%s records=%s" % (layer_count, record_count))
client.close()

if layer_count != len(layers) or record_count != len(records):
    raise SystemExit("seeding failed: unexpected object counts")
PYEOF

# --------------------------------------------------------------------------------------
# Build-time bootstrap: start the server, create + apply the migration, seed, link project.
# --------------------------------------------------------------------------------------
RUN cat >/opt/seed/bootstrap.sh <<'BOOTEOF'
#!/bin/bash
set -Eeuo pipefail

SCHEMA_DIR=/home/user/settings-engine/dbschema

/usr/local/bin/gel-start.sh
gel migration create --schema-dir "${SCHEMA_DIR}" --non-interactive
gel migration apply --schema-dir "${SCHEMA_DIR}"
python3 /opt/seed/seed.py

# Convenience for the agent: a named instance plus a project link. Connection resolution
# already works through the environment, so failures here are not fatal.
gel instance link \
    --dsn "gel://admin@127.0.0.1:5656" \
    --non-interactive --trust-tls-cert --overwrite settings_local \
    || echo "bootstrap: 'gel instance link' skipped"
(cd /home/user/settings-engine \
    && gel project init --link --server-instance settings_local --non-interactive --no-migrations) \
    || echo "bootstrap: 'gel project init --link' skipped"

gel query "select count(SettingsLayer)"
gel migration status --schema-dir "${SCHEMA_DIR}"

pkill -TERM -f gel-server || true
sleep 10
BOOTEOF

RUN chmod 0755 /opt/seed/bootstrap.sh && /opt/seed/bootstrap.sh

# Harbor drives this container itself, so the server-starting entrypoint of the base image
# must not stay in effect.
ENTRYPOINT []

LABEL Author="generate-harbor-tasks"
LABEL Description="Gel 7.1 server, CLI and Python client in a single container, with a migrated project at /home/user/settings-engine and a seeded layered JSON settings catalog."

CMD ["/bin/bash", "-c", "trap : TERM INT; sleep infinity & wait"]

# Container contents:
#   - base image geldata/gel:7.1 (Gel 7.1 server + `gel` CLI, Debian bookworm)
#   - always required apt packages: curl wget ca-certificates git python3 python3-pip
#     python3-pytest bash ripgrep (plus procps/psmisc for process management)
#   - always required pip3 packages: pytest-json-ctrf
#   - Gel Python client: gel==3.1.0
#   - Pochi CLI installed to /root/.pochi/bin and symlinked into /usr/local/bin
#   - /opt/geldata: seeded Gel data directory (the parent image's VOLUME path is avoided)
#   - /usr/local/bin/gel-start.sh: idempotent server starter that blocks until ready
#   - /home/user/settings-engine: project with gel.toml, dbschema/default.gel and the
#     applied migration in dbschema/migrations
#   - /opt/seed/seed.py, /opt/seed/bootstrap.sh: build-time schema/seed provisioning
#   - seeded catalog: 914 SettingsLayer objects and 307 SettingsRecord objects

COPY test_initial_state.py /bootstrap/test_initial_state.py

# Setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
