FROM ubuntu:24.04

ARG POCHI_VERSION

RUN apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl wget ca-certificates git python3 python3-pip python3-pytest bash ripgrep \
        gnupg jq procps less && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    curl -fsSL https://getpochi.com/install.sh | bash -s ${POCHI_VERSION} && \
    ln -s /root/.pochi/bin/pochi /usr/local/bin/pochi && \
    pip3 install --break-system-packages --retries 10 --timeout 60 \
        pytest-json-ctrf "gel==3.1.0" && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Gel 6 server (bundles the `gel` CLI) from the official APT repository.
RUN mkdir -p /usr/local/share/keyrings && \
    curl --proto '=https' --tlsv1.2 -sSf \
        -o /usr/local/share/keyrings/gel-keyring.gpg \
        https://packages.geldata.com/keys/gel-keyring.gpg && \
    echo "deb [signed-by=/usr/local/share/keyrings/gel-keyring.gpg] https://packages.geldata.com/apt noble main" \
        > /etc/apt/sources.list.d/gel.list && \
    apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends gel-6 && \
    ln -sf /usr/bin/gel-server-6 /usr/local/bin/gel-server-6 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Connection settings for both the `gel` CLI and the Gel Python client. The local
# instance runs in insecure dev mode, so no password or certificate is needed.
ENV GEL_DSN="gel://admin@localhost:5656/main"
ENV GEL_CLIENT_TLS_SECURITY="insecure"
ENV GEL_DATA_DIR="/var/lib/gel/6/data"
ENV GEL_RUNSTATE_DIR="/run/gel"
ENV GEL_SERVER_LOG="/var/log/gel-server.log"

# Initialise the data directory at build time so that the first start is fast.
# gel-server refuses to run as root, so everything runs as the packaged `gel` user.
RUN install -d -o gel -g gel -m 0700 /var/lib/gel/6/data && \
    install -d -o gel -g gel -m 0755 /run/gel /tmp/gel-bootstrap-run && \
    touch /var/log/gel-server.log && \
    chown gel:gel /var/log/gel-server.log && \
    su gel -s /bin/bash -c "/usr/bin/gel-server-6 \
        --data-dir=/var/lib/gel/6/data \
        --runstate-dir=/tmp/gel-bootstrap-run \
        --security=insecure_dev_mode \
        --compiler-pool-size=1 \
        --bootstrap-only" && \
    rm -rf /tmp/gel-bootstrap-run

# Idempotent helper that brings the local Gel server up and waits for readiness.
RUN cat > /usr/local/bin/start-gel.sh <<'START_EOF'
#!/bin/bash
# Start the local Gel 6 server in the background (idempotent).
# Returns 0 as soon as the server answers queries.

DATA_DIR="${GEL_DATA_DIR:-/var/lib/gel/6/data}"
RUNSTATE_DIR="${GEL_RUNSTATE_DIR:-/run/gel}"
LOG_FILE="${GEL_SERVER_LOG:-/var/log/gel-server.log}"
SERVER_BIN="/usr/bin/gel-server-6"

ready() {
    gel query 'select 1' >/dev/null 2>&1
}

if ready; then
    echo "gel server already running"
    exit 0
fi

mkdir -p "$RUNSTATE_DIR"
chown gel:gel "$RUNSTATE_DIR" 2>/dev/null
touch "$LOG_FILE" 2>/dev/null
chown gel:gel "$LOG_FILE" 2>/dev/null

CMD="$SERVER_BIN --data-dir=$DATA_DIR --runstate-dir=$RUNSTATE_DIR"
CMD="$CMD --security=insecure_dev_mode --bind-address=127.0.0.1 --port=5656"
CMD="$CMD --admin-ui=disabled --compiler-pool-size=2"

if [ "$(id -u)" = "0" ]; then
    su gel -s /bin/bash -c "setsid nohup $CMD >>'$LOG_FILE' 2>&1 </dev/null &"
else
    setsid nohup $CMD >>"$LOG_FILE" 2>&1 </dev/null &
fi

for _ in $(seq 1 240); do
    if ready; then
        echo "gel server is ready on port 5656"
        exit 0
    fi
    sleep 1
done

echo "the local gel server did not become ready in time" >&2
tail -n 40 "$LOG_FILE" >&2
exit 1
START_EOF

RUN chmod 0755 /usr/local/bin/start-gel.sh

RUN mkdir -p /home/user && chmod 0755 /home/user
RUN mkdir -p /home/user/kbsearch/dbschema

# Gel project manifest, pinned to the installed server series.
RUN GEL_VERSION="$(/usr/bin/gel-server-6 --version | sed -E 's/^gel-server, version ([0-9]+\.[0-9]+).*/\1/')" && \
    printf '[instance]\nserver-version = "%s"\n' "$GEL_VERSION" > /home/user/kbsearch/gel.toml

# Empty starting schema - the model itself is part of the task.
RUN cat > /home/user/kbsearch/dbschema/default.gel <<'SCHEMA_EOF'
module default {
}
SCHEMA_EOF

