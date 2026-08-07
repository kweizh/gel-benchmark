"""Final-state verification for gel_group_by_window_analytics_py.

Everything is checked against the real, running local Gel 7 server and the real
CLI / Python module the executor authored.  The expected analytics report is
recomputed independently inside this test from raw ``select`` queries (no
``group``), so the suite acts as an oracle rather than encoding magic numbers.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from statistics import fmean, stdev

import pytest

try:  # pragma: no cover - import guard only
    import asyncio
    import gel
except Exception as exc:  # pragma: no cover
    raise RuntimeError(f"The `gel` Python client is required by the verifier: {exc}")

PROJECT_DIR = "/home/user/analytics"
ROLLUPS_PATH = os.path.join(PROJECT_DIR, "analytics", "rollups.py")
CLI_PATH = os.path.join(PROJECT_DIR, "analytics", "cli.py")
INIT_PATH = os.path.join(PROJECT_DIR, "analytics", "__init__.py")
REFUNDS_FILE = os.path.join(PROJECT_DIR, "data", "refunds.json")
MIGRATIONS_DIR = os.path.join(PROJECT_DIR, "dbschema", "migrations")
GEL_START = "/usr/local/bin/gel-start"

VARIANT_FILE = "/tmp/refunds_variant.json"
BAD_AMOUNT_FILE = "/tmp/refunds_bad_amount.json"
DUP_FILE = "/tmp/refunds_dup.json"
MISSING_FILE = "/tmp/does_not_exist.json"

FLOAT_TOL = 0.006

TOP_LEVEL_KEYS = {
    "window",
    "grand_total",
    "monthly_by_channel",
    "channel_totals",
    "category_rank",
    "empty_categories",
}
GRAND_TOTAL_KEYS = {
    "sale_count",
    "unit_count",
    "gross_cents",
    "refund_cents",
    "net_cents",
    "month_count",
    "channel_count",
    "category_count",
}
MONTHLY_KEYS = {
    "month",
    "channel",
    "sale_count",
    "unit_count",
    "gross_cents",
    "refund_cents",
    "net_cents",
    "mean_net_cents",
    "min_net_cents",
    "max_net_cents",
    "stddev_net_cents",
    "top_orders",
}
CHANNEL_KEYS = {"channel", "sale_count", "net_cents", "share_pct"}
CATEGORY_KEYS = {"category", "region", "sale_count", "net_cents", "rank", "percentile"}
INGEST_KEYS = {
    "inserted",
    "updated",
    "unchanged",
    "skipped",
    "refund_total_count",
    "refund_total_cents",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def gel_server():
    """Start (or reuse) the local Gel server.

    Any test that touches the database - directly or by shelling out to the
    `gel` CLI or the executor's CLI - must depend on this fixture, otherwise it
    can race the server startup and fail with `Connection refused`.
    """
    assert os.path.isfile(GEL_START), f"{GEL_START} is missing from the image."
    proc = subprocess.run([GEL_START], capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, (
        f"Could not start the local Gel server.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    return True


@pytest.fixture(scope="session")
def client(gel_server):
    cl = gel.create_client()
    try:
        cl.ensure_connected()
        yield cl
    finally:
        cl.close()


@pytest.fixture(scope="session")
def canonical_state(client):
    """Bring the database into the canonical post-ingest state.

    Scratch files created by the verification itself are removed first, then the
    shipped refunds file is ingested once through the executor's CLI.
    """
    for path in (VARIANT_FILE, BAD_AMOUNT_FILE, DUP_FILE, MISSING_FILE):
        if os.path.exists(path):
            os.remove(path)
    run_cli(["ingest-refunds", "--file", REFUNDS_FILE])
    return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run_cli(args, timeout=300):
    return subprocess.run(
        [sys.executable, "-m", "analytics.cli", *args],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run_gel_cli(args, timeout=300):
    return subprocess.run(
        ["gel", *args],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def q(client, query, **kwargs):
    return json.loads(client.query_json(query, **kwargs))


def q_single(client, query, **kwargs):
    return json.loads(client.query_single_json(query, **kwargs))


def parse_dt(value):
    text = value.replace("Z", "+00:00") if value.endswith("Z") else value
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_refund_file():
    with open(REFUNDS_FILE, encoding="utf-8") as handle:
        return json.load(handle)


def load_rollups():
    if PROJECT_DIR not in sys.path:
        sys.path.insert(0, PROJECT_DIR)
    try:
        import analytics.rollups as module
    except Exception as exc:  # pragma: no cover
        pytest.fail(f"Could not import analytics.rollups from {PROJECT_DIR}: {exc!r}")
    return module


def call_async(coro_factory):
    async def runner():
        async_client = gel.create_async_client()
        try:
            return await coro_factory(async_client)
        finally:
            await async_client.aclose()

    return asyncio.run(runner())


def is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def is_num(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def check_int(actual, expected, ctx):
    assert is_int(actual), f"{ctx}: expected a JSON integer, got {actual!r} ({type(actual).__name__})."
    assert actual == expected, f"{ctx}: expected {expected}, got {actual}."


def check_float(actual, expected, ctx):
    assert is_num(actual), f"{ctx}: expected a JSON number, got {actual!r} ({type(actual).__name__})."
    assert abs(float(actual) - float(expected)) <= FLOAT_TOL, (
        f"{ctx}: expected approximately {expected}, got {actual}."
    )
    assert abs(float(actual) - round(float(actual), 2)) <= 1e-9, (
        f"{ctx}: value {actual} is not rounded to at most 2 decimal places."
    )


# ---------------------------------------------------------------------------
# Independent oracle
# ---------------------------------------------------------------------------


def fetch_raw(client):
    sales = q(
        client,
        "select Sale { order_ref, occurred_at, amount_cents, units, channel, "
        "category: { name, region } }",
    )
    refunds = q(
        client,
        "select Refund { external_id, amount_cents, refunded_at, sale: { order_ref } }",
    )
    categories = q(client, "select Category { name, region }")
    return sales, refunds, categories


def build_expected(sales, refunds, categories, month=None):
    refund_sum = defaultdict(int)
    for refund in refunds:
        refund_sum[refund["sale"]["order_ref"]] += refund["amount_cents"]

    rows = []
    for sale in sales:
        occurred = parse_dt(sale["occurred_at"])
        rows.append(
            {
                "order_ref": sale["order_ref"],
                "month": occurred.strftime("%Y-%m"),
                "channel": sale["channel"],
                "category": sale["category"]["name"],
                "region": sale["category"]["region"],
                "units": sale["units"],
                "gross": sale["amount_cents"],
                "net": sale["amount_cents"] - refund_sum.get(sale["order_ref"], 0),
            }
        )

    scope = [r for r in rows if month is None or r["month"] == month]

    gross = sum(r["gross"] for r in scope)
    net = sum(r["net"] for r in scope)
    grand_total = {
        "sale_count": len(scope),
        "unit_count": sum(r["units"] for r in scope),
        "gross_cents": gross,
        "refund_cents": gross - net,
        "net_cents": net,
        "month_count": len({r["month"] for r in scope}),
        "channel_count": len({r["channel"] for r in scope}),
        "category_count": len({r["category"] for r in scope}),
    }

    month_channel = defaultdict(list)
    for row in scope:
        month_channel[(row["month"], row["channel"])].append(row)
    monthly = []
    for key in sorted(month_channel):
        group = month_channel[key]
        nets = [r["net"] for r in group]
        group_gross = sum(r["gross"] for r in group)
        group_net = sum(nets)
        top = sorted(group, key=lambda r: (-r["net"], r["order_ref"]))[:3]
        monthly.append(
            {
                "month": key[0],
                "channel": key[1],
                "sale_count": len(group),
                "unit_count": sum(r["units"] for r in group),
                "gross_cents": group_gross,
                "refund_cents": group_gross - group_net,
                "net_cents": group_net,
                "mean_net_cents": fmean(nets),
                "min_net_cents": min(nets),
                "max_net_cents": max(nets),
                "stddev_net_cents": stdev(nets) if len(nets) >= 2 else None,
                "top_orders": [
                    {"order_ref": r["order_ref"], "net_cents": r["net"]} for r in top
                ],
            }
        )

    by_channel = defaultdict(list)
    for row in scope:
        by_channel[row["channel"]].append(row)
    channel_totals = []
    for channel, group in by_channel.items():
        group_net = sum(r["net"] for r in group)
        channel_totals.append(
            {
                "channel": channel,
                "sale_count": len(group),
                "net_cents": group_net,
                "share_pct": (group_net / net * 100.0) if net else 0.0,
            }
        )
    channel_totals.sort(key=lambda r: (-r["net_cents"], r["channel"]))

    by_category = defaultdict(list)
    for row in scope:
        by_category[row["category"]].append(row)
    category_nets = {name: sum(r["net"] for r in g) for name, g in by_category.items()}
    population = len(category_nets)
    category_rank = []
    for name, group in by_category.items():
        value = category_nets[name]
        category_rank.append(
            {
                "category": name,
                "region": group[0]["region"],
                "sale_count": len(group),
                "net_cents": value,
                "rank": 1 + sum(1 for other in category_nets.values() if other > value),
                "percentile": 100.0
                * sum(1 for other in category_nets.values() if other <= value)
                / population,
            }
        )
    category_rank.sort(key=lambda r: (r["rank"], r["category"]))

    empty_categories = sorted(
        c["name"] for c in categories if c["name"] not in by_category
    )

    return {
        "window": {"month": month},
        "grand_total": grand_total,
        "monthly_by_channel": monthly,
        "channel_totals": channel_totals,
        "category_rank": category_rank,
        "empty_categories": empty_categories,
    }


def compare_report(actual, expected, label):
    assert isinstance(actual, dict), f"{label}: report must be a JSON object, got {type(actual).__name__}."
    assert set(actual.keys()) == TOP_LEVEL_KEYS, (
        f"{label}: top-level keys must be exactly {sorted(TOP_LEVEL_KEYS)}, got {sorted(actual.keys())}."
    )

    assert isinstance(actual["window"], dict) and set(actual["window"].keys()) == {"month"}, (
        f"{label}: `window` must be an object with exactly the key `month`, got {actual['window']!r}."
    )
    assert actual["window"]["month"] == expected["window"]["month"], (
        f"{label}: window.month expected {expected['window']['month']!r}, got {actual['window']['month']!r}."
    )

    grand = actual["grand_total"]
    assert isinstance(grand, dict) and set(grand.keys()) == GRAND_TOTAL_KEYS, (
        f"{label}: grand_total keys must be exactly {sorted(GRAND_TOTAL_KEYS)}, got {sorted(grand.keys()) if isinstance(grand, dict) else grand!r}."
    )
    for key in sorted(GRAND_TOTAL_KEYS):
        check_int(grand[key], expected["grand_total"][key], f"{label}: grand_total.{key}")

    actual_monthly = actual["monthly_by_channel"]
    expected_monthly = expected["monthly_by_channel"]
    assert isinstance(actual_monthly, list), f"{label}: monthly_by_channel must be an array."
    assert len(actual_monthly) == len(expected_monthly), (
        f"{label}: monthly_by_channel must contain {len(expected_monthly)} entries "
        f"(one per (month, channel) pair that has sales), got {len(actual_monthly)}: "
        f"{[(e.get('month'), e.get('channel')) for e in actual_monthly if isinstance(e, dict)]}"
    )
    for index, (got, want) in enumerate(zip(actual_monthly, expected_monthly)):
        ctx = f"{label}: monthly_by_channel[{index}]"
        assert isinstance(got, dict) and set(got.keys()) == MONTHLY_KEYS, (
            f"{ctx}: keys must be exactly {sorted(MONTHLY_KEYS)}, got "
            f"{sorted(got.keys()) if isinstance(got, dict) else got!r}."
        )
        assert got["month"] == want["month"], (
            f"{ctx}.month expected {want['month']!r}, got {got['month']!r} "
            "(entries must be ordered by month then channel)."
        )
        assert got["channel"] == want["channel"], (
            f"{ctx}.channel expected {want['channel']!r}, got {got['channel']!r}."
        )
        for key in (
            "sale_count",
            "unit_count",
            "gross_cents",
            "refund_cents",
            "net_cents",
            "min_net_cents",
            "max_net_cents",
        ):
            check_int(got[key], want[key], f"{ctx}.{key}")
        check_float(got["mean_net_cents"], want["mean_net_cents"], f"{ctx}.mean_net_cents")
        if want["stddev_net_cents"] is None:
            assert got["stddev_net_cents"] is None, (
                f"{ctx}.stddev_net_cents must be null when sale_count < 2, got {got['stddev_net_cents']!r}."
            )
        else:
            check_float(
                got["stddev_net_cents"], want["stddev_net_cents"], f"{ctx}.stddev_net_cents"
            )
        tops = got["top_orders"]
        assert isinstance(tops, list), f"{ctx}.top_orders must be an array."
        assert len(tops) == len(want["top_orders"]), (
            f"{ctx}.top_orders must hold {len(want['top_orders'])} entries, got {len(tops)}."
        )
        assert len(tops) <= 3, f"{ctx}.top_orders must hold at most 3 entries, got {len(tops)}."
        for pos, (got_top, want_top) in enumerate(zip(tops, want["top_orders"])):
            top_ctx = f"{ctx}.top_orders[{pos}]"
            assert isinstance(got_top, dict) and set(got_top.keys()) == {"order_ref", "net_cents"}, (
                f"{top_ctx}: keys must be exactly ['net_cents', 'order_ref'], got "
                f"{sorted(got_top.keys()) if isinstance(got_top, dict) else got_top!r}."
            )
            assert got_top["order_ref"] == want_top["order_ref"], (
                f"{top_ctx}.order_ref expected {want_top['order_ref']!r}, got {got_top['order_ref']!r} "
                "(order by net_cents desc, then order_ref asc)."
            )
            check_int(got_top["net_cents"], want_top["net_cents"], f"{top_ctx}.net_cents")

    actual_channels = actual["channel_totals"]
    expected_channels = expected["channel_totals"]
    assert isinstance(actual_channels, list), f"{label}: channel_totals must be an array."
    assert len(actual_channels) == len(expected_channels), (
        f"{label}: channel_totals must contain {len(expected_channels)} entries, got {len(actual_channels)}."
    )
    for index, (got, want) in enumerate(zip(actual_channels, expected_channels)):
        ctx = f"{label}: channel_totals[{index}]"
        assert isinstance(got, dict) and set(got.keys()) == CHANNEL_KEYS, (
            f"{ctx}: keys must be exactly {sorted(CHANNEL_KEYS)}, got "
            f"{sorted(got.keys()) if isinstance(got, dict) else got!r}."
        )
        assert got["channel"] == want["channel"], (
            f"{ctx}.channel expected {want['channel']!r}, got {got['channel']!r} "
            "(order by net_cents desc, then channel asc)."
        )
        check_int(got["sale_count"], want["sale_count"], f"{ctx}.sale_count")
        check_int(got["net_cents"], want["net_cents"], f"{ctx}.net_cents")
        check_float(got["share_pct"], want["share_pct"], f"{ctx}.share_pct")

    actual_cats = actual["category_rank"]
    expected_cats = expected["category_rank"]
    assert isinstance(actual_cats, list), f"{label}: category_rank must be an array."
    assert len(actual_cats) == len(expected_cats), (
        f"{label}: category_rank must contain {len(expected_cats)} entries, got {len(actual_cats)}."
    )
    for index, (got, want) in enumerate(zip(actual_cats, expected_cats)):
        ctx = f"{label}: category_rank[{index}]"
        assert isinstance(got, dict) and set(got.keys()) == CATEGORY_KEYS, (
            f"{ctx}: keys must be exactly {sorted(CATEGORY_KEYS)}, got "
            f"{sorted(got.keys()) if isinstance(got, dict) else got!r}."
        )
        assert got["category"] == want["category"], (
            f"{ctx}.category expected {want['category']!r}, got {got['category']!r} "
            "(order by rank asc, then category asc)."
        )
        assert got["region"] == want["region"], (
            f"{ctx}.region expected {want['region']!r}, got {got['region']!r}."
        )
        check_int(got["sale_count"], want["sale_count"], f"{ctx}.sale_count")
        check_int(got["net_cents"], want["net_cents"], f"{ctx}.net_cents")
        check_int(got["rank"], want["rank"], f"{ctx}.rank")
        check_float(got["percentile"], want["percentile"], f"{ctx}.percentile")

    assert actual["empty_categories"] == expected["empty_categories"], (
        f"{label}: empty_categories expected {expected['empty_categories']}, got {actual['empty_categories']}."
    )


def cli_report(args=()):
    proc = run_cli(["report", *args])
    assert proc.returncode == 0, (
        f"`python3 -m analytics.cli report {' '.join(args)}` exited {proc.returncode}.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(
            f"stdout of `report {' '.join(args)}` is not a single JSON document ({exc}).\n"
            f"stdout was: {proc.stdout!r}"
        )


def cli_ingest(path):
    proc = run_cli(["ingest-refunds", "--file", path])
    assert proc.returncode == 0, (
        f"`ingest-refunds --file {path}` exited {proc.returncode}.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(
            f"stdout of `ingest-refunds --file {path}` is not JSON ({exc}). stdout: {proc.stdout!r}"
        )
    assert isinstance(payload, dict) and set(payload.keys()) == INGEST_KEYS, (
        f"The ingestion summary must be an object with exactly the keys {sorted(INGEST_KEYS)}, got "
        f"{sorted(payload.keys()) if isinstance(payload, dict) else payload!r}."
    )
    for key in sorted(INGEST_KEYS):
        assert is_int(payload[key]), (
            f"Ingestion summary field {key!r} must be a JSON integer, got {payload[key]!r}."
        )
    return payload


def refund_totals(client):
    return (
        q_single(client, "select count(Refund)"),
        q_single(client, "select sum(Refund.amount_cents)"),
    )


def ingestable_split(client):
    """Split data/refunds.json into records with a known / unknown order_ref."""
    known = set(q(client, "select Sale.order_ref"))
    records = load_refund_file()
    ingestable = [r for r in records if r["order_ref"] in known]
    skipped = [r for r in records if r["order_ref"] not in known]
    return ingestable, skipped


def expect_gel_error(callable_obj, message):
    try:
        callable_obj()
    except Exception as exc:  # noqa: BLE001 - we assert on the class hierarchy below
        names = {cls.__name__ for cls in type(exc).__mro__}
        assert names & {
            "ConstraintViolationError",
            "InvalidValueError",
            "CardinalityViolationError",
            "EdgeDBError",
            "GelError",
        }, f"{message}: raised {type(exc).__name__} ({exc}), which is not a Gel query error."
        return exc
    pytest.fail(message)


# ---------------------------------------------------------------------------
# A. Schema and migration
# ---------------------------------------------------------------------------


def test_analytics_package_files_exist():
    for path in (INIT_PATH, ROLLUPS_PATH, CLI_PATH):
        assert os.path.isfile(path), f"Required Python file {path} does not exist."


def test_refund_type_exists_with_required_pointers(client):
    result = q(
        client,
        "select schema::ObjectType { "
        "  name, "
        "  pointers: { name, required, cardinality, target: { name } } "
        "} filter .name = 'default::Refund'",
    )
    assert result, "Object type `default::Refund` does not exist in the database."
    pointers = {p["name"]: p for p in result[0]["pointers"]}
    expected = {
        "external_id": "std::str",
        "amount_cents": "std::int64",
        "refunded_at": "std::datetime",
        "sale": "default::Sale",
    }
    for name, target in expected.items():
        assert name in pointers, (
            f"default::Refund is missing the pointer {name!r}; found {sorted(pointers)}."
        )
        assert pointers[name]["required"] is True, f"default::Refund.{name} must be required."
        assert pointers[name]["cardinality"] == "One", (
            f"default::Refund.{name} must be single-valued, got cardinality "
            f"{pointers[name]['cardinality']!r}."
        )
        assert pointers[name]["target"]["name"] == target, (
            f"default::Refund.{name} must target {target}, got {pointers[name]['target']['name']}."
        )


def test_refund_external_id_is_exclusive(client, canonical_state):
    existing = q(client, "select Refund { external_id } limit 1")
    assert existing, "No Refund objects were ingested, cannot verify exclusivity."
    external_id = existing[0]["external_id"]
    order_ref = q(client, "select Sale { order_ref } limit 1")[0]["order_ref"]
    expect_gel_error(
        lambda: client.query(
            "insert Refund { external_id := <str>$eid, "
            "sale := (select Sale filter .order_ref = <str>$oref limit 1), "
            "amount_cents := 5, refunded_at := <datetime>'2024-06-01T00:00:00Z' }",
            eid=external_id,
            oref=order_ref,
        ),
        "Inserting a second Refund with an already-used external_id must be rejected by an "
        "exclusivity constraint, but it succeeded.",
    )


@pytest.mark.parametrize("amount", [0, -5])
def test_refund_amount_below_one_is_rejected(client, canonical_state, amount):
    order_ref = q(client, "select Sale { order_ref } limit 1")[0]["order_ref"]
    probe_id = f"ZZ-PROBE-REJECT-{amount}"
    try:
        expect_gel_error(
            lambda: client.query(
                "insert Refund { external_id := <str>$eid, "
                "sale := (select Sale filter .order_ref = <str>$oref limit 1), "
                "amount_cents := <int64>$amt, refunded_at := <datetime>'2024-06-01T00:00:00Z' }",
                eid=probe_id,
                oref=order_ref,
                amt=amount,
            ),
            f"The database must reject Refund.amount_cents = {amount}, but the insert succeeded.",
        )
    finally:
        client.query(
            "delete Refund filter .external_id = <str>$eid", eid=probe_id
        )


def test_refund_amount_of_one_is_accepted(client, canonical_state):
    order_ref = q(client, "select Sale { order_ref } limit 1")[0]["order_ref"]
    probe_id = "ZZ-PROBE-ACCEPT-1"
    try:
        client.query(
            "insert Refund { external_id := <str>$eid, "
            "sale := (select Sale filter .order_ref = <str>$oref limit 1), "
            "amount_cents := 1, refunded_at := <datetime>'2024-06-01T00:00:00Z' }",
            eid=probe_id,
            oref=order_ref,
        )
        count = q_single(
            client, "select count((select Refund filter .external_id = <str>$eid))", eid=probe_id
        )
        assert count == 1, "A Refund with amount_cents = 1 must be accepted by the database."
    finally:
        client.query("delete Refund filter .external_id = <str>$eid", eid=probe_id)


def test_sale_computed_properties_are_declared(client):
    result = q(
        client,
        "select schema::ObjectType { "
        "  pointers: { name, cardinality, expr, target: { name } } "
        "} filter .name = 'default::Sale'",
    )
    assert result, "default::Sale was not found during schema introspection."
    pointers = {p["name"]: p for p in result[0]["pointers"]}
    for name in ("net_cents", "refund_count"):
        assert name in pointers, (
            f"default::Sale is missing the computed property {name!r}; found {sorted(pointers)}."
        )
        assert pointers[name]["expr"], f"default::Sale.{name} must be a computed property."
        assert pointers[name]["cardinality"] == "One", (
            f"default::Sale.{name} must be single-valued, got {pointers[name]['cardinality']!r}."
        )
        assert pointers[name]["target"]["name"] == "std::int64", (
            f"default::Sale.{name} must be of type std::int64, got "
            f"{pointers[name]['target']['name']}."
        )


def test_sale_has_channel_occurred_at_index(client):
    result = q(
        client,
        "select schema::ObjectType { indexes: { expr } } filter .name = 'default::Sale'",
    )
    assert result, "default::Sale was not found during schema introspection."
    exprs = [i["expr"] or "" for i in result[0]["indexes"]]
    assert any("channel" in e and "occurred_at" in e for e in exprs), (
        "default::Sale must declare an index over (.channel, .occurred_at); "
        f"found index expressions: {exprs}."
    )


def test_new_migration_file_was_created():
    files = sorted(n for n in os.listdir(MIGRATIONS_DIR) if n.endswith(".edgeql"))
    assert len(files) >= 2, (
        "The schema change must be delivered as a new migration file in "
        f"dbschema/migrations/; found only {files}."
    )


def test_migration_history_in_sync(client):
    proc = run_gel_cli(["migration", "status"])
    assert proc.returncode == 0, (
        "`gel migration status` reports the branch is not in sync with "
        f"dbschema/migrations.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


def test_migrations_recorded_in_database(client):
    count = q_single(client, "select count(schema::Migration)")
    assert count >= 2, (
        f"The database must have at least 2 applied migrations, found {count}. "
        "Bare DDL outside the migration system is not acceptable."
    )


# ---------------------------------------------------------------------------
# B. Computed property values
# ---------------------------------------------------------------------------


def test_net_cents_and_refund_count_match_the_refund_rows(client, canonical_state):
    sales = q(client, "select Sale { order_ref, amount_cents, net_cents, refund_count }")
    refunds = q(client, "select Refund { amount_cents, sale: { order_ref } }")
    totals = defaultdict(int)
    counts = defaultdict(int)
    for refund in refunds:
        totals[refund["sale"]["order_ref"]] += refund["amount_cents"]
        counts[refund["sale"]["order_ref"]] += 1
    for sale in sales:
        ref = sale["order_ref"]
        assert sale["net_cents"] == sale["amount_cents"] - totals.get(ref, 0), (
            f"Sale {ref}: net_cents is {sale['net_cents']} but amount_cents "
            f"({sale['amount_cents']}) minus refunds ({totals.get(ref, 0)}) is "
            f"{sale['amount_cents'] - totals.get(ref, 0)}."
        )
        assert sale["refund_count"] == counts.get(ref, 0), (
            f"Sale {ref}: refund_count is {sale['refund_count']} but it has "
            f"{counts.get(ref, 0)} refunds."
        )


def test_computed_properties_cover_the_interesting_cases(client, canonical_state):
    sales = q(client, "select Sale { order_ref, amount_cents, net_cents, refund_count }")
    assert any(
        s["refund_count"] == 0 and s["net_cents"] == s["amount_cents"] for s in sales
    ), "Expected at least one sale with no refunds whose net_cents equals amount_cents."
    assert any(s["refund_count"] >= 2 for s in sales), (
        "Expected at least one sale carrying two or more refunds after ingestion."
    )
    assert any(s["net_cents"] == 0 for s in sales), (
        "Expected at least one fully refunded sale with net_cents == 0 after ingestion."
    )


# ---------------------------------------------------------------------------
# C. Ingestion semantics
# ---------------------------------------------------------------------------


def test_ingest_is_idempotent_and_reports_totals(client, canonical_state):
    ingestable, skipped = ingestable_split(client)
    assert skipped, (
        "data/refunds.json is expected to contain records referencing unknown orders."
    )
    payload = cli_ingest(REFUNDS_FILE)
    assert payload["inserted"] == 0, (
        f"Re-ingesting the same file must insert nothing, got inserted={payload['inserted']}."
    )
    assert payload["updated"] == 0, (
        f"Re-ingesting the same file must update nothing, got updated={payload['updated']}."
    )
    assert payload["unchanged"] == len(ingestable), (
        f"Expected unchanged={len(ingestable)} (records whose order_ref exists), "
        f"got {payload['unchanged']}."
    )
    assert payload["skipped"] == len(skipped), (
        f"Expected skipped={len(skipped)} (records whose order_ref is unknown), "
        f"got {payload['skipped']}."
    )
    db_count, db_sum = refund_totals(client)
    assert payload["refund_total_count"] == db_count, (
        f"refund_total_count is {payload['refund_total_count']} but the database holds {db_count} refunds."
    )
    assert payload["refund_total_cents"] == db_sum, (
        f"refund_total_cents is {payload['refund_total_cents']} but sum(Refund.amount_cents) is {db_sum}."
    )


def test_ingest_updates_changed_records_and_restores_them(client, canonical_state):
    ingestable, _ = ingestable_split(client)
    assert ingestable, "data/refunds.json contains no ingestable records."
    target = sorted(ingestable, key=lambda r: r["external_id"])[0]
    original_amount = target["amount_cents"]
    order_ref = target["order_ref"]

    before_net = q_single(
        client,
        "select (select Sale filter .order_ref = <str>$oref limit 1).net_cents",
        oref=order_ref,
    )

    records = load_refund_file()
    for record in records:
        if record["external_id"] == target["external_id"]:
            record["amount_cents"] = original_amount + 1
    with open(VARIANT_FILE, "w", encoding="utf-8") as handle:
        json.dump(records, handle)

    payload = cli_ingest(VARIANT_FILE)
    assert payload["inserted"] == 0, f"Expected inserted=0, got {payload['inserted']}."
    assert payload["updated"] == 1, (
        f"Changing one record's amount_cents must report updated=1, got {payload['updated']}."
    )
    assert payload["unchanged"] == len(ingestable) - 1, (
        f"Expected unchanged={len(ingestable) - 1}, got {payload['unchanged']}."
    )

    stored = q(
        client,
        "select Refund { amount_cents } filter .external_id = <str>$eid",
        eid=target["external_id"],
    )
    assert stored and stored[0]["amount_cents"] == original_amount + 1, (
        f"Refund {target['external_id']} was not updated to {original_amount + 1}; stored value: {stored}."
    )
    after_net = q_single(
        client,
        "select (select Sale filter .order_ref = <str>$oref limit 1).net_cents",
        oref=order_ref,
    )
    assert after_net == before_net - 1, (
        f"Sale {order_ref} net_cents should have dropped by 1 (from {before_net} to "
        f"{before_net - 1}) after the refund grew by 1, got {after_net}."
    )

    restored = cli_ingest(REFUNDS_FILE)
    assert restored["updated"] == 1, (
        f"Re-ingesting the original file must restore the record (updated=1), got {restored['updated']}."
    )
    stored = q(
        client,
        "select Refund { amount_cents } filter .external_id = <str>$eid",
        eid=target["external_id"],
    )
    assert stored and stored[0]["amount_cents"] == original_amount, (
        f"Refund {target['external_id']} was not restored to {original_amount}; stored value: {stored}."
    )
    final_net = q_single(
        client,
        "select (select Sale filter .order_ref = <str>$oref limit 1).net_cents",
        oref=order_ref,
    )
    assert final_net == before_net, (
        f"Sale {order_ref} net_cents should be back to {before_net}, got {final_net}."
    )


def test_invalid_amount_aborts_without_partial_writes(client, canonical_state):
    ingestable, _ = ingestable_split(client)
    before_count, before_sum = refund_totals(client)
    records = load_refund_file()
    records.append(
        {
            "external_id": "ZZ-INVALID-AMOUNT",
            "order_ref": ingestable[0]["order_ref"],
            "amount_cents": 0,
            "refunded_at": "2024-06-01T00:00:00Z",
        }
    )
    with open(BAD_AMOUNT_FILE, "w", encoding="utf-8") as handle:
        json.dump(records, handle)

    proc = run_cli(["ingest-refunds", "--file", BAD_AMOUNT_FILE])
    assert proc.returncode == 4, (
        f"An invalid refunds file must exit 4, got {proc.returncode}.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert proc.stdout.strip() == "", (
        f"stdout must be empty on failure, got {proc.stdout!r}."
    )
    assert "invalid refunds file" in proc.stderr, (
        f"stderr must contain 'invalid refunds file', got {proc.stderr!r}."
    )

    after_count, after_sum = refund_totals(client)
    assert (after_count, after_sum) == (before_count, before_sum), (
        "A rejected refunds file must leave the database unchanged; refund count/sum went "
        f"from {(before_count, before_sum)} to {(after_count, after_sum)}."
    )
    leaked = q_single(
        client, "select count((select Refund filter .external_id = 'ZZ-INVALID-AMOUNT'))"
    )
    assert leaked == 0, "The rejected file must not have written any of its records."


def test_duplicate_external_id_in_file_aborts(client, canonical_state):
    ingestable, _ = ingestable_split(client)
    before_count, before_sum = refund_totals(client)
    duplicate = [
        {
            "external_id": "ZZ-DUPLICATE",
            "order_ref": ingestable[0]["order_ref"],
            "amount_cents": 11,
            "refunded_at": "2024-06-01T00:00:00Z",
        },
        {
            "external_id": "ZZ-DUPLICATE",
            "order_ref": ingestable[0]["order_ref"],
            "amount_cents": 12,
            "refunded_at": "2024-06-02T00:00:00Z",
        },
    ]
    with open(DUP_FILE, "w", encoding="utf-8") as handle:
        json.dump(duplicate, handle)

    proc = run_cli(["ingest-refunds", "--file", DUP_FILE])
    assert proc.returncode == 4, (
        f"A file with a duplicated external_id must exit 4, got {proc.returncode}.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert proc.stdout.strip() == "", f"stdout must be empty on failure, got {proc.stdout!r}."
    assert "invalid refunds file" in proc.stderr, (
        f"stderr must contain 'invalid refunds file', got {proc.stderr!r}."
    )
    after_count, after_sum = refund_totals(client)
    assert (after_count, after_sum) == (before_count, before_sum), (
        "A duplicate-id file must leave the database unchanged; refund count/sum went "
        f"from {(before_count, before_sum)} to {(after_count, after_sum)}."
    )


def test_missing_refunds_file_exits_three(client, canonical_state):
    if os.path.exists(MISSING_FILE):
        os.remove(MISSING_FILE)
    proc = run_cli(["ingest-refunds", "--file", MISSING_FILE])
    assert proc.returncode == 3, (
        f"A missing --file path must exit 3, got {proc.returncode}.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert proc.stdout.strip() == "", f"stdout must be empty on failure, got {proc.stdout!r}."
    assert "refunds file not found" in proc.stderr, (
        f"stderr must contain 'refunds file not found', got {proc.stderr!r}."
    )


@pytest.mark.parametrize("argv", [[], ["bogus-subcommand"]])
def test_bad_subcommand_exits_two(client, canonical_state, argv):
    proc = run_cli(argv)
    assert proc.returncode == 2, (
        f"`python3 -m analytics.cli {' '.join(argv)}` must exit 2, got {proc.returncode}.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert proc.stdout.strip() == "", f"stdout must be empty on failure, got {proc.stdout!r}."


# ---------------------------------------------------------------------------
# D. Report, full window
# ---------------------------------------------------------------------------


def test_full_window_report_matches_independent_oracle(client, canonical_state):
    actual = cli_report()
    sales, refunds, categories = fetch_raw(client)
    expected = build_expected(sales, refunds, categories, month=None)
    compare_report(actual, expected, "report (full window)")


def test_full_window_report_structural_invariants(client, canonical_state):
    actual = cli_report()
    sales, refunds, categories = fetch_raw(client)
    expected = build_expected(sales, refunds, categories, month=None)

    pairs = {(e["month"], e["channel"]) for e in expected["monthly_by_channel"]}
    months = {e["month"] for e in expected["monthly_by_channel"]}
    channels = {e["channel"] for e in expected["monthly_by_channel"]}
    assert len(pairs) < len(months) * len(channels), (
        "The seeded data is expected to contain at least one (month, channel) pair with no "
        "sales; the oracle found none, so the absence rule cannot be verified."
    )
    assert any(e["stddev_net_cents"] is None for e in expected["monthly_by_channel"]), (
        "The seeded data is expected to contain a single-sale (month, channel) group."
    )

    share_sum = sum(e["share_pct"] for e in actual["channel_totals"])
    assert abs(share_sum - 100.0) <= 0.05, (
        f"channel_totals share_pct values must sum to ~100, got {share_sum}."
    )

    ranks = [e["rank"] for e in actual["category_rank"]]
    assert ranks == sorted(ranks), f"category_rank must be ordered by rank ascending, got {ranks}."
    nets = {}
    for entry in actual["category_rank"]:
        nets.setdefault(entry["net_cents"], set()).add(entry["rank"])
    for net_value, rank_values in nets.items():
        assert len(rank_values) == 1, (
            f"Categories tied on net_cents={net_value} must share a rank, got ranks {sorted(rank_values)}."
        )

    assert len(expected["empty_categories"]) >= 1, (
        "The seeded data is expected to contain categories without any sale."
    )
    assert actual["empty_categories"] == expected["empty_categories"], (
        f"empty_categories expected {expected['empty_categories']}, got {actual['empty_categories']}."
    )


# ---------------------------------------------------------------------------
# E. Report, month windows
# ---------------------------------------------------------------------------


def test_month_window_reports_match_independent_oracle(client, canonical_state):
    sales, refunds, categories = fetch_raw(client)
    months = sorted({parse_dt(s["occurred_at"]).strftime("%Y-%m") for s in sales})
    assert len(months) >= 2, f"Expected the seeded data to span several months, found {months}."
    for month in months:
        actual = cli_report(["--month", month])
        expected = build_expected(sales, refunds, categories, month=month)
        compare_report(actual, expected, f"report --month {month}")
        assert actual["window"] == {"month": month}, (
            f"report --month {month}: window must be {{'month': {month!r}}}, got {actual['window']!r}."
        )
        for entry in actual["monthly_by_channel"]:
            assert entry["month"] == month, (
                f"report --month {month}: monthly_by_channel contains an entry for "
                f"{entry['month']!r}."
            )


def test_month_windows_partition_the_full_window(client, canonical_state):
    sales, refunds, categories = fetch_raw(client)
    months = sorted({parse_dt(s["occurred_at"]).strftime("%Y-%m") for s in sales})
    full = cli_report()
    total = 0
    sale_total = 0
    for month in months:
        report = cli_report(["--month", month])
        total += report["grand_total"]["net_cents"]
        sale_total += report["grand_total"]["sale_count"]
    assert total == full["grand_total"]["net_cents"], (
        f"The month-scoped net_cents values sum to {total} but the full window reports "
        f"{full['grand_total']['net_cents']}."
    )
    assert sale_total == full["grand_total"]["sale_count"], (
        f"The month-scoped sale counts sum to {sale_total} but the full window reports "
        f"{full['grand_total']['sale_count']}."
    )


def test_empty_month_window(client, canonical_state):
    actual = cli_report(["--month", "2019-07"])
    _, _, categories = fetch_raw(client)
    assert actual["window"] == {"month": "2019-07"}, (
        f"window must be {{'month': '2019-07'}}, got {actual['window']!r}."
    )
    assert actual["monthly_by_channel"] == [], (
        f"monthly_by_channel must be empty for a month with no sales, got {actual['monthly_by_channel']!r}."
    )
    assert actual["channel_totals"] == [], (
        f"channel_totals must be empty for a month with no sales, got {actual['channel_totals']!r}."
    )
    assert actual["category_rank"] == [], (
        f"category_rank must be empty for a month with no sales, got {actual['category_rank']!r}."
    )
    assert actual["empty_categories"] == sorted(c["name"] for c in categories), (
        "For a month with no sales every category must be listed in empty_categories; got "
        f"{actual['empty_categories']!r}."
    )
    for key in sorted(GRAND_TOTAL_KEYS):
        check_int(actual["grand_total"][key], 0, f"empty window: grand_total.{key}")


@pytest.mark.parametrize("month", ["2024-13", "24-01", "notamonth", "2024-00"])
def test_invalid_month_exits_five(client, canonical_state, month):
    proc = run_cli(["report", "--month", month])
    assert proc.returncode == 5, (
        f"`report --month {month}` must exit 5, got {proc.returncode}.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert proc.stdout.strip() == "", f"stdout must be empty on failure, got {proc.stdout!r}."
    assert "invalid month" in proc.stderr, (
        f"stderr must contain 'invalid month', got {proc.stderr!r}."
    )


# ---------------------------------------------------------------------------
# F. Async Python API surface
# ---------------------------------------------------------------------------


def test_rollups_exposes_coroutine_functions(client, canonical_state):
    import inspect

    module = load_rollups()
    for name in ("build_report", "ingest_refunds"):
        assert hasattr(module, name), f"analytics.rollups does not define {name}."
        assert inspect.iscoroutinefunction(getattr(module, name)), (
            f"analytics.rollups.{name} must be defined with `async def`."
        )


def test_build_report_api_matches_cli_full_window(client, canonical_state):
    module = load_rollups()
    result = call_async(lambda c: module.build_report(c))
    assert isinstance(result, dict), (
        f"build_report must return a dict, got {type(result).__name__}."
    )
    cli_payload = cli_report()
    assert json.dumps(result, sort_keys=True) == json.dumps(cli_payload, sort_keys=True), (
        "analytics.rollups.build_report(client) must return exactly what "
        "`python3 -m analytics.cli report` prints.\n"
        f"API: {json.dumps(result, sort_keys=True)[:2000]}\n"
        f"CLI: {json.dumps(cli_payload, sort_keys=True)[:2000]}"
    )


def test_build_report_api_matches_cli_month_window(client, canonical_state):
    module = load_rollups()
    sales, _, _ = fetch_raw(client)
    month = sorted({parse_dt(s["occurred_at"]).strftime("%Y-%m") for s in sales})[0]
    result = call_async(lambda c: module.build_report(c, month=month))
    cli_payload = cli_report(["--month", month])
    assert json.dumps(result, sort_keys=True) == json.dumps(cli_payload, sort_keys=True), (
        f"build_report(client, month={month!r}) must match `report --month {month}`."
    )


def test_build_report_rejects_malformed_month(client, canonical_state):
    module = load_rollups()
    with pytest.raises(ValueError):
        call_async(lambda c: module.build_report(c, month="2024-1"))


def test_build_report_result_is_json_serialisable(client, canonical_state):
    module = load_rollups()
    result = call_async(lambda c: module.build_report(c))
    try:
        json.dumps(result)
    except TypeError as exc:
        pytest.fail(
            "build_report must return plain JSON-serialisable Python values "
            f"(no Decimal / datetime / Gel objects): {exc}"
        )


def test_ingest_refunds_api_is_idempotent(client, canonical_state):
    module = load_rollups()
    records = load_refund_file()
    ingestable, skipped = ingestable_split(client)
    result = call_async(lambda c: module.ingest_refunds(c, records))
    assert isinstance(result, dict) and set(result.keys()) == INGEST_KEYS, (
        f"ingest_refunds must return an object with exactly the keys {sorted(INGEST_KEYS)}, got "
        f"{sorted(result.keys()) if isinstance(result, dict) else result!r}."
    )
    assert result["inserted"] == 0, (
        f"Re-running ingest_refunds with the same records must insert nothing, got {result['inserted']}."
    )
    assert result["unchanged"] == len(ingestable), (
        f"Expected unchanged={len(ingestable)}, got {result['unchanged']}."
    )
    assert result["skipped"] == len(skipped), (
        f"Expected skipped={len(skipped)}, got {result['skipped']}."
    )


def test_ingest_refunds_api_rejects_invalid_amount(client, canonical_state):
    module = load_rollups()
    ingestable, _ = ingestable_split(client)
    before_count, before_sum = refund_totals(client)
    records = load_refund_file()
    records.append(
        {
            "external_id": "ZZ-API-INVALID",
            "order_ref": ingestable[0]["order_ref"],
            "amount_cents": 0,
            "refunded_at": "2024-06-01T00:00:00Z",
        }
    )
    with pytest.raises(ValueError):
        call_async(lambda c: module.ingest_refunds(c, records))
    after_count, after_sum = refund_totals(client)
    assert (after_count, after_sum) == (before_count, before_sum), (
        "ingest_refunds must not write anything when the record list is invalid; refund "
        f"count/sum went from {(before_count, before_sum)} to {(after_count, after_sum)}."
    )


# ---------------------------------------------------------------------------
# G. Non-runtime constraint (secondary signal only)
# ---------------------------------------------------------------------------


def test_report_query_uses_top_level_group_statement():
    with open(ROLLUPS_PATH, encoding="utf-8") as handle:
        source = handle.read()
    assert re.search(r"\bgroup\b[\s\S]{0,4000}?\bby\b", source, re.IGNORECASE), (
        "analytics/rollups.py must build the report with EdgeQL's top-level "
        "`group ... by ...` statement."
    )


def test_shutil_which_still_finds_gel():
    assert shutil.which("gel") is not None, "The `gel` CLI disappeared from PATH."
