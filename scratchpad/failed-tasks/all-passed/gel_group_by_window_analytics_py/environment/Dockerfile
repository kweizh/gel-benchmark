FROM ubuntu:24.04

ARG POCHI_VERSION

# ---------------------------------------------------------------------------
# Base tooling. NOTE: python3-pytest is deliberately NOT installed from apt --
# the distro pytest is too old for pytest-json-ctrf and makes the whole suite
# abort with an INTERNALERROR. pytest comes from pip instead.
# ---------------------------------------------------------------------------
RUN apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl wget ca-certificates gnupg git python3 python3-pip bash ripgrep \
        procps psmisc tzdata locales && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    curl -fsSL https://getpochi.com/install.sh | bash -s ${POCHI_VERSION} && \
    ln -s /root/.pochi/bin/pochi /usr/local/bin/pochi && \
    pip3 install --break-system-packages "pytest>=8" pytest-json-ctrf gel && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Gel 7 server + CLI from the official apt repository.
# ---------------------------------------------------------------------------
RUN mkdir -p /usr/local/share/keyrings && \
    curl --proto '=https' --tlsv1.2 -sSf \
        -o /usr/local/share/keyrings/gel-keyring.gpg \
        https://packages.geldata.com/keys/gel-keyring.gpg && \
    echo "deb [signed-by=/usr/local/share/keyrings/gel-keyring.gpg] https://packages.geldata.com/apt noble main" \
        > /etc/apt/sources.list.d/gel.list && \
    apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends gel-7 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Resolve the server binary defensively: its name differs across images and
# major versions (gel-server, gel-server-7, or nested under /usr/lib).
# ---------------------------------------------------------------------------
RUN mkdir -p /usr/local/etc && \
    GEL_BIN="" && \
    for cand in gel-server-7 gel-server gel-server-6; do \
        if command -v "$cand" >/dev/null 2>&1; then GEL_BIN="$(command -v $cand)"; break; fi; \
    done && \
    if [ -z "$GEL_BIN" ]; then GEL_BIN="$(ls /usr/bin/gel-server* 2>/dev/null | head -n 1)"; fi && \
    if [ -z "$GEL_BIN" ]; then GEL_BIN="$(ls /usr/lib/*/gel-server-*/bin/gel-server 2>/dev/null | head -n 1)"; fi && \
    if [ -z "$GEL_BIN" ]; then echo "FATAL: no gel-server binary found in this image" >&2; exit 1; fi && \
    echo "$GEL_BIN" > /usr/local/etc/gel-server-bin && \
    echo "Resolved gel server binary: $GEL_BIN" && \
    "$GEL_BIN" --version

# ---------------------------------------------------------------------------
# Dedicated unprivileged account + explicit data/runstate dirs. gel-server
# refuses to run as root, and without an explicit --runstate-dir it falls back
# to the root-owned /run/gel and dies with EACCES.
# ---------------------------------------------------------------------------
RUN useradd --system --home-dir /var/lib/gel --create-home --shell /bin/bash gelsrv || true && \
    mkdir -p /var/lib/gel/run /var/lib/gel/taskdata /var/log && \
    touch /var/log/gel-server.log && \
    chown -R gelsrv:gelsrv /var/lib/gel /var/log/gel-server.log && \
    chmod 0700 /var/lib/gel/run /var/lib/gel/taskdata

ENV GEL_DSN="gel://admin@127.0.0.1:5656/main"
ENV GEL_CLIENT_SECURITY="insecure_dev_mode"

RUN cat >/usr/local/bin/gel-start <<'GELSTART'
#!/bin/bash
# Idempotently start the local Gel server and block until it answers queries.
DATADIR=/var/lib/gel/taskdata
RUNSTATE=/var/lib/gel/run
LOGFILE=/var/log/gel-server.log
BIN="$(cat /usr/local/etc/gel-server-bin)"

if [ -z "$BIN" ] || [ ! -x "$BIN" ]; then
    echo "FATAL: gel-server binary '$BIN' is not executable" >&2
    exit 1
fi

if gel query 'select 1' >/dev/null 2>&1; then
    exit 0
