# Task environment for `gel_polymorphic_catalog_constraints_cli`.
#
# NOTE ON THE BASE IMAGE: this task needs a *local* Gel 6 server that works with
# no network access at evaluation time. The official `geldata/gel` image is the
# only distribution channel that reliably ships both the server and the `gel`
# CLI together, and its bundled `docker-entrypoint.sh` knows how to locate the
# server binary and drop privileges to the unprivileged `gel` user. We therefore
# base off `geldata/gel:6.11` (Debian bookworm) instead of `ubuntu:24.04`, clear
# the image ENTRYPOINT so the container behaves like a plain shell box, and add
# the standard harbor tooling on top.
FROM geldata/gel:6.11

ENTRYPOINT []
SHELL ["/bin/bash", "-c"]

ARG POCHI_VERSION

ENV DEBIAN_FRONTEND=noninteractive

# --- local Gel server configuration ----------------------------------------
# An explicit runstate dir is mandatory: the default (/run/gel) is root-owned
# tmpfs while the server itself runs as the unprivileged `gel` user.
# The data directory deliberately lives OUTSIDE /var/lib/gel/data, because the
# base image declares that path as a VOLUME and Docker would silently throw
# away anything a build step writes underneath it.
ENV GEL_SERVER_SECURITY=insecure_dev_mode \
    GEL_SERVER_TLS_CERT_MODE=generate_self_signed \
    GEL_SERVER_DATADIR=/var/lib/gelcatalog/data \
    GEL_SERVER_RUNSTATE_DIR=/var/lib/gelcatalog/run \
    GEL_SERVER_BIND_ADDRESS=127.0.0.1 \
    GEL_SERVER_PORT=5656 \
    GEL_SERVER_DEFAULT_BRANCH=main \
    GEL_DOCKER_APPLY_MIGRATIONS=never \
    GEL_DOCKER_SHOW_GENERATED_CERT=never

# --- client configuration (CLI and any client library) ----------------------
ENV GEL_HOST=127.0.0.1 \
    GEL_PORT=5656 \
    GEL_USER=admin \
    GEL_BRANCH=main \
    GEL_CLIENT_SECURITY=insecure_dev_mode \
    GEL_WAIT_UNTIL_AVAILABLE=30s

RUN apt-get update -y && \
    apt-get install -y --no-install-recommends \
        curl wget ca-certificates git bash ripgrep jq less procps \
        python3 python3-pip && \
    ln -sf /usr/bin/python3 /usr/bin/python && \
    curl -fsSL https://getpochi.com/install.sh | bash -s ${POCHI_VERSION} && \
    ln -sf /root/.pochi/bin/pochi /usr/local/bin/pochi && \
    pip3 install --break-system-packages "pytest>=8" pytest-json-ctrf && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN mkdir -p /home/user && chmod 0755 /home/user

# --- helper that boots the local Gel server (idempotent) --------------------
RUN cat >/usr/local/bin/gel-start <<'GELSTART'
#!/usr/bin/env bash
# Bring up the bundled local Gel server if it is not already answering queries.
set -uo pipefail

LOG=/var/log/gel-server.log

probe() {
  env GEL_WAIT_UNTIL_AVAILABLE=1s gel query 'select 1' >/dev/null 2>&1
}

if probe; then
  echo "gel server already running on ${GEL_HOST}:${GEL_PORT}"
  exit 0
fi

mkdir -p "${GEL_SERVER_DATADIR}" "${GEL_SERVER_RUNSTATE_DIR}"
chown -R gel "${GEL_SERVER_DATADIR}" "${GEL_SERVER_RUNSTATE_DIR}" 2>/dev/null || true
touch "${LOG}"

nohup /usr/local/bin/docker-entrypoint.sh server >>"${LOG}" 2>&1 &
disown 2>/dev/null || true

for _ in $(seq 1 75); do
  if probe; then
    echo "gel server is ready on ${GEL_HOST}:${GEL_PORT}"
    exit 0
  fi
  sleep 2
done

echo "ERROR: the local Gel server did not become ready in time." >&2
echo "--- last 80 lines of ${LOG} ---" >&2
tail -n 80 "${LOG}" >&2
exit 1
GELSTART

RUN chmod 0755 /usr/local/bin/gel-start && \
    mkdir -p /var/lib/gelcatalog/data /var/lib/gelcatalog/run /opt/task \
             /home/user/catalog/dbschema/migrations && \
    chown -R gel /var/lib/gelcatalog/data /var/lib/gelcatalog/run && \
    chmod 0700 /var/lib/gelcatalog/data && \
    chmod 0775 /var/lib/gelcatalog/run

