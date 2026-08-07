import glob
import hashlib
import json
import os
import shutil
import subprocess

import pytest

PROJECT_DIR = "/home/user/recovery"
BACKUPS_DIR = os.path.join(PROJECT_DIR, "backups")
SOURCE_DUMP = os.path.join(BACKUPS_DIR, "pre_incident.dump")
FRESH_DUMP = os.path.join(BACKUPS_DIR, "recovered.dump")
REPORT_SCRIPT = os.path.join(PROJECT_DIR, "scripts", "verify_recovery.sh")

TARGET_BRANCH = "recovered"
ROUNDTRIP_BRANCH = "verify_roundtrip"
SCRATCH_BRANCH = "test_scratch_restore"

# seq -> (tracking, weight_kg, status, warehouse code)
EXPECTED_ROWS = {
    1: ("GL-100001", 12.5, "delivered", "WH-ALPHA"),
    2: ("GL-100002", 3.25, "pending", "WH-ALPHA"),
    3: ("GL-100003", 7.0, "in_transit", "WH-BRAVO"),
    4: ("GL-100004", 1.75, "returned", "WH-CHARLIE"),
    5: ("GL-100005", 22.0, "delivered", "WH-ECHO"),
    6: ("GL-100006", 5.5, "delivered", "WH-ALPHA"),
    7: ("GL100007", 8.0, "pending", "WH-BRAVO"),
    8: ("GL-100008", 2.25, "in_transit", "WH-CHARLIE"),
    9: ("GL-100009", 4.0, "delivered", "WH-ALPHA"),
    12: ("GL-100011", 9.5, "delivered", "WH-ECHO"),
    13: ("GL-100013", 3.0, "pending", "WH-ALPHA"),
    15: ("GL-100015", 4.5, "delivered", "WH-BRAVO"),
    16: ("GL-100016", 0.25, "returned", "WH-CHARLIE"),
    19: ("GL-100019", 1.5, "in_transit", "WH-BRAVO"),
    20: ("GL-100020", 2.5, "in_transit", "WH-BRAVO"),
    21: ("GL-100021", 3.5, "delivered", "WH-CHARLIE"),
    22: ("GL-100022", 4.5, "delivered", "WH-CHARLIE"),
    23: ("GL-100023", 5.5, "pending", "WH-ALPHA"),
    24: ("GL-100024", 6.5, "returned", "WH-ECHO"),
    25: ("GL-100025", 7.5, "pending", "WH-ECHO"),
    26: ("GL-100026", 8.5, "delivered", "WH-ALPHA"),
    27: ("GL-100027", 9.0, "pending", "WH-CHARLIE"),
    28: ("GL-100028", 10.0, "delivered", "WH-ECHO"),
}

DELETED_SEQS = [10, 11, 14, 17, 18, 29, 30]

EXPECTED_WAREHOUSES = {
    "WH-ALPHA": "Alpha Depot",
    "WH-BRAVO": "Bravo Depot",
    "WH-CHARLIE": "Charlie Depot",
    "WH-ECHO": "Echo Depot",
}

EXPECTED_STATUS_COUNTS = {
    "delivered": 10,
    "in_transit": 4,
    "pending": 6,
    "returned": 3,
}

EXPECTED_WAREHOUSE_COUNTS = {
    "WH-ALPHA": 7,
    "WH-BRAVO": 5,
    "WH-CHARLIE": 6,
    "WH-ECHO": 5,
}

EXPECTED_TOTAL_WEIGHT = 142.5

REPORT_KEYS = [
    "branch",
    "dump_path",
    "dump_size_bytes",
    "dump_sha256",
    "migration_count",
    "counts",
    "status_counts",
    "warehouse_counts",
    "total_weight_kg",
    "tracking_checksum",
    "roundtrip_branch",
    "roundtrip_ok",
]

SHIPMENT_SHAPE = (
    "select Shipment { seq, tracking, weight_kg, status, origin_code, "
    "origin: { code } } order by .seq"
)


def run(cmd, cwd=PROJECT_DIR, timeout=600):
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout
    )


