"""Final-state verification for the Gel set-operator reconciliation CLI task."""

import glob
import json
import os
import re
import subprocess
from contextlib import contextmanager

import pytest

PROJECT_DIR = "/home/user/reconcile"
ENTRYPOINT = os.path.join(PROJECT_DIR, "reconcile.sh")
QUERIES_DIR = os.path.join(PROJECT_DIR, "queries")
MIGRATIONS_DIR = os.path.join(PROJECT_DIR, "dbschema", "migrations")

FOREIGN_SUFFIXES = (".py", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl")
FOREIGN_RUNTIME_RE = re.compile(r"\b(python[0-9.]*|node|deno|bun|ruby|perl)\b", re.IGNORECASE)

WAREHOUSES = ["W-AMS", "W-BER", "W-CDG", "W-DUB"]
SKUS = [f"SKU-00{i}" for i in range(1, 8)]

SKU_ARRAY_FIELDS = (
    "counted_skus",
    "ledger_skus",
    "both",
    "shelf_only",
    "ledger_only",
    "all_skus",
    "unreconciled_skus",
)

BASELINE_BALANCE = {
    "W-AMS": {
        "shelf_units": 14,
        "ledger_units": 14,
        "counted_skus": ["SKU-001", "SKU-002", "SKU-003"],
        "ledger_skus": ["SKU-001", "SKU-002", "SKU-006"],
        "both": ["SKU-001", "SKU-002"],
        "shelf_only": ["SKU-003"],
        "ledger_only": ["SKU-006"],
        "all_skus": ["SKU-001", "SKU-002", "SKU-003", "SKU-006"],
        "unreconciled_skus": ["SKU-003", "SKU-006"],
        "is_balanced": False,
    },
    "W-BER": {
        "shelf_units": 8,
        "ledger_units": 8,
        "counted_skus": ["SKU-001", "SKU-004"],
        "ledger_skus": ["SKU-001", "SKU-004"],
        "both": ["SKU-001", "SKU-004"],
        "shelf_only": [],
        "ledger_only": [],
        "all_skus": ["SKU-001", "SKU-004"],
        "unreconciled_skus": [],
        "is_balanced": True,
    },
    "W-CDG": {
        "shelf_units": 9,
        "ledger_units": 4,
        "counted_skus": ["SKU-005"],
        "ledger_skus": ["SKU-005"],
        "both": ["SKU-005"],
        "shelf_only": [],
        "ledger_only": [],
        "all_skus": ["SKU-005"],
        "unreconciled_skus": [],
        "is_balanced": False,
    },
    "W-DUB": {
        "shelf_units": 0,
        "ledger_units": 0,
        "counted_skus": [],
        "ledger_skus": [],
        "both": [],
        "shelf_only": [],
        "ledger_only": [],
        "all_skus": [],
        "unreconciled_skus": [],
        "is_balanced": True,
    },
}

BASELINE_SKU = {
    "SKU-001": (True, None, ["W-AMS", "W-BER"], 12, 12),
    "SKU-002": (True, "W-AMS", ["W-AMS"], 7, 7),
    "SKU-003": (True, "W-AMS", ["W-AMS"], 2, 0),
    "SKU-004": (True, "W-BER", ["W-BER"], 1, 1),
    "SKU-005": (True, "W-CDG", ["W-CDG"], 9, 4),
    "SKU-006": (True, None, [], 0, 2),
    "SKU-007": (True, None, [], 0, 0),
}

BASELINE_DUPLICATE_PAIRS = [
    {"warehouse": "W-AMS", "sku": "SKU-002", "rows": 2, "quantities": [3, 4]},
    {"warehouse": "W-CDG", "sku": "SKU-005", "rows": 2, "quantities": [4, 5]},
]

BASELINE_MATRIX_CELLS = {
    ("W-AMS", "SKU-001"): (5, 5),
    ("W-AMS", "SKU-002"): (7, 7),
    ("W-AMS", "SKU-003"): (2, 0),
    ("W-AMS", "SKU-006"): (0, 2),
    ("W-BER", "SKU-001"): (7, 7),
    ("W-BER", "SKU-004"): (1, 1),
    ("W-CDG", "SKU-005"): (9, 4),
}

CLEANUP_QUERIES = [
    "delete ShelfCount filter .tag = 'mut'",
    "delete LedgerLine filter .tag = 'mut'",
    "delete Sku filter .code = 'SKU-900'",
    "delete Warehouse filter .code = 'W-ZZZ'",
]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _run(args, cwd, timeout=180):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)