fi

mkdir -p "$DATADIR" "$RUNSTATE"
touch "$LOGFILE"
chown -R gelsrv:gelsrv "$DATADIR" "$RUNSTATE" "$LOGFILE" 2>/dev/null

su gelsrv -s /bin/bash -c "env HOME=/var/lib/gel \
    GEL_SERVER_COMPILER_POOL_MODE=on_demand \
    GEL_SERVER_COMPILER_POOL_SIZE=2 \
    setsid nohup '$BIN' \
        --data-dir='$DATADIR' \
        --runstate-dir='$RUNSTATE' \
        --bind-address=127.0.0.1 \
        --port=5656 \
        --security=insecure_dev_mode \
        --tls-cert-mode=generate_self_signed \
        --log-level=warn \
        >>'$LOGFILE' 2>&1 </dev/null &"

for _ in $(seq 1 300); do
    if gel query 'select 1' >/dev/null 2>&1; then
        exit 0
    fi
    sleep 1
done

echo "FATAL: the local Gel server did not become ready in time" >&2
tail -n 60 "$LOGFILE" >&2
exit 1
GELSTART

RUN cat >/usr/local/bin/gel-stop <<'GELSTOP'
#!/bin/bash
# Gracefully stop the local Gel server (used at image build time).
pkill -TERM -f 'gel-server' >/dev/null 2>&1
for _ in $(seq 1 60); do
    if ! pgrep -f 'gel-server' >/dev/null 2>&1; then
        exit 0
    fi
    sleep 1
done
pkill -KILL -f 'gel-server' >/dev/null 2>&1
exit 0
GELSTOP

RUN chmod 0755 /usr/local/bin/gel-start /usr/local/bin/gel-stop

# ---------------------------------------------------------------------------
# The pre-existing project skeleton.
# ---------------------------------------------------------------------------
RUN mkdir -p /home/user && chmod 0755 /home/user
RUN mkdir -p /home/user/analytics/dbschema /home/user/analytics/data /opt/seed

RUN cat >/home/user/analytics/gel.toml <<'GELTOML'
[instance]
server-version = "7.1"

[project]
schema-dir = "dbschema"
GELTOML

RUN cat >/home/user/analytics/dbschema/default.gel <<'SDL'
module default {
  type Category {
    required name: str {
      constraint exclusive;
    }
    required region: str;
  }

  type Sale {
    required order_ref: str {
      constraint exclusive;
    }
    required occurred_at: datetime;
    required amount_cents: int64;
    required units: int64;
    required channel: str;
    required category: Category;
  }
}
SDL

RUN cat >/opt/seed/seed.py <<'SEED'
"""Deterministic build-time seeding of Category / Sale plus data/refunds.json."""

import calendar
import json

import gel

PROJECT_DIR = "/home/user/analytics"

CATEGORIES = [
    ("Espresso", "EU"),
    ("Filter", "EU"),
    ("Grinder", "NA"),
    ("Kettle", "NA"),
    ("Mug", "APAC"),
    ("Scale", "APAC"),
    ("Tamper", "EU"),
    ("Storage", "NA"),
    ("Decanter", "APAC"),
    ("Sampler", "EU"),
]
ACTIVE = [name for name, _ in CATEGORIES[:8]]

# (month, channel, number of sales). 2024-01/partner is intentionally empty and
# 2024-03/partner intentionally holds a single sale.
COUNTS = [
    ("2024-01", "web", 22),
    ("2024-01", "retail", 18),
    ("2024-01", "partner", 0),
    ("2024-02", "web", 25),
    ("2024-02", "retail", 20),
    ("2024-02", "partner", 12),
    ("2024-03", "web", 30),
    ("2024-03", "retail", 21),
    ("2024-03", "partner", 1),
    ("2024-04", "web", 24),
    ("2024-04", "retail", 16),
    ("2024-04", "partner", 11),
]

REFUNDABLE_MONTHS = ("2024-01", "2024-02", "2024-04")


