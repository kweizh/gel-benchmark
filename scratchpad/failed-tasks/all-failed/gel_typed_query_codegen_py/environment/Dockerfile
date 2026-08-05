# Task environment: a fully local Gel 7.1 stack (server + CLI + Python client)
# with a seeded, already-migrated project at /home/user/harvest_api.
#
# The official Gel 7.1 image is used as the base because it is the only reliable
# way to get the pinned Gel 7.1 *server* and the matching `gel` CLI into a single
# offline container. The image runs as root by default (it drops privileges to the
# `gel` OS user itself via gosu), so no USER directive is used here.
FROM geldata/gel:7.1

ARG POCHI_VERSION

# --- Base tooling, Pochi CLI and every Python dependency (build time only) ---
RUN apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl wget ca-certificates git python3 python3-pip python3-pytest bash ripgrep \
        procps psmisc jq && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    curl -fsSL https://getpochi.com/install.sh | bash -s ${POCHI_VERSION} && \
    ln -s /root/.pochi/bin/pochi /usr/local/bin/pochi && \
    pip3 install --break-system-packages \
        pytest-json-ctrf requests "gel>=3.1.0,<4" fastapi uvicorn && \
    rm -f /usr/local/bin/gel /usr/local/bin/edgedb && \
    gel --version && \
    python3 -c "import gel; print('gel python client', gel.__version__)" && \
    command -v gel-py && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# --- Local Gel instance configuration (data dir kept out of the image VOLUME) ---
ENV GEL_SERVER_DATADIR=/var/lib/gel-task/data \
    GEL_SERVER_SECURITY=insecure_dev_mode \
    GEL_SERVER_TLS_CERT_MODE=generate_self_signed \
    GEL_SERVER_COMPILER_POOL_SIZE=2 \
    GEL_DOCKER_APPLY_MIGRATIONS=never \
    GEL_DOCKER_SHOW_GENERATED_CERT=never \
    GEL_DSN=gel://admin@127.0.0.1:5656/main \
    GEL_CLIENT_TLS_SECURITY=insecure

RUN mkdir -p /home/user && chmod 0755 /home/user
RUN mkdir -p /opt/task /var/log/gel /var/lib/gel-task/data

# --- Helpers to start/stop the local Gel server inside the container ---
RUN cat >/usr/local/bin/gel-ready <<'EOF'
#!/bin/bash
# Exit 0 as soon as the local Gel instance reports that it is ready for queries.
curl -fsS -k -m 5 https://127.0.0.1:5656/server/status/ready >/dev/null 2>&1 && exit 0
curl -fsS -m 5 http://127.0.0.1:5656/server/status/ready >/dev/null 2>&1 && exit 0
exit 1
EOF
RUN cat >/usr/local/bin/gel-start <<'EOF'
#!/bin/bash
# Start the local Gel server if it is not running yet and block until it is
# ready to accept queries. Idempotent.
if gel-ready; then
  exit 0
fi
mkdir -p /var/log/gel
setsid nohup docker-entrypoint.sh server </dev/null >>/var/log/gel/server.log 2>&1 &
for _ in $(seq 1 300); do
  if gel-ready; then
    if gel query "select 1" >/dev/null 2>&1; then
      exit 0
    fi
  fi
  sleep 1
done
echo "error: the local Gel server did not become ready in time" >&2
tail -n 100 /var/log/gel/server.log >&2
exit 1
EOF
RUN cat >/usr/local/bin/gel-stop <<'EOF'
#!/bin/bash
# Stop the local Gel server (graceful) and wait until the port is closed.
pkill -f "[g]el-server" >/dev/null 2>&1
for _ in $(seq 1 90); do
  if ! gel-ready; then
    exit 0
  fi
  sleep 1
done
echo "warning: the local Gel server is still responding" >&2
exit 0
EOF
RUN chmod 0755 /usr/local/bin/gel-ready /usr/local/bin/gel-start /usr/local/bin/gel-stop

# --- The pre-existing Gel project skeleton ---
RUN mkdir -p /home/user/harvest_api/dbschema /home/user/harvest_api/app/queries && \
    touch /home/user/harvest_api/app/__init__.py \
          /home/user/harvest_api/app/queries/__init__.py

RUN cat >/home/user/harvest_api/gel.toml <<'EOF'
[instance]
server-version = "7.1"
EOF

RUN cat >/home/user/harvest_api/dbschema/default.gel <<'EOF'
module default {
  type Region {
    required code: str {
      constraint exclusive;
    }
    required name: str;
  }

  type Grower {
    required slug: str {
      constraint exclusive;
    }
    required name: str;
    required region: Region;
  }

  type Batch {
    required code: str {
      constraint exclusive;
    }
    required grower: Grower;
    required kilograms: float64;
    required harvested_on: cal::local_date;
    multi certifications: str;

    index on (.code);
  }

  type Defect {
    required code: str;
    required severity: int64;
  }

