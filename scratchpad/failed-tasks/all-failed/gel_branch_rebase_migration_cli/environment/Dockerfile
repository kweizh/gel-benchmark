FROM ubuntu:24.04

ARG POCHI_VERSION

# --- base tooling, Gel 6 server + Gel CLI ------------------------------------
RUN apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl wget ca-certificates gnupg git python3 python3-pip bash ripgrep \
        procps util-linux locales && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    localedef -i en_US -f UTF-8 en_US.UTF-8 && \
    mkdir -p /usr/local/share/keyrings && \
    curl --proto '=https' --tlsv1.2 -sSf \
        -o /usr/local/share/keyrings/gel-keyring.gpg \
        https://packages.geldata.com/keys/gel-keyring.gpg && \
    echo "deb [signed-by=/usr/local/share/keyrings/gel-keyring.gpg] https://packages.geldata.com/apt noble main" \
        > /etc/apt/sources.list.d/gel.list && \
    apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        gel-server-6 gel-cli && \
    curl -fsSL https://getpochi.com/install.sh | bash -s ${POCHI_VERSION} && \
    ln -s /root/.pochi/bin/pochi /usr/local/bin/pochi && \
    pip3 install --break-system-packages pytest pytest-json-ctrf && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

ENV LANG=en_US.UTF-8
ENV LC_ALL=en_US.UTF-8

# The Gel CLI keeps credentials / project stashes / instance state in the XDG
# directories. Pin them so the CLI resolves the same project link no matter
# which account runs it (gel-server itself refuses to run as root).
ENV XDG_CONFIG_HOME=/home/user/.config
ENV XDG_DATA_HOME=/home/user/.local/share
ENV XDG_CACHE_HOME=/home/user/.cache

RUN mkdir -p /home/user && chmod 0755 /home/user

RUN useradd -d /home/user -s /bin/bash user && \
    mkdir -p /home/user/geldata /home/user/gelrun /home/user/crm/dbschema \
             /home/user/.config /home/user/.local/share /home/user/.cache && \
    chown -R user:user /home/user && \
    chmod 0777 /home/user/gelrun && \
    git config --system --add safe.directory /home/user/crm

# --- helper that (re)starts the local Gel server ------------------------------
RUN cat > /usr/local/bin/gel-server-run.sh <<'EOF'
#!/bin/bash
# Runs the local Gel server in the foreground. Never invoked as root: gel-server
# refuses to run as the root account.
exec >>/tmp/gel-server.log 2>&1
export GEL_SERVER_SECURITY=insecure_dev_mode
SERVER=""
for cand in gel-server gel-server-6; do
    if command -v "$cand" >/dev/null 2>&1; then
        SERVER="$(command -v "$cand")"
        break
    fi
done
if [ -z "$SERVER" ]; then
    SERVER="$(ls /usr/bin/gel-server* 2>/dev/null | head -n 1)"
fi
if [ -z "$SERVER" ]; then
    echo "gel-server-run.sh: no gel-server binary found" >&2
    exit 1
fi
exec "$SERVER" \
    --data-dir=/home/user/geldata \
    --runstate-dir=/home/user/gelrun \
    --port=5656 \
    --bind-address=127.0.0.1 \
    --tls-cert-mode=generate_self_signed \
    --default-branch=main
EOF

RUN cat > /usr/local/bin/gel-start.sh <<'EOF'
#!/bin/bash
# Idempotently start the local Gel server used by /home/user/crm.
probe() {
    gel --dsn 'gel://127.0.0.1:5656' --tls-security insecure query 'select 1' >/dev/null 2>&1
}
if probe; then
    exit 0
fi
mkdir -p /home/user/geldata /home/user/gelrun 2>/dev/null
touch /tmp/gel-server.log 2>/dev/null
chmod 0666 /tmp/gel-server.log 2>/dev/null
if [ "$(id -u)" = "0" ]; then
    chown user:user /home/user/geldata /home/user/gelrun 2>/dev/null
    nohup su user -s /bin/bash -c /usr/local/bin/gel-server-run.sh >/dev/null 2>&1 &
else
    nohup /usr/local/bin/gel-server-run.sh >/dev/null 2>&1 &
fi
for _ in $(seq 1 240); do
    if probe; then
        exit 0
    fi
    sleep 1
done
echo "gel-start.sh: the Gel server did not become ready in time" >&2
tail -n 40 /tmp/gel-server.log >&2
exit 1
EOF

RUN chmod 0755 /usr/local/bin/gel-server-run.sh /usr/local/bin/gel-start.sh

# --- baked project state ------------------------------------------------------
RUN cat > /tmp/schema_initial.gel <<'EOF'
module default {
  type Contact {
    required email: str {
      constraint exclusive;
    };
    required full_name: str;
    required stage: str {
      default := 'lead';
    };
    note: str;
  }
}
EOF

RUN cat > /tmp/schema_domain.gel <<'EOF'
module default {
  type Contact {
    required email: str {
      constraint exclusive;
    };
    required full_name: str;
    required domain: str;
    required stage: str {
      default := 'lead';
    };
    note: str;
  }
}
EOF