# --- initial schema, revision 1 (abstract Product + Book) -------------------
RUN cat >/home/user/catalog/dbschema/default.gel <<'SDLV1'
module default {
  abstract type Product {
    required sku: str {
      constraint exclusive;
    }
    required title: str;
    required price_cents: int64;
    required stock: int64;

    index on (.title);
  }

  type Book extending Product {
    required author: str;
    pages: int64;
  }
}
SDLV1

# --- initial schema, revision 2 (adds Apparel) ------------------------------
RUN cat >/opt/task/schema_v2.gel <<'SDLV2'
module default {
  abstract type Product {
    required sku: str {
      constraint exclusive;
    }
    required title: str;
    required price_cents: int64;
    required stock: int64;

    index on (.title);
  }

  type Book extending Product {
    required author: str;
    pages: int64;
  }

  type Apparel extending Product {
    required size_label: str;
  }
}
SDLV2

# --- seed data (12 products) ------------------------------------------------
RUN cat >/opt/task/seed.edgeql <<'SEED'
insert Book { sku := 'BOK-0001', title := 'Aurora Press | Deep Waters', price_cents := 1899, stock := 12, author := 'Mira Vance', pages := 340 };
insert Book { sku := 'BOK-0002', title := 'Aurora Press | Salt and Stone', price_cents := 2499, stock := 0, author := 'Mira Vance', pages := 512 };
insert Book { sku := 'BOK-0003', title := 'Nimbus Books | The Quiet Engine', price_cents := 1250, stock := 3, author := 'Otto Reyes', pages := 210 };
insert Book { sku := 'BOK-0004', title := 'Nimbus Books | Vector Fields', price_cents := 4599, stock := 7, author := 'Priya Raman', pages := 880 };
insert Book { sku := 'BOK-0005', title := 'Harbor House | Lanterns', price_cents := 999, stock := 25, author := 'Ken Adeyemi', pages := 128 };
insert Book { sku := 'BOK-0006', title := 'Harbor House | Tidal', price_cents := 1599, stock := 0, author := 'Ken Adeyemi', pages := 256 };
insert Apparel { sku := 'APP-0001', title := 'Northwind | Trail Jacket', price_cents := 8999, stock := 4, size_label := 'M' };
insert Apparel { sku := 'APP-0002', title := 'Northwind | Summit Pants', price_cents := 5499, stock := 11, size_label := 'L' };
insert Apparel { sku := 'APP-0003', title := 'Cobalt | Base Layer', price_cents := 3200, stock := 0, size_label := 'S' };
insert Apparel { sku := 'APP-0004', title := 'Cobalt | Wind Shell', price_cents := 7250, stock := 2, size_label := 'XL' };
insert Apparel { sku := 'APP-0005', title := 'Meridian | Wool Socks', price_cents := 1450, stock := 60, size_label := 'M' };
insert Apparel { sku := 'APP-0006', title := 'Meridian | Rain Hat', price_cents := 2100, stock := 1, size_label := 'L' };
SEED

# --- snapshot writer -------------------------------------------------------
RUN cat >/opt/task/make_snapshot.py <<'SNAP'
import json


def load(path):
    with open(path) as handle:
        data = json.load(handle)
    return data if isinstance(data, list) else [data]


names = sorted(row["name"] for row in load("/tmp/migrations.json"))
products = []
for row in load("/tmp/products.json"):
    kind = row["kind"].split("::")[-1]
    entry = {
        "id": row["id"],
        "sku": row["sku"],
        "title": row["title"],
        "price_cents": row["price_cents"],
        "stock": row["stock"],
        "kind": kind,
    }
    if kind == "Book":
        entry["author"] = row["author"]
        entry["pages"] = row["pages"]
    elif kind == "Apparel":
        entry["size_label"] = row["size_label"]
    else:
        raise AssertionError("unexpected product kind: %r" % kind)
    products.append(entry)

assert len(names) == 2, names
assert len(products) == 12, products

with open("/opt/task/initial_state.json", "w") as handle:
    json.dump({"migrations": names, "products": products}, handle, indent=2, sort_keys=True)
    handle.write("\n")
SNAP

# --- bake script: bootstrap the instance, apply 2 migrations, seed data -----
RUN cat >/opt/task/bake.sh <<'BAKE'
#!/usr/bin/env bash
set -euxo pipefail