@pytest.fixture(scope="session")
def gel_server():
    """Ensure the local Gel server is running before any CLI/DB interaction."""
    proc = _run(["gel-ctl", "start"], cwd="/tmp", timeout=300)
    assert proc.returncode == 0, (
        "`gel-ctl start` failed to start the local Gel server.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    ping = _run(["gel", "query", "-F", "json", "select 1"], cwd=PROJECT_DIR)
    assert ping.returncode == 0, (
        "The local Gel server did not answer a trivial query after `gel-ctl start`.\n"
        f"stdout: {ping.stdout}\nstderr: {ping.stderr}"
    )
    yield True
    for query in CLEANUP_QUERIES:
        _run(["gel", "query", query], cwd=PROJECT_DIR)


def gel_query(query):
    """Run an EdgeQL query through the Gel CLI and return the decoded JSON result."""
    proc = _run(["gel", "query", "-F", "json", query], cwd=PROJECT_DIR)
    assert proc.returncode == 0, (
        f"EdgeQL query failed unexpectedly: {query}\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    value = json.loads(proc.stdout)
    return value if isinstance(value, list) else [value]


def gel_exec(query):
    proc = _run(["gel", "query", query], cwd=PROJECT_DIR)
    assert proc.returncode == 0, (
        f"EdgeQL statement failed unexpectedly: {query}\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


def run_cli(*args, cwd="/tmp"):
    return _run(["bash", ENTRYPOINT, *args], cwd=cwd)


def cli_json(*args, cwd="/tmp"):
    proc = run_cli(*args, cwd=cwd)
    label = " ".join(args)
    assert proc.returncode == 0, (
        f"`reconcile.sh {label}` exited with {proc.returncode}, expected 0.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"`reconcile.sh {label}` did not print exactly one JSON document on stdout "
            f"({exc}).\nstdout: {proc.stdout!r}\nstderr: {proc.stderr}"
        ) from exc


@contextmanager
def mutations(*queries):
    """Apply temporary DML, then always undo it so later tests see the baseline."""
    try:
        for query in queries:
            gel_exec(query)
        yield
    finally:
        for query in CLEANUP_QUERIES:
            _run(["gel", "query", query], cwd=PROJECT_DIR)


def warehouse_ref(code):
    return f"assert_single((select Warehouse filter .code = '{code}'))"


def sku_ref(code):
    return f"assert_single((select Sku filter .code = '{code}'))"


def insert_shelf(warehouse, sku, quantity):
    return (
        "insert ShelfCount { "
        f"warehouse := {warehouse_ref(warehouse)}, "
        f"sku := {sku_ref(sku)}, "
        f"quantity := {quantity}, tag := 'mut' }}"
    )


def insert_ledger(warehouse, sku, quantity):
    return (
        "insert LedgerLine { "
        f"warehouse := {warehouse_ref(warehouse)}, "
        f"sku := {sku_ref(sku)}, "
        f"quantity := {quantity}, tag := 'mut' }}"
    )


def balance_map(payload):
    assert payload.get("report") == "balance", (
        f"`balance` must report \"report\": \"balance\", got {payload.get('report')!r}."
    )
    entries = payload.get("warehouses")
    assert isinstance(entries, list), (
        f"`balance` must contain a `warehouses` array, got {type(entries).__name__}."
    )
    return entries


def assert_warehouse_entry(entry, code, expected):
    assert entry.get("code") == code, f"Expected warehouse entry for {code}, got {entry!r}."
    for key, want in expected.items():
        got = entry.get(key)
        assert got == want, (
            f"Warehouse {code}: field `{key}` should be {want!r} but was {got!r}. "
            f"Full entry: {entry!r}"
        )


def sku_tuple(payload):
    return (
        payload.get("exists"),
        payload.get("sole_warehouse"),
        payload.get("shelf_warehouses"),
        payload.get("shelf_units"),
        payload.get("ledger_units"),
    )


def matrix_index(payload):
    assert payload.get("report") == "matrix", (
        f"`matrix` must report \"report\": \"matrix\", got {payload.get('report')!r}."
    )
    cells = payload.get("cells")
    assert isinstance(cells, list), (
        f"`matrix` must contain a `cells` array, got {type(cells).__name__}."
    )
    return cells, {(c.get("warehouse"), c.get("sku")): c for c in cells}


def pointer_map(type_name, gel_server_ready):
    rows = gel_query(
        "select schema::ObjectType { name, pointers: { name, cardinality, required, expr, "
        "target: { name } } } "
        f"filter .name = '{type_name}'"
    )
    assert len(rows) == 1, f"Expected exactly one schema::ObjectType named {type_name}."
    return {p["name"]: p for p in rows[0]["pointers"]}


# --------------------------------------------------------------------------- #
# A. project structure and runtime restriction
# --------------------------------------------------------------------------- #
def test_entrypoint_exists():
    assert os.path.isfile(ENTRYPOINT), f"Expected the CLI entrypoint at {ENTRYPOINT}."


def test_queries_directory_holds_edgeql_files():
    assert os.path.isdir(QUERIES_DIR), f"Expected the EdgeQL query directory {QUERIES_DIR}."
    files = sorted(glob.glob(os.path.join(QUERIES_DIR, "*.edgeql")))
    assert len(files) >= 4, (
        f"Expected at least four .edgeql files in {QUERIES_DIR}, found {len(files)}: {files}"
    )
    empty = [f for f in files if os.path.getsize(f) == 0]
    assert not empty, f"These .edgeql files are empty: {empty}"


def test_no_foreign_runtime_source_files_in_project():
    offenders = []
    for root, _dirs, files in os.walk(PROJECT_DIR):
        for name in files:
            if name.endswith(FOREIGN_SUFFIXES):
                offenders.append(os.path.join(root, name))
    assert not offenders, (
        "The solution must be shell + EdgeQL only, but these files were found under "
        f"{PROJECT_DIR}: {offenders}"
    )


def test_entrypoint_does_not_invoke_foreign_runtimes():
    with open(ENTRYPOINT) as handle:
        lines = handle.read().splitlines()
    offenders = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = FOREIGN_RUNTIME_RE.search(stripped)
        if match:
            offenders.append(stripped)
    assert not offenders, (
        "reconcile.sh must not invoke python/node/deno/bun/ruby/perl. Offending lines: "
        f"{offenders}"
    )


# --------------------------------------------------------------------------- #
# B. migration state
# --------------------------------------------------------------------------- #
def test_new_migration_file_created():
    files = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql")))
    assert len(files) >= 2, (
        "A new migration must have been created; expected at least two migration files in "
        f"{MIGRATIONS_DIR}, found {len(files)}: {files}"
    )


def test_migration_state_in_sync(gel_server):
    proc = _run(["gel", "migration", "status"], cwd=PROJECT_DIR)
    assert proc.returncode == 0, (
        "`gel migration status` reports the instance is not in sync with dbschema/.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    applied = gel_query("select count(schema::Migration)")[0]
    on_disk = len(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql")))
    assert applied == on_disk, (
        f"{on_disk} migration files exist on disk but {applied} migrations are applied to the "
        "instance; every created migration must be applied."
    )


# --------------------------------------------------------------------------- #
# C. schema computeds
# --------------------------------------------------------------------------- #
def test_warehouse_scalar_computeds_declared(gel_server):
    pointers = pointer_map("default::Warehouse", gel_server)
    for name, target in (
        ("shelf_units", "std::int64"),
        ("ledger_units", "std::int64"),
        ("is_balanced", "std::bool"),
    ):
        assert name in pointers, f"default::Warehouse is missing the computed `{name}`."
        p = pointers[name]
        assert p["cardinality"] == "One", (
            f"Warehouse.{name} must be declared `single` (cardinality 'One'), got "
            f"{p['cardinality']!r}."
        )
        assert p["required"] is True, (
            f"Warehouse.{name} must be declared `required`, introspection reports "
            f"required={p['required']!r}."
        )
        assert p["target"]["name"] == target, (
            f"Warehouse.{name} must have target type {target}, got {p['target']['name']}."
        )
        assert p["expr"], f"Warehouse.{name} must be a computed pointer (non-empty expr)."


def test_warehouse_multi_link_computeds_declared(gel_server):
    pointers = pointer_map("default::Warehouse", gel_server)
    for name in ("counted_skus", "ledger_skus", "unreconciled_skus"):
        assert name in pointers, f"default::Warehouse is missing the computed `{name}`."
        p = pointers[name]
        assert p["cardinality"] == "Many", (
            f"Warehouse.{name} must be declared `multi` (cardinality 'Many'), got "
            f"{p['cardinality']!r}."
        )
        assert p["target"]["name"] == "default::Sku", (
            f"Warehouse.{name} must link to default::Sku, got {p['target']['name']}."
        )
        assert p["expr"], f"Warehouse.{name} must be a computed pointer (non-empty expr)."


def test_sku_sole_warehouse_declared(gel_server):
    pointers = pointer_map("default::Sku", gel_server)
    assert "sole_warehouse" in pointers, "default::Sku is missing the computed `sole_warehouse`."
    p = pointers["sole_warehouse"]
    assert p["cardinality"] == "One", (
        "Sku.sole_warehouse must be declared `single` (cardinality 'One'), got "
        f"{p['cardinality']!r}."
    )
    assert p["required"] is not True, (
        "Sku.sole_warehouse must not be required; it is empty for SKUs stocked in zero or "
        "several warehouses."
    )
    assert p["target"]["name"] == "default::Warehouse", (
        f"Sku.sole_warehouse must link to default::Warehouse, got {p['target']['name']}."
    )
    assert p["expr"], "Sku.sole_warehouse must be a computed pointer (non-empty expr)."


def test_computeds_are_readable_for_every_object(gel_server):
    warehouses = gel_query(
        "select Warehouse { code, shelf_units, ledger_units, counted_skus: {code}, "
        "ledger_skus: {code}, unreconciled_skus: {code}, is_balanced } order by .code"
    )
    assert len(warehouses) == 4, f"Expected 4 Warehouse objects, got {len(warehouses)}."
    skus = gel_query("select Sku { code, sole_warehouse: {code} } order by .code")
    assert len(skus) == 7, f"Expected 7 Sku objects, got {len(skus)}."
    sole = {row["code"]: (row["sole_warehouse"] or {}).get("code") for row in skus}
    assert sole["SKU-001"] is None, (
        "SKU-001 is shelf-counted in two warehouses, so sole_warehouse must be empty, got "
        f"{sole['SKU-001']!r}."
    )
    assert sole["SKU-002"] == "W-AMS", (
        "SKU-002 has two shelf rows in the single warehouse W-AMS, so sole_warehouse must be "
        f"W-AMS, got {sole['SKU-002']!r}."
    )
    assert sole["SKU-006"] is None, "SKU-006 has no shelf rows, so sole_warehouse must be empty."
    assert sole["SKU-007"] is None, "SKU-007 has no rows at all, so sole_warehouse must be empty."


def test_warehouse_computed_values(gel_server):
    rows = gel_query(
        "select Warehouse { code, shelf_units, ledger_units, is_balanced } order by .code"
    )
    actual = [(r["code"], r["shelf_units"], r["ledger_units"], r["is_balanced"]) for r in rows]
    expected = [
        ("W-AMS", 14, 14, False),
        ("W-BER", 8, 8, True),
        ("W-CDG", 9, 4, False),
        ("W-DUB", 0, 0, True),
    ]
    assert actual == expected, f"Unexpected computed values per warehouse: {actual}"


# --------------------------------------------------------------------------- #
# D. balance report
# --------------------------------------------------------------------------- #
def test_balance_report_baseline(gel_server):
    entries = balance_map(cli_json("balance"))
    assert [e.get("code") for e in entries] == WAREHOUSES, (
        "`balance` must list every warehouse ordered by code ascending, got "
        f"{[e.get('code') for e in entries]}."
    )
    for entry in entries:
        assert_warehouse_entry(entry, entry["code"], BASELINE_BALANCE[entry["code"]])


def test_balance_sku_arrays_are_distinct(gel_server):
    for entry in balance_map(cli_json("balance")):
        for field in SKU_ARRAY_FIELDS:
            values = entry[field]
            assert len(values) == len(set(values)), (
                f"Warehouse {entry['code']}: `{field}` must contain distinct SKU codes, got "
                f"{values!r}."
            )


# --------------------------------------------------------------------------- #
# E. sku report
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("code", SKUS)
def test_sku_report_baseline(gel_server, code):
    payload = cli_json("sku", code)
    assert payload.get("report") == "sku", f"`sku` must report \"report\": \"sku\" for {code}."
    assert payload.get("code") == code, f"`sku {code}` must echo the code, got {payload!r}."
    assert sku_tuple(payload) == BASELINE_SKU[code], (
        f"`sku {code}` returned {sku_tuple(payload)}, expected {BASELINE_SKU[code]}."
    )


def test_sku_report_unknown_code_without_strict(gel_server):
    payload = cli_json("sku", "SKU-999")
    assert payload.get("code") == "SKU-999", f"`sku SKU-999` must echo the code, got {payload!r}."
    assert sku_tuple(payload) == (False, None, [], 0, 0), (
        f"`sku SKU-999` must report a non-existent SKU as empty, got {payload!r}."
    )


def test_sku_report_unknown_code_with_strict_fails(gel_server):
    proc = run_cli("sku", "SKU-999", "--strict")
    assert proc.returncode == 3, (
        f"`sku SKU-999 --strict` must exit 3, got {proc.returncode}.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert proc.stdout.strip() == "", (
        f"`sku SKU-999 --strict` must print nothing on stdout, got {proc.stdout!r}."
    )
    assert "error: sku not found: SKU-999" in proc.stderr, (
        f"`sku SKU-999 --strict` must report the marker on stderr, got {proc.stderr!r}."
    )


def test_sku_report_known_code_with_strict_succeeds(gel_server):
    payload = cli_json("sku", "SKU-005", "--strict")
    assert sku_tuple(payload) == BASELINE_SKU["SKU-005"], (
        f"`sku SKU-005 --strict` returned {sku_tuple(payload)}, expected "
        f"{BASELINE_SKU['SKU-005']}."
    )


# --------------------------------------------------------------------------- #
# F. duplicates report
# --------------------------------------------------------------------------- #
def test_duplicates_report_baseline(gel_server):
    payload = cli_json("duplicates")
    assert payload.get("report") == "duplicates", (
        f"`duplicates` must report \"report\": \"duplicates\", got {payload.get('report')!r}."
    )
    assert payload.get("clean") is False, (
        f"`duplicates` must report clean=false for the seeded data, got {payload.get('clean')!r}."
    )
    assert payload.get("pairs") == BASELINE_DUPLICATE_PAIRS, (
        f"`duplicates` returned {payload.get('pairs')!r}, expected {BASELINE_DUPLICATE_PAIRS!r}."
    )


def test_duplicates_assert_fails_when_duplicates_exist(gel_server):
    proc = run_cli("duplicates", "--assert")
    assert proc.returncode == 4, (
        f"`duplicates --assert` must exit 4 while duplicates exist, got {proc.returncode}.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert proc.stdout.strip() == "", (
        f"`duplicates --assert` must print nothing on stdout when it fails, got {proc.stdout!r}."
    )
    assert "error: duplicate shelf counts" in proc.stderr, (
        f"`duplicates --assert` must report the marker on stderr, got {proc.stderr!r}."
    )


def test_duplicates_clean_state(gel_server):
    delete_query = (
        "delete ShelfCount filter "
        "(.warehouse.code = 'W-AMS' and .sku.code = 'SKU-002' and .quantity = 4) "
        "or (.warehouse.code = 'W-CDG' and .sku.code = 'SKU-005' and .quantity = 5)"
    )
    restore = [
        "insert ShelfCount { warehouse := "
        + warehouse_ref("W-AMS")
        + ", sku := "
        + sku_ref("SKU-002")
        + ", quantity := 4, tag := 'baseline' }",
        "insert ShelfCount { warehouse := "
        + warehouse_ref("W-CDG")
        + ", sku := "
        + sku_ref("SKU-005")
        + ", quantity := 5, tag := 'baseline' }",
    ]
    try:
        gel_exec(delete_query)
        payload = cli_json("duplicates")
        assert payload.get("clean") is True, (
            f"With no duplicate pairs left, `duplicates` must report clean=true, got {payload!r}."
        )
        assert payload.get("pairs") == [], (
            f"With no duplicate pairs left, `pairs` must be empty, got {payload.get('pairs')!r}."
        )
        strict = run_cli("duplicates", "--assert")
        assert strict.returncode == 0, (
            "`duplicates --assert` must exit 0 when there are no duplicates, got "
            f"{strict.returncode}.\nstdout: {strict.stdout}\nstderr: {strict.stderr}"
        )
        assert json.loads(strict.stdout).get("pairs") == [], (
            "`duplicates --assert` must print the same JSON as without the flag when clean."
        )
    finally:
        for query in restore:
            _run(["gel", "query", query], cwd=PROJECT_DIR)
    restored = cli_json("duplicates")
    assert restored.get("pairs") == BASELINE_DUPLICATE_PAIRS, (
        f"Baseline duplicates were not restored correctly: {restored.get('pairs')!r}."
    )


# --------------------------------------------------------------------------- #
# G. matrix report
# --------------------------------------------------------------------------- #
def test_matrix_report_baseline(gel_server):
    payload = cli_json("matrix")
    cells, index = matrix_index(payload)
    assert len(cells) == 28, (
        f"`matrix` must emit the full 4x7 cross product (28 cells), got {len(cells)}."
    )
    expected_order = [(w, s) for w in WAREHOUSES for s in SKUS]
    actual_order = [(c.get("warehouse"), c.get("sku")) for c in cells]
    assert actual_order == expected_order, (
        f"`matrix` cells must be ordered by warehouse then sku, got {actual_order}."
    )
    assert payload.get("total_delta") == 5, (
        f"`matrix` total_delta must be 5, got {payload.get('total_delta')!r}."
    )
    for key, cell in index.items():
        shelf, ledger = BASELINE_MATRIX_CELLS.get(key, (0, 0))
        assert (cell["shelf"], cell["ledger"], cell["delta"]) == (shelf, ledger, shelf - ledger), (
            f"`matrix` cell {key} should be shelf={shelf}, ledger={ledger}, "
            f"delta={shelf - ledger}, got {cell!r}."
        )


def test_matrix_report_filtered_by_sku(gel_server):
    payload = cli_json("matrix", "--sku", "SKU-005")
    cells, index = matrix_index(payload)
    assert [c["warehouse"] for c in cells] == WAREHOUSES, (
        f"`matrix --sku SKU-005` must emit one cell per warehouse in order, got {cells!r}."
    )
    assert all(c["sku"] == "SKU-005" for c in cells), (
        f"`matrix --sku SKU-005` must only emit SKU-005 cells, got {cells!r}."
    )
    assert (index[("W-CDG", "SKU-005")]["shelf"], index[("W-CDG", "SKU-005")]["ledger"]) == (9, 4), (
        f"`matrix --sku SKU-005` W-CDG cell should be 9/4, got {index[('W-CDG', 'SKU-005')]!r}."
    )
    assert index[("W-CDG", "SKU-005")]["delta"] == 5, "W-CDG delta for SKU-005 must be 5."
    for code in ("W-AMS", "W-BER", "W-DUB"):
        cell = index[(code, "SKU-005")]
        assert (cell["shelf"], cell["ledger"], cell["delta"]) == (0, 0, 0), (
            f"`matrix --sku SKU-005` cell for {code} should be all zeros, got {cell!r}."
        )
    assert payload.get("total_delta") == 5, (
        f"`matrix --sku SKU-005` total_delta must be 5, got {payload.get('total_delta')!r}."
    )


def test_matrix_report_unknown_sku(gel_server):
    payload = cli_json("matrix", "--sku", "SKU-999")
    assert payload.get("cells") == [], (
        f"`matrix --sku SKU-999` must emit no cells, got {payload.get('cells')!r}."
    )
    assert payload.get("total_delta") == 0, (
        f"`matrix --sku SKU-999` total_delta must be 0, got {payload.get('total_delta')!r}."
    )


# --------------------------------------------------------------------------- #
# H. argument errors
# --------------------------------------------------------------------------- #
def test_unknown_report_name(gel_server):
    proc = run_cli("totals")
    assert proc.returncode == 2, (
        f"An unknown report must exit 2, got {proc.returncode}.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert proc.stdout.strip() == "", f"stdout must stay empty, got {proc.stdout!r}."
    assert "error: unknown report: totals" in proc.stderr, (
        f"Expected the unknown-report marker on stderr, got {proc.stderr!r}."
    )


def test_sku_report_without_code(gel_server):
    proc = run_cli("sku")
    assert proc.returncode == 2, (
        f"`sku` without a code must exit 2, got {proc.returncode}.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert proc.stdout.strip() == "", f"stdout must stay empty, got {proc.stdout!r}."
    assert "error: missing sku code" in proc.stderr, (
        f"Expected the missing-argument marker on stderr, got {proc.stderr!r}."
    )


# --------------------------------------------------------------------------- #
# I. live-data checks (anti hard-coding)
# --------------------------------------------------------------------------- #
def test_new_shelf_row_changes_every_report(gel_server):
    with mutations(insert_shelf("W-DUB", "SKU-007", 4)):
        entries = {e["code"]: e for e in balance_map(cli_json("balance"))}
        assert_warehouse_entry(
            entries["W-DUB"],
            "W-DUB",
            {
                "shelf_units": 4,
                "ledger_units": 0,
                "counted_skus": ["SKU-007"],
                "ledger_skus": [],
                "both": [],
                "shelf_only": ["SKU-007"],
                "ledger_only": [],
                "all_skus": ["SKU-007"],
                "unreconciled_skus": ["SKU-007"],
                "is_balanced": False,
            },
        )
        payload = cli_json("matrix")
        _cells, index = matrix_index(payload)
        assert payload["total_delta"] == 9, (
            f"After adding 4 shelf units, matrix total_delta must be 9, got {payload['total_delta']}."
        )
        cell = index[("W-DUB", "SKU-007")]
        assert (cell["shelf"], cell["ledger"], cell["delta"]) == (4, 0, 4), (
            f"Cell (W-DUB, SKU-007) should be 4/0/4, got {cell!r}."
        )
        sku = cli_json("sku", "SKU-007")
        assert sku_tuple(sku) == (True, "W-DUB", ["W-DUB"], 4, 0), (
            f"`sku SKU-007` should follow the new row, got {sku_tuple(sku)}."
        )
    entries = {e["code"]: e for e in balance_map(cli_json("balance"))}
    assert_warehouse_entry(entries["W-DUB"], "W-DUB", BASELINE_BALANCE["W-DUB"])


def test_second_warehouse_invalidates_sole_warehouse(gel_server):
    with mutations(insert_shelf("W-BER", "SKU-003", 2)):
        sku = cli_json("sku", "SKU-003")
        assert sku_tuple(sku) == (True, None, ["W-AMS", "W-BER"], 4, 0), (
            "Once SKU-003 is counted in two warehouses, sole_warehouse must become null; got "
            f"{sku_tuple(sku)}."
        )
        entries = {e["code"]: e for e in balance_map(cli_json("balance"))}
        assert_warehouse_entry(
            entries["W-BER"],
            "W-BER",
            {
                "shelf_units": 10,
                "ledger_units": 8,
                "counted_skus": ["SKU-001", "SKU-003", "SKU-004"],
                "ledger_skus": ["SKU-001", "SKU-004"],
                "both": ["SKU-001", "SKU-004"],
                "shelf_only": ["SKU-003"],
                "ledger_only": [],
                "all_skus": ["SKU-001", "SKU-003", "SKU-004"],
                "unreconciled_skus": ["SKU-003"],
                "is_balanced": False,
            },
        )
    assert sku_tuple(cli_json("sku", "SKU-003")) == BASELINE_SKU["SKU-003"], (
        "SKU-003 did not return to its baseline state after cleanup."
    )
    entries = {e["code"]: e for e in balance_map(cli_json("balance"))}
    assert_warehouse_entry(entries["W-BER"], "W-BER", BASELINE_BALANCE["W-BER"])


def test_new_duplicate_pair_is_detected(gel_server):
    with mutations(insert_shelf("W-BER", "SKU-001", 1)):
        payload = cli_json("duplicates")
        assert payload.get("clean") is False, "A new duplicate pair must make clean=false."
        assert payload.get("pairs") == [
            {"warehouse": "W-AMS", "sku": "SKU-002", "rows": 2, "quantities": [3, 4]},
            {"warehouse": "W-BER", "sku": "SKU-001", "rows": 2, "quantities": [1, 7]},
            {"warehouse": "W-CDG", "sku": "SKU-005", "rows": 2, "quantities": [4, 5]},
        ], f"Unexpected duplicate pairs after the mutation: {payload.get('pairs')!r}"
        strict = run_cli("duplicates", "--assert")
        assert strict.returncode == 4, (
            f"`duplicates --assert` must still exit 4, got {strict.returncode}."
        )
    assert cli_json("duplicates").get("pairs") == BASELINE_DUPLICATE_PAIRS, (
        "Baseline duplicates were not restored after cleanup."
    )


def test_new_sku_expands_matrix(gel_server):
    with mutations("insert Sku { code := 'SKU-900', label := 'probe' }"):
        payload = cli_json("matrix")
        cells, index = matrix_index(payload)
        assert len(cells) == 32, (
            f"With an eighth SKU the matrix must have 4x8=32 cells, got {len(cells)}."
        )
        assert payload["total_delta"] == 5, (
            f"A SKU with no rows must not change total_delta, got {payload['total_delta']}."
        )
        cell = index[("W-AMS", "SKU-900")]
        assert (cell["shelf"], cell["ledger"], cell["delta"]) == (0, 0, 0), (
            f"Cell (W-AMS, SKU-900) should be all zeros, got {cell!r}."
        )
        assert sku_tuple(cli_json("sku", "SKU-900")) == (True, None, [], 0, 0), (
            "`sku SKU-900` should report an existing but empty SKU."
        )
        entries = {e["code"]: e for e in balance_map(cli_json("balance"))}
        for code in WAREHOUSES:
            assert_warehouse_entry(entries[code], code, BASELINE_BALANCE[code])
    cells, _index = matrix_index(cli_json("matrix"))
    assert len(cells) == 28, f"The matrix must shrink back to 28 cells, got {len(cells)}."


def test_new_warehouse_expands_balance(gel_server):
    with mutations("insert Warehouse { code := 'W-ZZZ', region := 'probe' }"):
        entries = balance_map(cli_json("balance"))
        assert [e["code"] for e in entries] == WAREHOUSES + ["W-ZZZ"], (
            f"`balance` must include the new warehouse last, got {[e['code'] for e in entries]}."
        )
        assert_warehouse_entry(
            entries[-1],
            "W-ZZZ",
            {
                "shelf_units": 0,
                "ledger_units": 0,
                "counted_skus": [],
                "ledger_skus": [],
                "both": [],
                "shelf_only": [],
                "ledger_only": [],
                "all_skus": [],
                "unreconciled_skus": [],
                "is_balanced": True,
            },
        )
        payload = cli_json("matrix")
        cells, _index = matrix_index(payload)
        assert len(cells) == 35, (
            f"With a fifth warehouse the matrix must have 5x7=35 cells, got {len(cells)}."
        )
        assert payload["total_delta"] == 5, (
            f"An empty warehouse must not change total_delta, got {payload['total_delta']}."
        )
    assert len(balance_map(cli_json("balance"))) == 4, (
        "`balance` must be back to 4 warehouses after cleanup."
    )


def test_new_ledger_row_changes_every_report(gel_server):
    with mutations(insert_ledger("W-DUB", "SKU-006", 6)):
        entries = {e["code"]: e for e in balance_map(cli_json("balance"))}
        assert_warehouse_entry(
            entries["W-DUB"],
            "W-DUB",
            {
                "shelf_units": 0,
                "ledger_units": 6,
                "counted_skus": [],
                "ledger_skus": ["SKU-006"],
                "both": [],
                "shelf_only": [],
                "ledger_only": ["SKU-006"],
                "all_skus": ["SKU-006"],
                "unreconciled_skus": ["SKU-006"],
                "is_balanced": False,
            },
        )
        payload = cli_json("matrix")
        _cells, index = matrix_index(payload)
        assert payload["total_delta"] == -1, (
            f"After adding 6 ledger units, total_delta must be -1, got {payload['total_delta']}."
        )
        cell = index[("W-DUB", "SKU-006")]
        assert (cell["shelf"], cell["ledger"], cell["delta"]) == (0, 6, -6), (
            f"Cell (W-DUB, SKU-006) should be 0/6/-6, got {cell!r}."
        )
        sku = cli_json("sku", "SKU-006")
        assert sku_tuple(sku) == (True, None, [], 0, 8), (
            f"`sku SKU-006` should report ledger_units=8, got {sku_tuple(sku)}."
        )
    entries = {e["code"]: e for e in balance_map(cli_json("balance"))}
    assert_warehouse_entry(entries["W-DUB"], "W-DUB", BASELINE_BALANCE["W-DUB"])
    assert cli_json("matrix")["total_delta"] == 5, (
        "matrix total_delta must return to 5 after cleanup."
    )
    assert sku_tuple(cli_json("sku", "SKU-006")) == BASELINE_SKU["SKU-006"], (
        "SKU-006 did not return to its baseline state after cleanup."
    )
