FROM ubuntu:24.04

ARG POCHI_VERSION
ARG GEL_CLI_VERSION=7.10.2
ARG GEL_SERVER_VERSION=7.1

RUN apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl wget ca-certificates git python3 python3-pip python3-pytest bash ripgrep \
        jq procps less nano vim-tiny util-linux locales && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    curl -fsSL https://getpochi.com/install.sh | bash -s ${POCHI_VERSION} && \
    ln -s /root/.pochi/bin/pochi /usr/local/bin/pochi && \
    pip3 install --break-system-packages pytest-json-ctrf && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# --- Gel CLI (pinned) ---------------------------------------------------------
RUN arch="$(uname -m)" && \
    case "$arch" in \
        x86_64) target="x86_64-unknown-linux-musl" ;; \
        aarch64|arm64) target="aarch64-unknown-linux-musl" ;; \
        *) echo "unsupported architecture: $arch" && exit 1 ;; \
    esac && \
    ref="$(curl -fsSL "https://packages.geldata.com/archive/.jsonindexes/${target}.json" \
        | jq -r --arg v "${GEL_CLI_VERSION}+" '[.packages[] | select(.name == "gel-cli" and (.version | startswith($v)))] | last | .installref')" && \
    if [ -n "$ref" ] && [ "$ref" != "null" ]; then \
        curl -fsSL "https://packages.geldata.com${ref}" -o /usr/local/bin/gel; \
    else \
        curl --proto '=https' --tlsv1.2 -sSf https://www.geldata.com/sh | sh -s -- -y --no-modify-path && \
        cp /root/.local/bin/gel /usr/local/bin/gel; \
    fi && \
    chmod 0755 /usr/local/bin/gel && \
    /usr/local/bin/gel --version

# --- Gel server (portable build, relocated so a non-root account can run it) --
RUN gel server install --version "${GEL_SERVER_VERSION}" && \
    mkdir -p /opt/gel && \
    mv "/root/.local/share/edgedb/portable/${GEL_SERVER_VERSION}" "/opt/gel/${GEL_SERVER_VERSION}" && \
    ln -s "/opt/gel/${GEL_SERVER_VERSION}" /opt/gel/current && \
    chmod -R a+rX /opt/gel && \
    useradd --system --create-home --home-dir /var/lib/gel --shell /usr/sbin/nologin gelsrv && \
    mkdir -p /var/lib/gel/data /var/lib/gel/run /var/log/gel && \
    chown -R gelsrv:gelsrv /var/lib/gel /var/log/gel

# --- helper that boots the local instance and blocks until it is reachable ----
RUN cat >/usr/local/bin/gel-start <<'EOF'
#!/bin/bash
# Boot the local Gel instance "devinst" (server runs as the unprivileged
# `gelsrv` account) and block until it accepts connections.
DSN="gel://admin@127.0.0.1:5656/main"
probe() { gel --dsn "$DSN" --tls-security insecure query "select 1" >/dev/null 2>&1; }
if probe; then exit 0; fi
mkdir -p /var/lib/gel/run /var/log/gel
chown gelsrv:gelsrv /var/lib/gel/run /var/log/gel 2>/dev/null
(setsid nohup runuser -u gelsrv -- /opt/gel/current/bin/gel-server \
    --data-dir=/var/lib/gel/data \
    --runstate-dir=/var/lib/gel/run \
    --port=5656 \
    --bind-address=127.0.0.1 \
    --security=insecure_dev_mode \
    --tls-cert-mode=generate_self_signed \
    --compiler-pool-mode=on_demand \
    >>/var/log/gel/server.log 2>&1 &) >/dev/null 2>&1
for _ in $(seq 1 180); do
    probe && exit 0
    sleep 1
done
echo "gel-start: instance devinst did not become reachable" >&2
tail -n 40 /var/log/gel/server.log >&2
exit 1
EOF
RUN chmod 0755 /usr/local/bin/gel-start

RUN mkdir -p /home/user && chmod 0755 /home/user

# --- bootstrap the database, link it as instance `devinst`, seed the project --
RUN runuser -u gelsrv -- /opt/gel/current/bin/gel-server \
        --data-dir=/var/lib/gel/data \
        --runstate-dir=/var/lib/gel/run \
        --port=5656 \
        --bind-address=127.0.0.1 \
        --security=insecure_dev_mode \
        --tls-cert-mode=generate_self_signed \
        --bootstrap-only && \
    /usr/local/bin/gel-start && \
    gel instance link \
        --non-interactive --trust-tls-cert --overwrite --quiet \
        --host 127.0.0.1 --port 5656 --user admin --branch main --no-password \
        devinst && \
    gel -I devinst query "select sys::get_version_as_str()"

