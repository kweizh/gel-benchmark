FROM geldata/gel:7.1

ARG POCHI_VERSION

# The geldata/gel image ships an entrypoint that immediately launches the server.
# Clear it so this behaves like a normal task container; the server is started
# through the /usr/local/bin/gel-start helper instead.
ENTRYPOINT []

RUN apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl wget ca-certificates git python3 python3-pip bash ripgrep procps && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    curl -fsSL https://getpochi.com/install.sh | bash -s ${POCHI_VERSION} && \
    ln -s /root/.pochi/bin/pochi /usr/local/bin/pochi && \
    pip3 install --break-system-packages pytest pytest-json-ctrf && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Server layout: the image declares /var/lib/gel/data as a VOLUME, so the baked
# database lives under /opt/geldata instead (an explicit runstate dir is always
# passed as well, because the server runs as the unprivileged `gel` user).
ENV GEL_SERVER_DATADIR=/opt/geldata/data \
    GEL_SERVER_RUNSTATE_DIR=/opt/geldata/run \
    GEL_SERVER_PORT=5656 \
    GEL_SERVER_BIND_ADDRESS=127.0.0.1 \
    GEL_SERVER_SECURITY=insecure_dev_mode \
    GEL_SERVER_DEFAULT_AUTH_METHOD=Trust \
    GEL_SERVER_TLS_CERT_MODE=generate_self_signed \
    GEL_SERVER_COMPILER_POOL_SIZE=1 \
    GEL_DSN=gel://admin@127.0.0.1:5656 \
    GEL_CLIENT_TLS_SECURITY=insecure

RUN mkdir -p /home/user && chmod 0755 /home/user

RUN mkdir -p /usr/local/bin && \
    cat >/usr/local/bin/gel-serve <<'EOF'
#!/usr/bin/env bash
# Run the bundled Gel server in the foreground using the image's own launcher,
# so the server binary name never has to be guessed.
exec /usr/local/bin/docker-entrypoint.sh server
EOF

RUN cat >/usr/local/bin/gel-start <<'EOF'
#!/usr/bin/env bash
# Start the local Gel server in the background and wait until it answers
# queries. Idempotent: does nothing when the server is already up.
LOG_FILE="/var/log/gel-server.log"

if gel query "select 1" >/dev/null 2>&1; then
  echo "Gel server is already accepting queries."
  exit 0
fi

mkdir -p "${GEL_SERVER_RUNSTATE_DIR}" "${GEL_SERVER_DATADIR}"
chown gel:gel "${GEL_SERVER_RUNSTATE_DIR}" "${GEL_SERVER_DATADIR}" 2>/dev/null || true
touch "${LOG_FILE}" 2>/dev/null || true

nohup /usr/local/bin/gel-serve >>"${LOG_FILE}" 2>&1 &

for _ in $(seq 1 150); do
  if gel query "select 1" >/dev/null 2>&1; then
    echo "Gel server ready on port ${GEL_SERVER_PORT}."
    exit 0
  fi
  sleep 2
done

echo "Gel server did not become ready in time. Last log lines:" >&2
tail -n 40 "${LOG_FILE}" >&2 || true
exit 1
EOF

RUN mkdir -p /home/user/catalog/dbschema/migrations && \
    cd /home/user/catalog && \
    cat >gel.toml <<'EOF'
[instance]
server-version = "7.1"
EOF

RUN cd /home/user/catalog && \
    cat >dbschema/default.gel <<'EOF'
module default {
  type Category {
    required slug: str;
    required name: str;
    parent: Category;
  }

  type Product {
    required sku: str {
      constraint exclusive;
    }
    required name: str;
    required price: decimal;
    required stock: int64;
    required category: Category;
  }
}
EOF

RUN mkdir -p /opt/catalog-seed && \
    cat >/opt/catalog-seed/seed.edgeql <<'EOF'