def build_sales():
    sales = []
    counter = 0
    for month, channel, count in COUNTS:
        year = int(month[:4])
        mon = int(month[5:7])
        last_day = calendar.monthrange(year, mon)[1]
        for i in range(count):
            counter += 1
            if i == 0:
                occurred = "%s-01T00:00:00Z" % month
            elif i == 1:
                occurred = "%s-%02dT23:59:59Z" % (month, last_day)
            else:
                day = 2 + ((i * 7) % 26)
                occurred = "%s-%02dT%02d:%02d:00Z" % (
                    month,
                    day,
                    (i * 3) % 24,
                    (i * 11) % 60,
                )
            sales.append(
                {
                    "order_ref": "ORD-%04d" % counter,
                    "occurred_at": occurred,
                    "amount_cents": 1500 + ((i * 17) % 20) * 25,
                    "units": 1 + (i % 5),
                    "channel": channel,
                    "category": ACTIVE[counter % 8],
                    "month": month,
                }
            )
    return sales


def build_refunds(sales):
    pool = [s for s in sales if s["month"] in REFUNDABLE_MONTHS]
    targets = [s for j, s in enumerate(pool) if j % 4 == 0]
    drafts = []
    for idx, sale in enumerate(targets):
        if idx == 2:
            amount = sale["amount_cents"]  # full refund -> net_cents == 0
        else:
            amount = 25 * (1 + (idx % 8))
        drafts.append((sale["order_ref"], amount, "%s-15T12:00:00Z" % sale["month"]))
        if idx == 0:
            # a second refund on the same sale -> refund_count == 2
            drafts.append(
                (sale["order_ref"], 150, "%s-18T08:30:00Z" % sale["month"])
            )
    records = [
        {
            "external_id": "RF-%04d" % (n + 1),
            "order_ref": order_ref,
            "amount_cents": amount,
            "refunded_at": refunded_at,
        }
        for n, (order_ref, amount, refunded_at) in enumerate(drafts)
    ]
    # Records pointing at orders that do not exist -> must be skipped, not fatal.
    for u in (1, 2, 3):
        records.append(
            {
                "external_id": "RF-90%02d" % u,
                "order_ref": "ORD-9%03d" % u,
                "amount_cents": 100 * u,
                "refunded_at": "2024-05-0%dT12:00:00Z" % u,
            }
        )
    return records


def main():
    sales = build_sales()
    refunds = build_refunds(sales)

    client = gel.create_client()
    try:
        client.ensure_connected()
        client.execute("delete Sale; delete Category;")
        client.query(
            """
            for item in json_array_unpack(<json>$data) union (
              insert Category {
                name := <str>item['name'],
                region := <str>item['region']
              }
            )
            """,
            data=json.dumps([{"name": n, "region": r} for n, r in CATEGORIES]),
        )
        client.query(
            """
            for item in json_array_unpack(<json>$data) union (
              insert Sale {
                order_ref := <str>item['order_ref'],
                occurred_at := <datetime><str>item['occurred_at'],
                amount_cents := <int64>item['amount_cents'],
                units := <int64>item['units'],
                channel := <str>item['channel'],
                category := assert_single((
                  select Category filter .name = <str>item['category']
                ))
              }
            )
            """,
            data=json.dumps(
                [
                    {k: v for k, v in s.items() if k != "month"}
                    for s in sales
                ]
            ),
        )

        seeded_categories = client.query_single("select count(Category)")
        seeded_sales = client.query_single("select count(Sale)")
        assert seeded_categories == len(CATEGORIES), seeded_categories
        assert seeded_sales == len(sales), seeded_sales
    finally:
        client.close()

    with open("%s/data/refunds.json" % PROJECT_DIR, "w", encoding="utf-8") as handle:
        json.dump(refunds, handle, indent=2)
        handle.write("\n")

    # Build-time invariants the verifier relies on. The data is fully
    # deterministic, so these can never regress at run time.
    by_order = {s["order_ref"]: s for s in sales}
    totals = {}
    counts = {}
    for record in refunds:
        if record["order_ref"] in by_order:
            totals[record["order_ref"]] = (
                totals.get(record["order_ref"], 0) + record["amount_cents"]
            )
            counts[record["order_ref"]] = counts.get(record["order_ref"], 0) + 1
    nets = {
        ref: sale["amount_cents"] - totals.get(ref, 0) for ref, sale in by_order.items()
    }
    assert any(v == 0 for v in nets.values()), "expected a fully refunded sale"
    assert any(v >= 2 for v in counts.values()), "expected a sale with 2 refunds"
    assert any(ref not in totals for ref in by_order), "expected an unrefunded sale"
    assert any(
        r["order_ref"] not in by_order for r in refunds
    ), "expected refunds referencing unknown orders"
    assert all(r["amount_cents"] >= 1 for r in refunds), "refund amounts must be >= 1"
    assert len({r["external_id"] for r in refunds}) == len(refunds), "duplicate ids"

    groups = {}
    for sale in sales:
        groups.setdefault((sale["month"], sale["channel"]), []).append(sale)
    assert any(len(g) == 1 for g in groups.values()), "expected a single-sale group"
    months = {m for m, _ in groups}
    channels = {c for _, c in groups}
    assert len(groups) < len(months) * len(channels), "expected an empty month/channel pair"
    used = {s["category"] for s in sales}
    assert len(used) == 8 and len(CATEGORIES) - len(used) == 2, "expected 2 empty categories"

    tie_found = False
    for group in groups.values():
        ranked = sorted(group, key=lambda s: (-nets[s["order_ref"]], s["order_ref"]))[:3]
        values = [nets[s["order_ref"]] for s in ranked]
        if len(values) >= 2 and len(set(values)) < len(values):
            tie_found = True
    assert tie_found, "expected a net_cents tie inside some group's top 3"

    print("seeded %d categories, %d sales, %d refund records"
          % (len(CATEGORIES), len(sales), len(refunds)))


