FROM geldata/gel:7.1

SHELL ["/bin/bash", "-c"]

ARG POCHI_VERSION

# The base image declares VOLUME /var/lib/gel/data, so anything written there during
# the build would be discarded. Keep the pre-seeded cluster outside of that volume.
ENV GEL_SERVER_DATADIR=/var/lib/gelbench/data
ENV GEL_SERVER_SECURITY=insecure_dev_mode
ENV GEL_SERVER_COMPILER_POOL_MODE=on_demand
ENV GEL_SERVER_COMPILER_POOL_SIZE=2
ENV GEL_DSN=gel://admin@127.0.0.1:5656/main
ENV GEL_CLIENT_TLS_SECURITY=insecure

RUN apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl wget ca-certificates git python3 python3-pip python3-pytest bash ripgrep \
        unzip procps jq && \
    ln -sf /usr/bin/python3 /usr/bin/python && \
    curl -fsSL https://getpochi.com/install.sh | bash -s ${POCHI_VERSION} && \
    ln -sf /root/.pochi/bin/pochi /usr/local/bin/pochi && \
    pip3 install --break-system-packages --no-cache-dir pytest-json-ctrf gel==3.1.0 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN mkdir -p /home/user && chmod 0755 /home/user

# ---------------------------------------------------------------------------
# helper that boots the in-container Gel server and waits until it is ready
# ---------------------------------------------------------------------------
RUN cat >/usr/local/bin/gel-start <<'EOF'
#!/usr/bin/env bash
# Idempotently start the bundled Gel server and block until it accepts queries.
set -uo pipefail

LOG=/var/log/gel-server.log
PIDFILE=/run/gel-start.pid

mkdir -p /var/log /run

is_ready() {
  curl -fsS --max-time 3 http://127.0.0.1:5656/server/status/ready >/dev/null 2>&1 && return 0
  curl -fsS -k --max-time 3 https://127.0.0.1:5656/server/status/ready >/dev/null 2>&1 && return 0
  gel --connect-timeout 2s --wait-until-available 1s query "select 1" >/dev/null 2>&1 && return 0
  return 1
}

if is_ready; then
  echo "gel server is already running"
  exit 0
fi

running=""
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null; then
  running="yes"
fi
if pgrep -f "gel-server" >/dev/null 2>&1; then
  running="yes"
fi

if [ -z "$running" ]; then
  echo "starting gel server (log: $LOG)"
  nohup /usr/local/bin/docker-entrypoint.sh server >>"$LOG" 2>&1 &
  echo $! >"$PIDFILE"
fi