@pytest.fixture(scope="session")
def gel_server():
    """Ensure the local Gel server is accepting connections before any DB check."""
    proc = subprocess.run(["gel-start"], capture_output=True, text=True, timeout=900)
    assert proc.returncode == 0, (
        "gel-start failed to bring the local Gel server up:\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    probe = run(["gel", "query", "-F", "json", "--branch", "main", "select 1"])
    assert probe.returncode == 0, (
        "Could not query the local Gel instance after gel-start:\n"
        f"stdout={probe.stdout}\nstderr={probe.stderr}"
    )
    return True


def query_json(query, branch=TARGET_BRANCH):
    proc = run(["gel", "query", "-F", "json", "--branch", branch, query])
    assert proc.returncode == 0, (
        f"Query {query!r} on branch {branch!r} failed:\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    return json.loads(proc.stdout)


def branch_names():
    return query_json("select sys::Branch.name", branch="main")


def drop_branch_if_exists(name):
    if name in branch_names():
        run(["gel", "branch", "drop", "--non-interactive", name])


def shipments(branch=TARGET_BRANCH):
    return query_json(SHIPMENT_SHAPE, branch=branch)


def tracking_checksum(trackings):
    joined = "\n".join(sorted(trackings))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def run_report(args=None, cwd="/tmp"):
    cmd = ["bash", REPORT_SCRIPT]
    if args:
        cmd.extend(args)
    return run(cmd, cwd=cwd)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------
# Branch / data recovery
# --------------------------------------------------------------------------


def test_recovered_branch_exists(gel_server):
    names = branch_names()
    assert TARGET_BRANCH in names, (
        f"Branch {TARGET_BRANCH!r} does not exist on the instance. Found: {names}"
    )
    assert "main" in names, f"Branch 'main' disappeared from the instance. Found: {names}"


def test_recovered_row_counts(gel_server):
    warehouses = query_json("select count(Warehouse)")[0]
    ships = query_json("select count(Shipment)")[0]
    assert warehouses == 4, (
        f"Branch {TARGET_BRANCH!r} must hold 4 warehouses after the repair, found {warehouses}."
    )
    assert ships == len(EXPECTED_ROWS), (
        f"Branch {TARGET_BRANCH!r} must hold {len(EXPECTED_ROWS)} shipments after the "
        f"repair, found {ships}."
    )


def test_recovered_warehouses(gel_server):
    rows = query_json("select Warehouse { code, name } order by .code")
    got = {row["code"]: row["name"] for row in rows}
    assert got == EXPECTED_WAREHOUSES, (
        f"Unexpected warehouses on {TARGET_BRANCH!r}. Expected {EXPECTED_WAREHOUSES}, got {got}."
    )


def test_recovered_surviving_seqs(gel_server):
    seqs = sorted(row["seq"] for row in shipments())
    assert seqs == sorted(EXPECTED_ROWS), (
        f"Unexpected surviving shipment seq values on {TARGET_BRANCH!r}.\n"
        f"Expected: {sorted(EXPECTED_ROWS)}\nGot: {seqs}"
    )
    for seq in DELETED_SEQS:
        assert seq not in seqs, (
            f"Shipment seq={seq} should have been deleted by the repair rules but is "
            f"still present on {TARGET_BRANCH!r}."
        )


def test_recovered_rows_are_normalized(gel_server):
    rows = {row["seq"]: row for row in shipments()}
    for seq, (tracking, weight, status, code) in EXPECTED_ROWS.items():
        row = rows.get(seq)
        assert row is not None, f"Shipment seq={seq} is missing from {TARGET_BRANCH!r}."
        assert row["tracking"] == tracking, (
            f"Shipment seq={seq}: tracking should be {tracking!r}, got {row['tracking']!r}."
        )
        assert abs(row["weight_kg"] - weight) < 1e-9, (
            f"Shipment seq={seq}: weight_kg should be {weight}, got {row['weight_kg']}."
        )
        assert row["status"] == status, (
            f"Shipment seq={seq}: status should be {status!r}, got {row['status']!r}."
        )
        assert row["origin"] is not None, (
            f"Shipment seq={seq}: origin link is missing on {TARGET_BRANCH!r}."
        )
        assert row["origin"]["code"] == code, (
            f"Shipment seq={seq}: origin should be warehouse {code!r}, "
            f"got {row['origin']['code']!r}."
        )
        assert row["origin_code"] == code, (
            f"Shipment seq={seq}: origin_code should have been rewritten to {code!r}, "
            f"got {row['origin_code']!r}."
        )


def test_recovered_aggregates(gel_server):
    rows = shipments()
    total = sum(row["weight_kg"] for row in rows)
    assert abs(total - EXPECTED_TOTAL_WEIGHT) < 1e-6, (
        f"Total weight on {TARGET_BRANCH!r} should be {EXPECTED_TOTAL_WEIGHT}, got {total}."
    )
    status_counts = {key: 0 for key in EXPECTED_STATUS_COUNTS}
    warehouse_counts = {key: 0 for key in EXPECTED_WAREHOUSE_COUNTS}
    for row in rows:
        assert row["status"] in status_counts, (
            f"Shipment seq={row['seq']} carries a non-canonical status {row['status']!r}."
        )
        status_counts[row["status"]] += 1
        warehouse_counts[row["origin"]["code"]] = (
            warehouse_counts.get(row["origin"]["code"], 0) + 1
        )
    assert status_counts == EXPECTED_STATUS_COUNTS, (
        f"Status histogram on {TARGET_BRANCH!r} should be {EXPECTED_STATUS_COUNTS}, "
        f"got {status_counts}."
    )
    assert warehouse_counts == EXPECTED_WAREHOUSE_COUNTS, (
        f"Per-warehouse shipment counts on {TARGET_BRANCH!r} should be "
        f"{EXPECTED_WAREHOUSE_COUNTS}, got {warehouse_counts}."
    )


# --------------------------------------------------------------------------
# Hardened schema
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label,statement",
    [
        (
            "shipment without origin",
            "insert Shipment { seq := 901, tracking := 'ZZ-90001', "
            "weight_kg := 1.0, status := 'pending' }",
        ),
        (
            "lower-case tracking",
            "insert Shipment { seq := 902, tracking := 'zz-90002', weight_kg := 1.0, "
            "status := 'pending', origin := assert_single("
            "(select Warehouse filter .code = 'WH-ALPHA')) }",
        ),
        (
            "duplicate tracking",
            "insert Shipment { seq := 903, tracking := 'GL-100001', weight_kg := 1.0, "
            "status := 'pending', origin := assert_single("
            "(select Warehouse filter .code = 'WH-ALPHA')) }",
        ),
        (
            "negative weight",
            "insert Shipment { seq := 904, tracking := 'ZZ-90004', weight_kg := -1.0, "
            "status := 'pending', origin := assert_single("
            "(select Warehouse filter .code = 'WH-ALPHA')) }",
        ),
        (
            "zero weight",
            "insert Shipment { seq := 905, tracking := 'ZZ-90005', weight_kg := 0.0, "
            "status := 'pending', origin := assert_single("
            "(select Warehouse filter .code = 'WH-ALPHA')) }",
        ),
        (
            "unknown status",
            "insert Shipment { seq := 906, tracking := 'ZZ-90006', weight_kg := 1.0, "
            "status := 'shipped', origin := assert_single("
            "(select Warehouse filter .code = 'WH-ALPHA')) }",
        ),
        (
            "lower-case warehouse code",
            "insert Warehouse { code := 'wh-foxtrot', name := 'Foxtrot Depot' }",
        ),
    ],
)
def test_hardened_schema_rejects_invalid_objects(gel_server, label, statement):
    proc = run(["gel", "query", "--branch", TARGET_BRANCH, statement])
    if proc.returncode == 0:
        # Undo the object that should never have been accepted so the remaining
        # checks still see a pristine branch.
        run(
            [
                "gel",
                "query",
                "--branch",
                TARGET_BRANCH,
                "delete Shipment filter .seq >= 900 and .seq < 1000;"
                " delete Warehouse filter .code = 'wh-foxtrot';",
            ]
        )
    assert proc.returncode != 0, (
        f"The hardened schema on {TARGET_BRANCH!r} accepted an invalid object ({label}).\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    combined = proc.stdout + proc.stderr
    assert "UnknownDatabaseError" not in combined, (
        f"The insert ({label}) was not rejected by the schema but by a missing branch:\n"
        f"{combined}"
    )


def test_hardened_schema_accepts_valid_objects(gel_server):
    insert = (
        "insert Shipment { seq := 999, tracking := 'ZZ-99999', weight_kg := 1.5, "
        "status := 'pending', origin_code := 'WH-ALPHA', origin := assert_single("
        "(select Warehouse filter .code = 'WH-ALPHA')) }"
    )
    try:
        proc = run(["gel", "query", "--branch", TARGET_BRANCH, insert])
        assert proc.returncode == 0, (
            "A fully valid shipment must still be insertable on "
            f"{TARGET_BRANCH!r}.\nstdout={proc.stdout}\nstderr={proc.stderr}"
        )
    finally:
        run(
            [
                "gel",
                "query",
                "--branch",
                TARGET_BRANCH,
                "delete Shipment filter .seq = 999",
            ]
        )
    remaining = query_json("select count(Shipment)")[0]
    assert remaining == len(EXPECTED_ROWS), (
        f"After the probe insert/delete, {TARGET_BRANCH!r} must hold "
        f"{len(EXPECTED_ROWS)} shipments again, found {remaining}."
    )


# --------------------------------------------------------------------------
# Migration state
# --------------------------------------------------------------------------


def test_migration_history_extended(gel_server):
    count = query_json("select count(schema::Migration)")[0]
    assert count >= 2, (
        f"Branch {TARGET_BRANCH!r} should have at least 2 migrations in its history "
        f"(the restored one plus the hardening one), found {count}."
    )
    root_recovered = query_json(
        "select (select schema::Migration filter not exists .parents).name"
    )
    root_main = query_json(
        "select (select schema::Migration filter not exists .parents).name",
        branch="main",
    )
    assert root_recovered == root_main, (
        "The restored migration history was rewritten: the root migration of "
        f"{TARGET_BRANCH!r} ({root_recovered}) differs from main's ({root_main})."
    )


def test_migration_status_in_sync(gel_server):
    proc = run(["gel", "migration", "status", "--branch", TARGET_BRANCH])
    assert proc.returncode == 0, (
        f"`gel migration status --branch {TARGET_BRANCH}` failed:\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    combined = (proc.stdout + proc.stderr).lower()
    assert "up to date" in combined, (
        f"Branch {TARGET_BRANCH!r} is not in sync with dbschema/: {proc.stdout} {proc.stderr}"
    )


def test_migration_files_added(gel_server):
    migrations = sorted(
        glob.glob(os.path.join(PROJECT_DIR, "dbschema", "migrations", "*.edgeql"))
    )
    assert len(migrations) >= 2, (
        "dbschema/migrations must contain the original migration plus at least one new "
        f"one, found: {migrations}"
    )


def test_main_branch_untouched(gel_server):
    warehouses = query_json("select count(Warehouse)", branch="main")[0]
    ships = query_json("select count(Shipment)", branch="main")[0]
    migrations = query_json("select count(schema::Migration)", branch="main")[0]
    assert warehouses == 4, f"Branch main must still hold 4 warehouses, found {warehouses}."
    assert ships == 24, f"Branch main must still hold 24 shipments, found {ships}."
    assert migrations == 1, (
        f"Branch main's migration history must still hold exactly 1 migration, found {migrations}."
    )


# --------------------------------------------------------------------------
# Fresh dump
# --------------------------------------------------------------------------


def test_fresh_dump_file_present(gel_server):
    assert os.path.isfile(FRESH_DUMP), f"The fresh backup {FRESH_DUMP} does not exist."
    assert os.path.getsize(FRESH_DUMP) > 0, f"The fresh backup {FRESH_DUMP} is empty."
    assert os.path.isfile(SOURCE_DUMP), (
        f"The provided pre-incident backup {SOURCE_DUMP} must be kept in place."
    )


def test_fresh_dump_restores_into_scratch_branch(gel_server):
    expected = {row["seq"]: row["tracking"] for row in shipments()}
    drop_branch_if_exists(SCRATCH_BRANCH)
    try:
        created = run(["gel", "branch", "create", "--empty", SCRATCH_BRANCH])
        assert created.returncode == 0, (
            f"Could not create the scratch branch {SCRATCH_BRANCH!r}:\n"
            f"stdout={created.stdout}\nstderr={created.stderr}"
        )
        restored = run(["gel", "restore", "--branch", SCRATCH_BRANCH, FRESH_DUMP])
        assert restored.returncode == 0, (
            f"Restoring {FRESH_DUMP} into an empty branch failed:\n"
            f"stdout={restored.stdout}\nstderr={restored.stderr}"
        )
        warehouses = query_json("select count(Warehouse)", branch=SCRATCH_BRANCH)[0]
        assert warehouses == 4, (
            f"The restored copy of {FRESH_DUMP} holds {warehouses} warehouses, expected 4."
        )
        rows = shipments(branch=SCRATCH_BRANCH)
        got = {row["seq"]: row["tracking"] for row in rows}
        assert got == expected, (
            "The restored copy of the fresh dump does not match the recovered branch.\n"
            f"Expected: {expected}\nGot: {got}"
        )
    finally:
        drop_branch_if_exists(SCRATCH_BRANCH)


# --------------------------------------------------------------------------
# Verification CLI
# --------------------------------------------------------------------------


def test_report_script_exists(gel_server):
    assert os.path.isfile(REPORT_SCRIPT), (
        f"The verification command {REPORT_SCRIPT} does not exist."
    )


def test_report_happy_path(gel_server):
    proc = run_report()
    assert proc.returncode == 0, (
        f"`bash {REPORT_SCRIPT}` should exit 0, got {proc.returncode}.\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"stdout of {REPORT_SCRIPT} is not a single JSON object: {exc}\n"
            f"stdout={proc.stdout!r}"
        )
    assert list(report.keys()) == REPORT_KEYS, (
        f"The report's top-level keys must be exactly {REPORT_KEYS} in that order, "
        f"got {list(report.keys())}."
    )
    assert report["branch"] == TARGET_BRANCH, (
        f"report['branch'] should be {TARGET_BRANCH!r}, got {report['branch']!r}."
    )
    assert report["dump_path"] == FRESH_DUMP, (
        f"report['dump_path'] should be {FRESH_DUMP!r}, got {report['dump_path']!r}."
    )
    assert report["dump_size_bytes"] == os.path.getsize(FRESH_DUMP), (
        f"report['dump_size_bytes'] ({report['dump_size_bytes']}) does not match the real "
        f"size of {FRESH_DUMP} ({os.path.getsize(FRESH_DUMP)})."
    )
    assert report["dump_sha256"] == sha256_file(FRESH_DUMP), (
        f"report['dump_sha256'] ({report['dump_sha256']}) does not match the real SHA-256 "
        f"of {FRESH_DUMP} ({sha256_file(FRESH_DUMP)})."
    )
    live_migrations = query_json("select count(schema::Migration)")[0]
    assert report["migration_count"] == live_migrations, (
        f"report['migration_count'] ({report['migration_count']}) does not match the "
        f"branch's real migration count ({live_migrations})."
    )
    assert list(report["counts"].keys()) == ["Warehouse", "Shipment"], (
        "report['counts'] must have exactly the keys ['Warehouse', 'Shipment'] in that "
        f"order, got {list(report['counts'].keys())}."
    )
    assert report["counts"] == {"Warehouse": 4, "Shipment": len(EXPECTED_ROWS)}, (
        f"report['counts'] should be {{'Warehouse': 4, 'Shipment': {len(EXPECTED_ROWS)}}}, "
        f"got {report['counts']}."
    )
    assert list(report["status_counts"].keys()) == sorted(EXPECTED_STATUS_COUNTS), (
        "report['status_counts'] must have exactly the keys "
        f"{sorted(EXPECTED_STATUS_COUNTS)} in that order, got "
        f"{list(report['status_counts'].keys())}."
    )
    assert report["status_counts"] == EXPECTED_STATUS_COUNTS, (
        f"report['status_counts'] should be {EXPECTED_STATUS_COUNTS}, got "
        f"{report['status_counts']}."
    )
    assert list(report["warehouse_counts"].keys()) == sorted(EXPECTED_WAREHOUSE_COUNTS), (
        "report['warehouse_counts'] keys must be the warehouse codes sorted ascending "
        f"({sorted(EXPECTED_WAREHOUSE_COUNTS)}), got {list(report['warehouse_counts'].keys())}."
    )
    assert report["warehouse_counts"] == EXPECTED_WAREHOUSE_COUNTS, (
        f"report['warehouse_counts'] should be {EXPECTED_WAREHOUSE_COUNTS}, got "
        f"{report['warehouse_counts']}."
    )
    assert abs(report["total_weight_kg"] - EXPECTED_TOTAL_WEIGHT) < 1e-9, (
        f"report['total_weight_kg'] should be {EXPECTED_TOTAL_WEIGHT}, got "
        f"{report['total_weight_kg']}."
    )
    expected_checksum = tracking_checksum(row["tracking"] for row in shipments())
    assert report["tracking_checksum"] == expected_checksum, (
        f"report['tracking_checksum'] should be {expected_checksum}, got "
        f"{report['tracking_checksum']}."
    )
    assert report["roundtrip_branch"] == ROUNDTRIP_BRANCH, (
        f"report['roundtrip_branch'] should be {ROUNDTRIP_BRANCH!r}, got "
        f"{report['roundtrip_branch']!r}."
    )
    assert report["roundtrip_ok"] is True, (
        f"report['roundtrip_ok'] should be true, got {report['roundtrip_ok']!r}."
    )


def test_report_leaves_no_scratch_branch_and_is_idempotent(gel_server):
    first = run_report()
    assert first.returncode == 0, (
        f"The first run of {REPORT_SCRIPT} should exit 0, got {first.returncode}.\n"
        f"stdout={first.stdout}\nstderr={first.stderr}"
    )
    names = branch_names()
    assert ROUNDTRIP_BRANCH not in names, (
        f"The throw-away branch {ROUNDTRIP_BRANCH!r} must be dropped once the command "
        f"finished. Branches: {names}"
    )
    second = run_report()
    assert second.returncode == 0, (
        f"The second run of {REPORT_SCRIPT} should exit 0, got {second.returncode}.\n"
        f"stdout={second.stdout}\nstderr={second.stderr}"
    )
    assert json.loads(first.stdout) == json.loads(second.stdout), (
        "Running the verification command twice must produce the same report.\n"
        f"first={first.stdout!r}\nsecond={second.stdout!r}"
    )
    names = branch_names()
    assert ROUNDTRIP_BRANCH not in names, (
        f"The throw-away branch {ROUNDTRIP_BRANCH!r} must not survive a second run. "
        f"Branches: {names}"
    )


def test_report_unknown_branch(gel_server):
    proc = run_report(["--branch", "no_such_branch_zz"])
    assert proc.returncode == 3, (
        f"An unknown target branch must exit 3, got {proc.returncode}.\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"stdout must be the JSON object {{'error': 'branch_not_found'}}: {exc}\n"
            f"stdout={proc.stdout!r}"
        )
    assert payload == {"error": "branch_not_found"}, (
        f"stdout should be {{'error': 'branch_not_found'}}, got {payload}."
    )


def test_report_missing_dump(gel_server):
    stash = FRESH_DUMP + ".stash"
    shutil.move(FRESH_DUMP, stash)
    try:
        proc = run_report()
        assert proc.returncode == 2, (
            f"A missing {FRESH_DUMP} must exit 2, got {proc.returncode}.\n"
            f"stdout={proc.stdout}\nstderr={proc.stderr}"
        )
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise AssertionError(
                f"stdout must be the JSON object {{'error': 'dump_not_found'}}: {exc}\n"
                f"stdout={proc.stdout!r}"
            )
        assert payload == {"error": "dump_not_found"}, (
            f"stdout should be {{'error': 'dump_not_found'}}, got {payload}."
        )
    finally:
        shutil.move(stash, FRESH_DUMP)
    assert os.path.isfile(FRESH_DUMP), (
        f"The test failed to put {FRESH_DUMP} back in place."
    )


def test_report_roundtrip_mismatch_against_main(gel_server):
    proc = run_report(["--branch", "main"])
    assert proc.returncode == 4, (
        "Comparing the recovered dump against branch main must report a mismatch and "
        f"exit 4, got {proc.returncode}.\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"stdout of the mismatching run is not a single JSON object: {exc}\n"
            f"stdout={proc.stdout!r}"
        )
    assert list(report.keys()) == REPORT_KEYS, (
        f"The mismatch report's top-level keys must be exactly {REPORT_KEYS} in that "
        f"order, got {list(report.keys())}."
    )
    assert report["branch"] == "main", (
        f"report['branch'] should be 'main', got {report['branch']!r}."
    )
    assert report["roundtrip_ok"] is False, (
        f"report['roundtrip_ok'] should be false for branch main, got {report['roundtrip_ok']!r}."
    )
    names = branch_names()
    assert ROUNDTRIP_BRANCH not in names, (
        f"The throw-away branch {ROUNDTRIP_BRANCH!r} must be dropped even when the "
        f"round-trip comparison fails. Branches: {names}"
    )