main()
SEED

# ---------------------------------------------------------------------------
# Bootstrap the instance, apply the initial migration and seed the data.
# ---------------------------------------------------------------------------
RUN /usr/local/bin/gel-start && \
    cd /home/user/analytics && \
    gel migration create --non-interactive --allow-unsafe && \
    gel migrate && \
    gel migration status && \
    ls -1 /home/user/analytics/dbschema/migrations/*.edgeql && \
    python3 /opt/seed/seed.py && \
    gel query --output-format json 'select count(Sale)' && \
    /usr/local/bin/gel-stop && \
    rm -rf /opt/seed && \
    chmod -R 0777 /home/user/analytics

LABEL Author="generate-harbor-tasks"
LABEL Description="Gel 7 server/CLI with a pre-migrated, pre-seeded coffee-retail analytics project at /home/user/analytics for building group-by rollups behind an async Python client."

CMD ["/bin/bash", "-c", "trap : TERM INT; sleep infinity & wait"]

# Container contents:
#   - apt packages: curl wget ca-certificates gnupg git python3 python3-pip bash ripgrep procps psmisc tzdata locales
#     (python3-pytest is intentionally omitted; pytest comes from pip)
#   - pip3 packages: pytest>=8, pytest-json-ctrf, gel (async/sync Python client)
#   - Gel 7 server + CLI (apt package gel-7) from packages.geldata.com
#   - /usr/local/bin/gel-start : idempotent local server starter (explicit --runstate-dir,
#     runs as the unprivileged gelsrv user, waits until queries succeed)
#   - /usr/local/bin/gel-stop  : graceful shutdown helper used during the image build
#   - /var/lib/gel/taskdata    : bootstrapped Gel data directory (owned by gelsrv)
#   - /var/lib/gel/run         : explicit runstate directory (owned by gelsrv)
#   - /home/user/analytics     : project root (gel.toml, dbschema/default.gel,
#     dbschema/migrations/<applied initial migration>, data/refunds.json)
#   - Seeded data: 10 Category objects (2 without sales) and 200 Sale objects across
#     4 UTC months x 3 channels, with one empty month/channel pair and one single-sale group
#   - Connection configured through GEL_DSN / GEL_CLIENT_SECURITY environment variables

COPY test_initial_state.py /bootstrap/test_initial_state.py

# Setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