deadline=$(( $(date +%s) + 300 ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  if is_ready; then
    echo "gel server is ready"
    exit 0
  fi
  sleep 1
done

echo "gel server did not become ready in time" >&2
tail -n 120 "$LOG" >&2 2>/dev/null
exit 1
EOF

RUN chmod 0755 /usr/local/bin/gel-start

# ---------------------------------------------------------------------------
# pre-existing Gel project
# ---------------------------------------------------------------------------
RUN mkdir -p /home/user/catalog/dbschema && \
    cat >/home/user/catalog/gel.toml <<'EOF'
[instance]
server-version = "7.1"

[project]
schema-dir = "dbschema"
EOF

RUN cat >/home/user/catalog/dbschema/default.gel <<'EOF'
module default {
  abstract type Node {
    required slug: str {
      constraint exclusive;
    }
    required label: str;
  }

  type Category extending Node {
    parent: Category;
    required rank: int64 {
      default := 0;
    }
  }

  abstract type Listing extending Node {
    required category: Category;
    required price_cents: int64 {
      constraint min_value(0);
    }
  }

  type Product extending Listing {
    required in_stock: bool {
      default := true;
    }
  }

  type Bundle extending Listing {
    required item_count: int64 {
      constraint min_value(2);
    }
  }

  type CategoryAudit {
    required category: Category {
      constraint exclusive;
    }
    required checked_by: str;
  }
}
EOF

# ---------------------------------------------------------------------------
# deterministic seed data
# ---------------------------------------------------------------------------
RUN mkdir -p /usr/local/share/gelbench && \
    cat >/usr/local/share/gelbench/seed.edgeql <<'EOF'
# --- explicit category tree ------------------------------------------------
insert Category { slug := 'electronics', label := 'Electronics', rank := 1 };
insert Category {
  slug := 'audio', label := 'Audio', rank := 1,
  parent := assert_single((select detached Category filter .slug = 'electronics'))
};
insert Category {
  slug := 'headphones', label := 'Headphones', rank := 1,
  parent := assert_single((select detached Category filter .slug = 'audio'))
};
insert Category {
  slug := 'wireless', label := 'Wireless', rank := 1,
  parent := assert_single((select detached Category filter .slug = 'headphones'))
};
insert Category {
  slug := 'wired', label := 'Wired', rank := 2,
  parent := assert_single((select detached Category filter .slug = 'headphones'))
};
insert Category {
  slug := 'speakers', label := 'Speakers', rank := 2,
  parent := assert_single((select detached Category filter .slug = 'audio'))
};
insert Category {
  slug := 'cameras', label := 'Cameras', rank := 2,
  parent := assert_single((select detached Category filter .slug = 'electronics'))
};
insert Category {
  slug := 'lenses', label := 'Lenses', rank := 1,
  parent := assert_single((select detached Category filter .slug = 'cameras'))
};
insert Category {
  slug := 'storage', label := 'Storage', rank := 3,
  parent := assert_single((select detached Category filter .slug = 'electronics'))
};
insert Category {
  slug := 'movable', label := 'Movable', rank := 1,
  parent := assert_single((select detached Category filter .slug = 'storage'))
};
insert Category { slug := 'sandbox', label := 'Sandbox', rank := 9 };

# --- broken legacy data: a closed parent loop ------------------------------
insert Category { slug := 'loop-a', label := 'Loop A', rank := 1 };
insert Category { slug := 'loop-b', label := 'Loop B', rank := 2 };
insert Category { slug := 'loop-c', label := 'Loop C', rank := 3 };
update Category filter .slug = 'loop-a'
set { parent := assert_single((select detached Category filter .slug = 'loop-b')) };
update Category filter .slug = 'loop-b'
set { parent := assert_single((select detached Category filter .slug = 'loop-a')) };
update Category filter .slug = 'loop-c'
set { parent := assert_single((select detached Category filter .slug = 'loop-a')) };

# --- explicit listings -----------------------------------------------------
insert Product {
  slug := 'cable-basic', label := 'Basic Cable', price_cents := 500, in_stock := true,
  category := assert_single((select detached Category filter .slug = 'electronics'))
};
insert Product {
  slug := 'hp-alpha', label := 'Headphone Alpha', price_cents := 12000, in_stock := true,
  category := assert_single((select detached Category filter .slug = 'headphones'))
};
insert Product {
  slug := 'hp-beta', label := 'Headphone Beta', price_cents := 8000, in_stock := false,
  category := assert_single((select detached Category filter .slug = 'headphones'))
};
insert Product {
  slug := 'hp-wl-1', label := 'Wireless One', price_cents := 15000, in_stock := true,
  category := assert_single((select detached Category filter .slug = 'wireless'))
};
insert Product {
  slug := 'hp-wd-1', label := 'Wired One', price_cents := 4000, in_stock := true,
  category := assert_single((select detached Category filter .slug = 'wired'))
};
insert Product {
  slug := 'spk-1', label := 'Speaker One', price_cents := 7000, in_stock := false,
  category := assert_single((select detached Category filter .slug = 'speakers'))
};
insert Product {
  slug := 'cam-1', label := 'Camera One', price_cents := 55000, in_stock := true,
  category := assert_single((select detached Category filter .slug = 'cameras'))
};
insert Product {
  slug := 'lens-1', label := 'Lens One', price_cents := 33000, in_stock := true,
  category := assert_single((select detached Category filter .slug = 'lenses'))
};
insert Product {
  slug := 'lens-2', label := 'Lens Two', price_cents := 21000, in_stock := false,
  category := assert_single((select detached Category filter .slug = 'lenses'))
};
insert Bundle {
  slug := 'audio-starter', label := 'Audio Starter', price_cents := 9900, item_count := 3,
  category := assert_single((select detached Category filter .slug = 'audio'))
};
insert Bundle {
  slug := 'wl-bundle', label := 'Wireless Bundle', price_cents := 25000, item_count := 2,
  category := assert_single((select detached Category filter .slug = 'wireless'))
};

# --- audits ----------------------------------------------------------------
insert CategoryAudit {
  category := assert_single((select detached Category filter .slug = 'electronics')),
  checked_by := 'qa-bob'
};
insert CategoryAudit {
  category := assert_single((select detached Category filter .slug = 'audio')),
  checked_by := 'qa-anna'
};

# --- generated bulk data: a 40 level spine with 20 bins per level -----------
for i in range_unpack(range(0, 40)) union (
  insert Category {
    slug := 'spine-' ++ <str>i,
    label := 'Spine ' ++ <str>i,
    rank := i
  }
);
for i in range_unpack(range(1, 40)) union (
  update Category filter .slug = 'spine-' ++ <str>i
  set {
    parent := assert_single((
      select detached Category filter .slug = 'spine-' ++ <str>(i - 1)
    ))
  }
);
for i in range_unpack(range(0, 40)) union (
  for j in range_unpack(range(0, 20)) union (
    insert Category {
      slug := 'bin-' ++ <str>i ++ '-' ++ <str>j,
      label := 'Bin ' ++ <str>i ++ '/' ++ <str>j,
      rank := j,
      parent := assert_single((
        select detached Category filter .slug = 'spine-' ++ <str>i
      ))
    }
  )
);
for i in range_unpack(range(0, 40)) union (
  for j in range_unpack(range(0, 20)) union (
    for k in range_unpack(range(0, 2)) union (
      insert Product {
        slug := 'sku-' ++ <str>i ++ '-' ++ <str>j ++ '-' ++ <str>k,
        label := 'SKU ' ++ <str>i ++ '/' ++ <str>j ++ '/' ++ <str>k,
        price_cents := 100 * (i + 1) + 10 * j + k,
        in_stock := ((i + j + k) % 3) != 0,
        category := assert_single((
          select detached Category
          filter .slug = 'bin-' ++ <str>i ++ '-' ++ <str>j
        ))
      }
    )
  )
);
EOF

# ---------------------------------------------------------------------------
# build-time bootstrap: create the instance, apply the first migration, seed
# ---------------------------------------------------------------------------
RUN cat >/usr/local/share/gelbench/bootstrap.sh <<'EOF'
#!/usr/bin/env bash
set -uo pipefail

LOG=/var/log/gel-build.log
mkdir -p /var/log /var/lib/gelbench

die() {
  echo "BOOTSTRAP FAILURE: $*" >&2
  tail -n 200 "$LOG" >&2 2>/dev/null
  exit 1
}

echo "== booting gel server for image bootstrap =="
nohup /usr/local/bin/docker-entrypoint.sh server >>"$LOG" 2>&1 &
SERVER_PID=$!

ready=""
deadline=$(( $(date +%s) + 600 ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  if curl -fsS --max-time 3 http://127.0.0.1:5656/server/status/ready >/dev/null 2>&1; then
    ready="yes"
    break
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    die "the gel server exited while bootstrapping the data directory"
  fi
  sleep 1
done
[ -n "$ready" ] || die "the gel server never became ready"

gel --wait-until-available 30s query "select 1" >/dev/null || die "cannot query the instance"

cd /home/user/catalog || die "missing project directory"
gel migration create --non-interactive || die "gel migration create failed"
gel migrate || die "gel migrate failed"
gel query -f /usr/local/share/gelbench/seed.edgeql >/dev/null || die "seeding failed"

# Best effort: also register the instance and link the project, so the CLI works
# even when the connection environment variables are not present.
env -u GEL_DSN -u GEL_CLIENT_TLS_SECURITY gel instance link \
      --dsn "gel://admin@127.0.0.1:5656" --trust-tls-cert --non-interactive \
      --overwrite catalog_local \
  || echo "warning: 'gel instance link' did not succeed"
env -u GEL_DSN -u GEL_CLIENT_TLS_SECURITY gel project init \
      --link --server-instance catalog_local --non-interactive --no-migrations \
      --project-dir /home/user/catalog \
  || echo "warning: 'gel project init --link' did not succeed"

gel query -F json-lines "select count(Category)" | tr -d '[:space:]' | grep -q '^854$' \
  || die "expected 854 seeded categories"
gel query -F json-lines "select count(Product)" | tr -d '[:space:]' | grep -q '^1609$' \
  || die "expected 1609 seeded products"
gel query -F json-lines "select count(Bundle)" | tr -d '[:space:]' | grep -q '^2$' \
  || die "expected 2 seeded bundles"
gel query -F json-lines "select count(CategoryAudit)" | tr -d '[:space:]' | grep -q '^2$' \
  || die "expected 2 seeded audit rows"
gel migration status --quiet || die "the seeded instance is not in sync with dbschema"

echo "== stopping gel server =="
kill -TERM "$SERVER_PID" 2>/dev/null
stop_deadline=$(( $(date +%s) + 180 ))
while [ "$(date +%s)" -lt "$stop_deadline" ]; do
  kill -0 "$SERVER_PID" 2>/dev/null || break
  sleep 1
done
pkill -TERM -f "gel-server" 2>/dev/null
sleep 3
rm -f /run/gel-start.pid
echo "== bootstrap complete =="
EOF

RUN chmod 0755 /usr/local/share/gelbench/bootstrap.sh && \
    /usr/local/share/gelbench/bootstrap.sh

LABEL Author="generate-harbor-tasks"
LABEL Description="Gel 7.1 server running inside the container with a pre-seeded, migrated category/listing catalog project at /home/user/catalog."

# The base image ships an entrypoint that would otherwise wrap every command.
ENTRYPOINT []

CMD ["/bin/bash", "-c", "trap : TERM INT; sleep infinity & wait"]

# Container contents:
#   - base image geldata/gel:7.1 (Debian bookworm): bundled gel-server 7.1 and the `gel` CLI
#   - always required apt packages: curl wget ca-certificates git python3 python3-pip python3-pytest bash ripgrep
#   - extra apt packages: unzip procps jq (used by the entrypoint and the start helper)
#   - always required pip3 packages: pytest-json-ctrf (plus the `gel` Python client 3.1.0)
#   - /usr/local/bin/gel-start: idempotent helper that boots the local Gel server and waits for readiness
#   - /var/lib/gelbench/data: pre-bootstrapped Gel data directory (outside the base image VOLUME)
#   - /home/user/catalog: pre-existing Gel project (gel.toml, dbschema/default.gel, dbschema/migrations)
#   - seeded dataset: 854 categories (including a 40 level spine and a broken parent loop),
#     1609 products, 2 bundles, 2 category audits
#   - connection settings provided through GEL_DSN / GEL_CLIENT_TLS_SECURITY

COPY test_initial_state.py /bootstrap/test_initial_state.py

# Setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