PROJECT=/home/user/catalog
LOG=/var/log/gel-build.log

mkdir -p "${GEL_SERVER_DATADIR}" "${GEL_SERVER_RUNSTATE_DIR}"
chown -R gel "${GEL_SERVER_DATADIR}" "${GEL_SERVER_RUNSTATE_DIR}"

# Initialise the data directory without leaving a server running.
GEL_SERVER_BOOTSTRAP_ONLY=1 /usr/local/bin/docker-entrypoint.sh server

# Start the real server in the background so the CLI can talk to it.
/usr/local/bin/docker-entrypoint.sh server >"${LOG}" 2>&1 &
SRV_PID=$!

ready=0
for _ in $(seq 1 90); do
  if env GEL_WAIT_UNTIL_AVAILABLE=1s gel query 'select 1' >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 2
done
if [ "${ready}" != "1" ]; then
  cat "${LOG}"
  exit 1
fi

cd "${PROJECT}"

# Revision 1.
gel migration create --non-interactive
gel migrate

# Revision 2.
cp /opt/task/schema_v2.gel "${PROJECT}/dbschema/default.gel"
gel migration create --non-interactive
gel migrate

gel migration status --quiet

# Seed the branch with 12 products, one statement per CLI call so that a single
# failing insert is easy to spot in the build log.
while IFS= read -r statement; do
  if [ -n "${statement}" ]; then
    gel query "${statement}" </dev/null
  fi
done </opt/task/seed.edgeql

# Sanity checks on the baked initial state (migration file names are
# content-hashed, so they are always resolved with a glob).
migration_count="$(find "${PROJECT}/dbschema/migrations" -maxdepth 1 -name '*.edgeql' | wc -l | tr -d '[:space:]')"
test "${migration_count}" = "2"
gel query -F json 'select count(Product)' | python3 -c 'import json, sys; data = json.load(sys.stdin); data = data if isinstance(data, list) else [data]; assert data == [12], data'

# Record the reference snapshot used by the graders.
gel query -F json 'select schema::Migration { name } filter not .builtin' >/tmp/migrations.json
gel query -F json 'select Product {
    id, sku, title, price_cents, stock,
    kind := .__type__.name,
    [is Book].author,
    [is Book].pages,
    [is Apparel].size_label
} order by .sku' >/tmp/products.json
python3 /opt/task/make_snapshot.py

# Stop the server cleanly so the on-disk cluster is consistent in the image.
kill -TERM "${SRV_PID}" || true
wait "${SRV_PID}" || true

rm -f /tmp/migrations.json /tmp/products.json
BAKE

RUN chmod 0755 /opt/task/bake.sh && bash /opt/task/bake.sh

RUN chmod 0444 /opt/task/initial_state.json && \
    chmod -R a+rX /home/user/catalog && \
    rm -f /var/log/gel-build.log

LABEL Author="generate-harbor-tasks"
LABEL Description="Local Gel 6 server with a seeded, partially migrated polymorphic product catalog project in /home/user/catalog."

CMD ["/bin/bash", "-c", "trap : TERM INT; sleep infinity & wait"]

# Container contents:
#   - base image geldata/gel:6.11 -> local Gel 6 server + `gel` CLI (Debian bookworm)
#   - apt packages: curl wget ca-certificates git bash ripgrep jq less procps python3 python3-pip
#   - pip3 packages: pytest>=8, pytest-json-ctrf (the distro `python3-pytest` is
#     deliberately NOT installed: it is too old for the ctrf reporting plugin)
#   - Pochi CLI at /usr/local/bin/pochi
#   - /usr/local/bin/gel-start: idempotent helper that boots the local server
#     with an explicit, pre-chowned --runstate-dir (/var/lib/gelcatalog/run)
#   - /var/lib/gelcatalog/data: pre-bootstrapped Gel data directory holding
#     branch `main` with 2 applied migrations and 12 seeded product rows
#     (kept out of the base image's /var/lib/gel/data VOLUME on purpose)
#   - /home/user/catalog/dbschema/default.gel + dbschema/migrations/*.edgeql:
#     the partial catalog schema and its 2 content-hashed migration files
#   - /opt/task/initial_state.json: read-only reference snapshot for graders
#   - /opt/task/{schema_v2.gel,seed.edgeql,make_snapshot.py,bake.sh}: build-time
#     artifacts kept for traceability

COPY test_initial_state.py /bootstrap/test_initial_state.py

# Setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