  type Inspection {
    required batch: Batch;
    required inspector: str;
    required passed: bool;
    required recorded_at: datetime {
      default := datetime_of_statement();
    }
    multi defects: Defect;
  }
}
EOF

# --- Deterministic seed data (build time only; lives outside the project) ---
RUN cat >/opt/task/seed.py <<'EOF'
"""Seed the harvest_api branch with a small, fully deterministic dataset."""
import datetime

import gel

REGIONS = [
    ("NOR", "Northern Highlands"),
    ("SOU", "Southern Valley"),
    ("EAS", "Eastern Coast"),
]
CERTIFICATIONS = {
    1: ["organic"],
    2: ["fairtrade", "organic"],
    3: ["fairtrade"],
    4: ["organic", "rainforest"],
    5: [],
}

client = gel.create_client()
client.ensure_connected()

for statement in [
    "delete Inspection",
    "delete Defect",
    "delete Batch",
    "delete Grower",
    "delete Region",
]:
    client.execute(statement)

for code, name in REGIONS:
    client.query(
        "insert Region { code := <str>$code, name := <str>$name }",
        code=code,
        name=name,
    )

for i in range(1, 13):
    slug = "grower-%02d" % i
    client.query(
        """
        insert Grower {
          slug := <str>$slug,
          name := <str>$name,
          region := assert_single((select Region filter .code = <str>$region_code)),
        }
        """,
        slug=slug,
        name="Grower %02d" % i,
        region_code=REGIONS[(i - 1) % 3][0],
    )
    for j in range(1, 6):
        client.query(
            """
            insert Batch {
              code := <str>$code,
              grower := assert_single((select Grower filter .slug = <str>$slug)),
              kilograms := <float64>$kilograms,
              harvested_on := <cal::local_date>$harvested_on,
              certifications := array_unpack(<array<str>>$certifications),
            }
            """,
            code="BLK-%d" % (i * 100 + j),
            slug=slug,
            kilograms=float(100 * j + 10 * i),
            harvested_on=datetime.date(2025, j, i),
            certifications=CERTIFICATIONS[j],
        )

assert client.query_single("select count(Region)") == 3
assert client.query_single("select count(Grower)") == 12
assert client.query_single("select count(Batch)") == 60
assert client.query_single("select count(Inspection)") == 0
assert client.query_single("select count(Defect)") == 0
client.close()
print("seeded 3 regions, 12 growers, 60 batches")
EOF

RUN cat >/opt/task/schema_checksum.py <<'EOF'
"""Print a checksum of the project's schema plus its migration history."""
import hashlib
import pathlib

PROJECT = pathlib.Path("/home/user/harvest_api")

paths = [PROJECT / "dbschema" / "default.gel"]
migrations = PROJECT / "dbschema" / "migrations"
if migrations.is_dir():
    paths.extend(sorted(p for p in migrations.rglob("*") if p.is_file()))

digest = hashlib.sha256()
for path in paths:
    digest.update(str(path.relative_to(PROJECT)).encode("utf-8"))
    digest.update(b"\0")
    digest.update(path.read_bytes())
    digest.update(b"\0")
print(digest.hexdigest())
EOF

# --- Bootstrap the instance, apply the migration, seed it, then shut it down ---
RUN gel-start && \
    cd /home/user/harvest_api && \
    gel migration create --non-interactive --allow-unsafe && \
    gel migrate && \
    gel migration status && \
    python3 /opt/task/seed.py && \
    python3 /opt/task/schema_checksum.py >/opt/task/schema.sha256 && \
    cat /opt/task/schema.sha256 && \
    gel-stop

LABEL Author="generate-harbor-tasks"
LABEL Description="Local Gel 7.1 server plus the Gel Python client and code generator, with a migrated and seeded harvest-tracking project at /home/user/harvest_api."

CMD ["/bin/bash", "-c", "trap : TERM INT; sleep infinity & wait"]

# Container contents:
#   - base image geldata/gel:7.1 (Gel 7.1 server + bundled `gel` CLI, Debian bookworm)
#   - always required apt packages: curl wget ca-certificates git python3 python3-pip
#     python3-pytest bash ripgrep (plus procps/psmisc/jq for process handling)
#   - always required pip3 packages: pytest-json-ctrf
#   - pip3 packages for the task and the tests: gel (Python client + code generator),
#     requests, fastapi, uvicorn
#   - the pip-installed `gel` console-script shim is removed so the bundled Gel CLI
#     at /usr/bin/gel stays in charge (it never needs network access)
#   - /usr/local/bin/gel-start, gel-stop, gel-ready: start/stop/probe the local server
#   - /var/lib/gel-task/data: pre-bootstrapped, pre-migrated and pre-seeded data dir
#   - /home/user/harvest_api: Gel project (gel.toml, dbschema/default.gel,
#     dbschema/migrations, app/__init__.py, app/queries/__init__.py)
#   - /opt/task/seed.py, /opt/task/schema_checksum.py, /opt/task/schema.sha256

COPY test_initial_state.py /bootstrap/test_initial_state.py

# Setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