# The handbook corpus that has to be loaded into the database (30 records).
RUN cat > /home/user/kbsearch/seed_data.json <<'SEED_EOF'
[
  {
    "slug": "quokka-cluster-provisioning",
    "title": "Quokka cluster provisioning checklist",
    "summary": "Steps for standing up a new cluster in the staging region.",
    "body": "Provision the nodes, verify the private network, then hand the cluster over to the platform team.",
    "status": "published",
    "tags": ["ops", "database"]
  },
  {
    "slug": "fleet-preparation-window",
    "title": "Fleet preparation window",
    "summary": "How the quokka fleet is prepared before a release window.",
    "body": "Confirm spare capacity, drain traffic slowly, then hand the fleet over to the platform team.",
    "status": "published",
    "tags": ["ops"]
  },
  {
    "slug": "spare-hardware-notes",
    "title": "Spare hardware notes",
    "summary": "General notes about preparing physical hardware.",
    "body": "The quokka rack sits next to the spare switches in the staging region and is powered from the second feed.",
    "status": "published",
    "tags": ["ops"]
  },
  {
    "slug": "tie-breaker-alpha",
    "title": "Wombat telemetry pipeline",
    "summary": "Reference for the wombat telemetry pipeline.",
    "body": "The wombat pipeline batches events in memory and writes them to the warehouse once every minute.",
    "status": "published",
    "tags": ["database", "ops"]
  },
  {
    "slug": "tie-breaker-beta",
    "title": "Wombat telemetry pipeline",
    "summary": "Reference for the wombat telemetry pipeline.",
    "body": "The wombat pipeline batches events in memory and writes them to the warehouse once every minute.",
    "status": "published",
    "tags": ["database", "ops"]
  },
  {
    "slug": "canary-rollout-guide",
    "title": "Canary rollout guide for the payments API",
    "summary": "The staged release procedure used by the payments team.",
    "body": "Shift one percent of traffic first, watch the error budget, then widen the exposure in three steps.",
    "status": "published",
    "tags": ["ops", "testing"]
  },
  {
    "slug": "canary-alerting-thresholds",
    "title": "Canary alerting thresholds",
    "summary": "Which metrics page the on-call engineer.",
    "body": "Alerts fire when the error budget burns faster than the agreed rate for two consecutive windows.",
    "status": "published",
    "tags": ["ops"]
  },
  {
    "slug": "release-freeze-policy",
    "title": "Release freeze policy",
    "summary": "When a rollout request is declined.",
    "body": "During a freeze window only security patches ship, and every exception needs a written approval.",
    "status": "published",
    "tags": ["ops", "security"]
  },
  {
    "slug": "kangaroos-load-harness",
    "title": "Kangaroos load testing harness",
    "summary": "A small harness for synthetic write bursts.",
    "body": "The harness replays a recorded traffic profile and reports the slowest percentile per endpoint.",
    "status": "published",
    "tags": ["testing"]
  },
  {
    "slug": "ledger-schema-overview",
    "title": "Ledger schema overview",
    "summary": "The object types that make up the ledger core.",
    "body": "Every entry links to an account and to a posting batch, and the balance is a computed value.",
    "status": "published",
    "tags": ["database", "onboarding"]
  },
  {
    "slug": "ledger-partition-strategy",
    "title": "Partition strategy for the ledger",
    "summary": "How entries are split across storage ranges.",
    "body": "Ranges are cut by posting month so that the hot range stays small enough for the cache.",
    "status": "published",
    "tags": ["database"]
  },
  {
    "slug": "ledger-backfill-runbook",
    "title": "Backfill runbook",
    "summary": "Recovering a ledger range after a failed import.",
    "body": "Pause the importer, replay the source file in batches of one thousand, then reconcile the totals.",
    "status": "published",
    "tags": ["ops", "database"]
  },
  {
    "slug": "ledger-audit-trail",
    "title": "Audit trail requirements",
    "summary": "What auditors expect from the ledger history.",
    "body": "Each mutation records an actor, a reason code and a timestamp that cannot be edited afterwards.",
    "status": "published",
    "tags": ["security", "database"]
  },
  {
    "slug": "ledger-api-pagination",
    "title": "Ledger API pagination rules",
    "summary": "Stable ordering for paged responses.",
    "body": "Clients pass a limit and an offset, and the service returns a stable order so pages never overlap.",
    "status": "published",
    "tags": ["frontend", "onboarding"]
  },
  {
    "slug": "ledger-retry-semantics",
    "title": "Retry semantics",
    "summary": "How the ledger writer handles a conflict.",
    "body": "A conflicting transaction is retried with a short backoff, and the request key keeps the write unique.",
    "status": "published",
    "tags": ["database"]
  },
  {
    "slug": "ledger-cost-report",
    "title": "Monthly cost report",
    "summary": "Where the ledger spend goes each month.",
    "body": "Storage dominates the bill, followed by the read replicas kept warm for the reporting workload.",
    "status": "draft",
    "tags": ["ops"]
  },
  {
    "slug": "ledger-onboarding-guide",
    "title": "Onboarding guide",
    "summary": "First week reading list for a new ledger engineer.",
    "body": "Read the schema overview, run the local stack, then ship a tiny change behind a feature flag.",
    "status": "draft",
    "tags": ["onboarding"]
  },
  {
    "slug": "ledger-incident-postmortem",
    "title": "Postmortem template",
    "summary": "The template used after a ledger incident.",
    "body": "Describe the impact, the detection gap and the follow up items with an owner for each of them.",
    "status": "draft",
    "tags": ["ops", "onboarding"]
  },
  {
    "slug": "ledger-secrets-rotation",
    "title": "Secrets rotation procedure",
    "summary": "Rotating the ledger service credentials.",
    "body": "Issue the new credential, deploy it beside the old one, then revoke the old credential after a day.",
    "status": "draft",
    "tags": ["security"]
  },
  {
    "slug": "ledger-query-tuning",
    "title": "Query tuning for the ledger",
    "summary": "Reading a slow query report.",
    "body": "Start from the widest scan, add the missing index, then confirm the plan changed before shipping.",
    "status": "published",
    "tags": ["database", "testing"]
  },
  {
    "slug": "ledger-archive-policy",
    "title": "Archive policy",
    "summary": "When a ledger range leaves hot storage.",
    "body": "Ranges older than seven years move to cold storage and stay readable through the export endpoint.",
    "status": "archived",
    "tags": ["database", "security"]
  },
  {
    "slug": "ledger-migration-checklist",
    "title": "Migration checklist",
    "summary": "Shipping a ledger schema change safely.",
    "body": "Write the migration, review the generated plan, apply it on staging and only then promote it.",
    "status": "archived",
    "tags": ["database", "onboarding"]
  },
  {
    "slug": "ledger-mobile-client",
    "title": "Mobile client notes",
    "summary": "How the mobile app reads the ledger.",
    "body": "The app keeps a small local cache and refreshes it whenever the session token is renewed.",
    "status": "archived",
    "tags": ["frontend"]
  },
  {
    "slug": "ledger-webhook-contract",
    "title": "Webhook contract",
    "summary": "Events emitted by the ledger service.",
    "body": "Every event carries a version field, and consumers must ignore any field they do not recognise.",
    "status": "published",
    "tags": ["frontend", "security"]
  },
  {
    "slug": "ledger-rate-limits",
    "title": "Rate limits",
    "summary": "Per client limits on the ledger API.",
    "body": "Reads are limited by a token bucket, and writes fall back to a queue once the bucket is empty.",
    "status": "published",
    "tags": ["frontend"]
  },
  {
    "slug": "ledger-data-retention",
    "title": "Data retention rules",
    "summary": "How long ledger attachments are kept.",
    "body": "Attachments live for two years unless a legal hold pins them for longer than the default window.",
    "status": "draft",
    "tags": ["security", "database"]
  },
  {
    "slug": "ledger-glossary",
    "title": "Glossary",
    "summary": "Words that show up in every ledger review.",
    "body": "A posting is one side of a transfer, and a batch is the smallest unit that can be reversed.",
    "status": "published",
    "tags": ["onboarding"]
  },
  {
    "slug": "graph-traversal-notes",
    "title": "Graph traversal notes",
    "summary": "Walking a link in reverse.",
    "body": "A reverse walk returns a set by default, so the shape has to say when a single value is expected.",
    "status": "published",
    "tags": ["database"]
  },
  {
    "slug": "index-tuning-primer",
    "title": "Index tuning primer",
    "summary": "Choosing between a plain index and a composite one.",
    "body": "A composite index helps when the filter always uses both fields, and hurts when it uses only one.",
    "status": "published",
    "tags": ["database", "testing"]
  },
  {
    "slug": "dashboard-design-notes",
    "title": "Dashboard design notes",
    "summary": "Layout rules for the operations dashboard.",
    "body": "Keep the busiest chart above the fold and put the slow panels behind an explicit refresh button.",
    "status": "draft",
    "tags": ["frontend"]
  }
]
SEED_EOF

LABEL Author="generate-harbor-tasks"
LABEL Description="Local Gel 6 server plus an empty Gel project at /home/user/kbsearch for building a weighted full-text search service in Python."

CMD ["/bin/bash", "-c", "trap : TERM INT; sleep infinity & wait"]

# Container contents:
#   - always required apt packages: curl wget ca-certificates git python3 python3-pip python3-pytest bash ripgrep
#   - always required pip3 packages: pytest-json-ctrf
#   - gel-6 apt package: local Gel 6 server (gel-server-6) and the bundled `gel` CLI
#   - gel==3.1.0 Python client for the async search service and the verification tests
#   - /var/lib/gel/6/data: pre-bootstrapped Gel data directory owned by the `gel` system user
#   - /usr/local/bin/start-gel.sh: idempotent helper that starts the local server and waits for readiness
#   - GEL_DSN / GEL_CLIENT_TLS_SECURITY: connection settings for the CLI and the Python client
#   - /home/user/kbsearch: Gel project skeleton (gel.toml, empty dbschema/default.gel, seed_data.json)

COPY test_initial_state.py /bootstrap/test_initial_state.py

# Setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