RUN mkdir -p /home/user/branchlab/dbschema && \
    cat >/home/user/branchlab/gel.toml <<'EOF'
[instance]
server-version = "7.1"
EOF

RUN cat >/home/user/branchlab/dbschema/default.gel <<'EOF'
module default {
  type Author {
    required name: str {
      constraint exclusive;
    }
    required country: str;
  }

  type Article {
    required title: str {
      constraint exclusive;
    }
    required word_count: int64;
    required author: Author;
  }
}
EOF

RUN mkdir -p /opt/task-fixtures && \
    cat >/opt/task-fixtures/seed_authors.edgeql <<'EOF'
insert Author { name := "Ada Lovelace", country := "UK" };
insert Author { name := "Grace Hopper", country := "US" };
insert Author { name := "Alan Turing", country := "UK" };
insert Author { name := "Katherine Johnson", country := "US" };
EOF

RUN cat >/opt/task-fixtures/seed_articles.edgeql <<'EOF'
for x in {
  ("Analytical Engines", 1500, "Ada Lovelace"),
  ("Notes on Bernoulli", 900, "Ada Lovelace"),
  ("Compiler Origins", 1200, "Grace Hopper"),
  ("Debugging Moths", 400, "Grace Hopper"),
  ("Machine Intelligence", 2400, "Alan Turing"),
  ("Morphogenesis Notes", 1100, "Alan Turing"),
  ("Orbital Mechanics", 1000, "Katherine Johnson"),
  ("Trajectory Tables", 750, "Katherine Johnson"),
  ("Punch Card Rituals", 1199, "Grace Hopper"),
  ("Enigma Sketches", 999, "Alan Turing"),
  ("Lunar Rendezvous", 1201, "Katherine Johnson"),
  ("Difference Engine Redux", 1600, "Ada Lovelace")
} union (
  insert Article {
    title := x.0,
    word_count := x.1,
    author := assert_single((select Author filter .name = x.2))
  }
);
EOF

RUN /usr/local/bin/gel-start && \
    cd /home/user/branchlab && \
    gel project init --link --server-instance devinst --non-interactive && \
    gel migration create --non-interactive && \
    gel migrate && \
    gel query -f /opt/task-fixtures/seed_authors.edgeql >/dev/null && \
    gel query -f /opt/task-fixtures/seed_articles.edgeql >/dev/null && \
    gel query -F json "select Article { id, title, word_count, author: { id, name, country } } order by .title" \
        >/opt/task-fixtures/initial_articles.json && \
    gel query -F json "select Author { id, name, country } order by .name" \
        >/opt/task-fixtures/initial_authors.json && \
    gel query -F json "select schema::Migration.name" | jq -r '.[0]' \
        >/opt/task-fixtures/initial_migration.txt && \
    rm -f /opt/task-fixtures/seed_authors.edgeql /opt/task-fixtures/seed_articles.edgeql && \
    chmod 0644 /opt/task-fixtures/*.json /opt/task-fixtures/initial_migration.txt && \
    gel migration status

RUN cd /home/user/branchlab && \
    git init -q -b main . && \
    git config user.email "dev@branchlab.example" && \
    git config user.name "Branchlab Dev" && \
    printf 'gel.local.toml\n' >.gitignore && \
    git add -A && \
    git commit -q -m "Initial editorial catalog schema and data"

WORKDIR /home/user/branchlab

LABEL Author="generate-harbor-tasks"
LABEL Description="Gel 7.1 branchlab project: a linked local instance with one applied migration and seeded catalog data, ready for a multi-branch schema reconciliation task."

CMD ["/bin/bash", "-c", "trap : TERM INT; sleep infinity & wait"]

# Container contents:
#   - always required apt packages: curl wget ca-certificates git python3 python3-pip python3-pytest bash ripgrep
#   - always required pip3 packages: pytest-json-ctrf
#   - /usr/local/bin/gel: pinned Gel CLI 7.10.2
#   - /opt/gel/current: portable Gel 7.1 server, executed by the unprivileged `gelsrv` account
#   - /usr/local/bin/gel-start: boots instance `devinst` on 127.0.0.1:5656 and waits for readiness
#   - /home/user/branchlab: Gel project (git repo) linked to instance `devinst`, branch `main`
#   - /home/user/branchlab/dbschema: base schema plus one applied migration
#   - /opt/task-fixtures: build-time snapshots of the seeded Author/Article rows and of the root migration name

COPY test_initial_state.py /bootstrap/test_initial_state.py

# Setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