delete Product;
delete Category;
for spec in {
  ('tools', 'Tools'), ('garden', 'Garden & Outdoor'), ('home', 'Home')
} union (insert Category { slug := spec.0, name := spec.1 });
for spec in {
  ('power', 'Power Tools', 'tools'), ('hand', 'Hand Tools', 'tools'), ('storage', 'Storage', 'tools'),
  ('clearance', 'Clearance', 'tools'),
  ('watering', 'Watering', 'garden'), ('seeds', 'Seeds', 'garden'), ('furniture', 'Garden Furniture', 'garden'),
  ('kitchen', 'Kitchen', 'home'), ('lighting', 'Lighting', 'home')
} union (insert Category { slug := spec.0, name := spec.1, parent := assert_single((select detached Category filter .slug = spec.2)) });
for spec in {
  ('drills', 'Drills', 'power'), ('saws', 'Saws', 'power'), ('sanders', 'Sanders', 'power'),
  ('wrenches', 'Wrenches', 'hand'), ('screwdrivers', 'Screwdrivers', 'hand'),
  ('spares', 'Spare Parts', 'storage'),
  ('hoses', 'Hoses', 'watering'), ('sprinklers', 'Sprinklers', 'watering'),
  ('cookware', 'Cookware', 'kitchen'), ('cutlery', 'Cutlery', 'kitchen'), ('bulbs', 'Light Bulbs', 'lighting')
} union (insert Category { slug := spec.0, name := spec.1, parent := assert_single((select detached Category filter .slug = spec.2)) });
for spec in {
  ('cordless', 'Cordless Drills', 'drills'), ('hammer', 'Hammer Drills', 'drills'), ('pans', 'Pans', 'cookware')
} union (insert Category { slug := spec.0, name := spec.1, parent := assert_single((select detached Category filter .slug = spec.2)) });
for spec in {
  ('cordless', 9), ('hammer', 5), ('saws', 8), ('sanders', 4), ('wrenches', 7), ('screwdrivers', 6),
  ('storage', 3), ('hoses', 8), ('sprinklers', 5), ('seeds', 12), ('furniture', 4), ('cookware', 6),
  ('pans', 7), ('cutlery', 5), ('bulbs', 9), ('kitchen', 3), ('power', 2), ('tools', 2), ('garden', 4), ('home', 3)
} union (
  with c := assert_single((select detached Category filter .slug = spec.0))
  for i in range_unpack(range(1, <int64>spec.1 + 1)) union (
    insert Product {
      sku := str_upper(spec.0) ++ '-' ++ to_str(i * 3 + 100),
      name := c.name ++ ' item ' ++ to_str(i),
      price := <decimal>((i * 7 + len(spec.0) * 13) % 89 + 3) + <decimal>0.49 * <decimal>((i % 3) + 1),
      stock := (i * 5 + len(spec.0) * 3) % 37,
      category := c
    }
  )
);
EOF

# Bake the initial database state: bootstrap the server, apply the first
# migration, seed the catalog, then shut the server down gracefully.
RUN chmod 0755 /usr/local/bin/gel-serve /usr/local/bin/gel-start && \
    mkdir -p /opt/geldata/data /opt/geldata/run && \
    chown -R gel:gel /opt/geldata && \
    /usr/local/bin/gel-start && \
    cd /home/user/catalog && \
    gel migration create --non-interactive && \
    gel migrate && \
    gel query -f /opt/catalog-seed/seed.edgeql >/dev/null && \
    gel query -F json "select count(Category)" && \
    gel query -F json "select count(Product)" && \
    gel migration status && \
    ls -1 /home/user/catalog/dbschema/migrations/*.edgeql && \
    pkill -TERM -f gel-server && \
    sleep 10 && \
    echo "baked gel instance shut down"

LABEL Author="generate-harbor-tasks"
LABEL Description="Local Gel 7.1 server with a seeded 26-category / 112-product catalog project at /home/user/catalog for building self-maintaining ancestry data and subtree roll-up queries."

CMD ["/bin/bash", "-c", "trap : TERM INT; sleep infinity & wait"]

# Container contents:
#   - base image geldata/gel:7.1 (Debian bookworm) providing the gel CLI and the bundled Gel 7.1 server
#   - always required apt packages: curl wget ca-certificates git python3 python3-pip bash ripgrep (plus procps)
#   - pip3 packages: pytest, pytest-json-ctrf (pytest comes from pip, never from apt)
#   - Pochi CLI installed and symlinked into /usr/local/bin
#   - /usr/local/bin/gel-serve: foreground server launcher delegating to the image's docker-entrypoint.sh
#   - /usr/local/bin/gel-start: idempotent background start + readiness wait helper
#   - /opt/geldata/data + /opt/geldata/run: baked data dir and explicit runstate dir owned by the gel user
#   - /home/user/catalog: gel.toml, dbschema/default.gel (Category/Product only) and one applied migration
#   - /opt/catalog-seed/seed.edgeql: the script used at build time to seed 26 categories and 112 products

COPY test_initial_state.py /bootstrap/test_initial_state.py

# Setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