RUN cat > /tmp/seed_main.edgeql <<'EOF'
insert Contact { email := 'ada@Example.COM', full_name := 'Ada Lovelace', stage := 'active', note := 'q3 pipeline' };
insert Contact { email := 'prince@music.io', full_name := 'Prince', stage := 'lead' };
insert Contact { email := 'jl@fleet.ORG', full_name := 'Jean  Luc Picard', stage := 'customer' };
insert Contact { email := 'grace@navy.mil', full_name := '  Grace Hopper ', stage := 'active', note := 'renewal' };
insert Contact { email := 'renee@studio.fr', full_name := 'Renée Dupont-Laurent', stage := 'lead' };
insert Contact { email := 'mary@daily.NET', full_name := 'Mary Jane Watson', stage := 'churned', note := 'lost to competitor' };
insert Contact { email := 'sean@pub.IE', full_name := "Seán O'Connor", stage := 'lead' };
insert Contact { email := 'ludwig@musik.DE', full_name := 'Ludwig van Beethoven', stage := 'customer' };
insert Contact { email := 'kim@studio.kr', full_name := 'Kim', stage := 'lead' };
insert Contact { email := 'wei@lab.CN', full_name := 'Wei  Zhang', stage := 'active', note := 'trial extended' };
insert Contact { email := 'ann@ops.io', full_name := 'Ann-Marie  de la Cruz', stage := 'lead' };
insert Contact { email := 'zed@ops.IO', full_name := ' Zed ', stage := 'churned' };
EOF

RUN cat > /tmp/seed_sandbox.edgeql <<'EOF'
insert Contact { email := 'draft-a@sandbox.test', full_name := 'Draft Alpha', stage := 'lead' };
insert Contact { email := 'draft-b@sandbox.test', full_name := 'Draft Beta', stage := 'lead' };
insert Contact { email := 'draft-c@sandbox.test', full_name := 'Draft Gamma', stage := 'lead' };
EOF

RUN cat > /tmp/bake.sh <<'EOF'
#!/bin/bash
set -eux
cd /home/user

/usr/local/bin/gel-start.sh

gel instance link --dsn 'gel://127.0.0.1:5656' --non-interactive --trust-tls-cert \
    --overwrite crm_instance

cat > /home/user/crm/gel.toml <<'TOML'
[instance]
server-version = "6"
TOML
cp /tmp/schema_initial.gel /home/user/crm/dbschema/default.gel

cd /home/user/crm
gel project init --link --server-instance crm_instance --non-interactive
gel migration create --non-interactive
gel migrate

git init -q -b main .
git config user.email "dev@crm.test"
git config user.name "CRM Dev"
printf 'gel.local.toml\n' > .gitignore
git add -A
git commit -qm "crm: initial Contact schema"
INITIAL_COMMIT="$(git rev-parse HEAD)"

gel query -f /tmp/seed_main.edgeql

cp /tmp/schema_domain.gel /home/user/crm/dbschema/default.gel
printf 'y\nstr_lower(str_split(.email, "@")[1])\n' | gel migration create --expert
gel migrate
git add -A
git commit -qm "crm: derive the domain property from email"

# The feature line diverged before the domain work landed on main.
git branch split_names "$INITIAL_COMMIT"
git checkout -q split_names
gel branch create split_names --empty
gel branch switch split_names
gel migrate
gel query -f /tmp/seed_sandbox.edgeql

# An abandoned experiment that is still lying around on the instance.
gel branch create stale_prototype --empty

# sanity checks on the baked state
test "$(gel query -F json 'select sys::get_current_branch()')" = '["split_names"]'
test "$(gel query --branch main -F json 'select count(Contact)')" = '[12]'
test "$(gel query --branch split_names -F json 'select count(Contact)')" = '[3]'
test "$(gel migration log --from-db --branch main | wc -l)" = "2"
test "$(gel migration log --from-db --branch split_names | wc -l)" = "1"
test "$(ls /home/user/crm/dbschema/migrations/*.edgeql | wc -l)" = "1"
test -z "$(git status --porcelain)"
gel migration log --from-db --branch main > /tmp/baked_history.txt
EOF

RUN chmod 0755 /tmp/bake.sh && \
    su user -s /bin/bash -c /tmp/bake.sh && \
    echo "--- baked migration history ---" && \
    cat /tmp/baked_history.txt && \
    pkill -TERM -f gel-server || true

RUN for i in $(seq 1 90); do \
        if gel --dsn 'gel://127.0.0.1:5656' --tls-security insecure query 'select 1' >/dev/null 2>&1; then \
            sleep 1; \
        else \
            break; \
        fi; \
    done && \
    rm -f /tmp/schema_initial.gel /tmp/schema_domain.gel /tmp/seed_main.edgeql \
          /tmp/seed_sandbox.edgeql /tmp/bake.sh && \
    chown -R user:user /home/user

LABEL Author="generate-harbor-tasks"
LABEL Description="Gel 6 CRM project with diverged git/Gel branches (main ahead by a domain migration, split_names behind) for a branch rebase + merge + data-preserving name-split task."

CMD ["/bin/bash", "-c", "trap : TERM INT; sleep infinity & wait"]

# Container contents:
#   - always required apt packages: curl wget ca-certificates git python3 python3-pip bash ripgrep
#     (python3-pytest is deliberately NOT installed: the distro pytest breaks pytest-json-ctrf)
#   - always required pip3 packages: pytest, pytest-json-ctrf
#   - gel-server-6 + gel-cli from packages.geldata.com, plus the Pochi CLI
#   - /usr/local/bin/gel-start.sh, /usr/local/bin/gel-server-run.sh: idempotent local server startup
#     (explicit pre-created, chowned --runstate-dir; server runs as the unprivileged "user")
#   - /home/user/geldata: Gel data directory holding branches main, split_names, stale_prototype
#   - /home/user/crm: git-tracked Gel project (gel.toml, dbschema/default.gel, dbschema/migrations)
#       * Gel branch main: initial migration + domain migration, 12 seeded contacts
#       * Gel branch split_names: initial migration only, 3 @sandbox.test contacts (current branch)
#       * Gel branch stale_prototype: abandoned empty branch
#       * git branch main: 2 commits; git branch split_names: checked out at the first commit

COPY test_initial_state.py /bootstrap/test_initial_state.py

# Setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
