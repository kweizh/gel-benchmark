import glob
import json
import os
import shutil
import subprocess

import pytest

PROJECT_DIR = "/home/user/reconcile"
SCHEMA_FILE = os.path.join(PROJECT_DIR, "dbschema", "default.gel")
MIGRATIONS_DIR = os.path.join(PROJECT_DIR, "dbschema", "migrations")


def _run(args, cwd=PROJECT_DIR, timeout=120):
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.fixture(scope="session")
def gel_server():
    """Start the local Gel server (idempotent) and make sure it answers queries."""
    proc = _run(["gel-ctl", "start"], cwd="/tmp", timeout=300)
    assert proc.returncode == 0, (
        "`gel-ctl start` failed to start the local Gel server.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    ping = _run(["gel", "query", "-F", "json", "select 1"])
    assert ping.returncode == 0, (
        "The local Gel server did not answer a trivial query after `gel-ctl start`.\n"
        f"stdout: {ping.stdout}\nstderr: {ping.stderr}"
    )
    return True


def _query_json(query):
    proc = _run(["gel", "query", "-F", "json", query])
    assert proc.returncode == 0, (
        f"EdgeQL query failed: {query}\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    return json.loads(proc.stdout)


def test_gel_cli_available():
    assert shutil.which("gel") is not None, "The `gel` CLI was not found in PATH."


def test_gel_server_control_script_available():
    assert shutil.which("gel-ctl") is not None, (
        "The `gel-ctl` helper used to start the local Gel server was not found in PATH."
    )


def test_project_directory_exists():
    assert os.path.isdir(PROJECT_DIR), f"Project directory {PROJECT_DIR} does not exist."


def test_gel_toml_exists():
    gel_toml = os.path.join(PROJECT_DIR, "gel.toml")
    assert os.path.isfile(gel_toml), f"{gel_toml} does not exist."


def test_schema_file_exists_with_seed_types():
    assert os.path.isfile(SCHEMA_FILE), f"{SCHEMA_FILE} does not exist."
    with open(SCHEMA_FILE) as handle:
        content = handle.read()
    for type_name in ("Warehouse", "Sku", "ShelfCount", "LedgerLine"):
        assert f"type {type_name}" in content, (
            f"Object type `{type_name}` is missing from {SCHEMA_FILE}."
        )


def test_initial_migration_exists():
    assert os.path.isdir(MIGRATIONS_DIR), f"{MIGRATIONS_DIR} does not exist."
    files = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql")))
    assert len(files) >= 1, (
        f"Expected at least one migration file in {MIGRATIONS_DIR}, found {files}."
    )


def test_solution_entrypoint_not_present_yet():
    entrypoint = os.path.join(PROJECT_DIR, "reconcile.sh")
    assert not os.path.exists(entrypoint), (
        f"{entrypoint} already exists; the executor is expected to create it."
    )


def test_migration_status_is_in_sync(gel_server):
    proc = _run(["gel", "migration", "status"])
    assert proc.returncode == 0, (
        "`gel migration status` failed in the initial environment.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


def test_seed_warehouses(gel_server):
    codes = _query_json("select Warehouse.code order by Warehouse.code")
    assert codes == ["W-AMS", "W-BER", "W-CDG", "W-DUB"], (
        f"Unexpected seeded Warehouse codes: {codes}"
    )


def test_seed_skus(gel_server):
    codes = _query_json("select Sku.code order by Sku.code")
    expected = [f"SKU-00{i}" for i in range(1, 8)]
    assert codes == expected, f"Unexpected seeded Sku codes: {codes}"


def test_seed_shelf_counts(gel_server):
    rows = _query_json(
        "select ShelfCount { w := .warehouse.code, s := .sku.code, q := .quantity } "
        "order by .warehouse.code then .sku.code then .quantity"
    )
    actual = [(r["w"], r["s"], r["q"]) for r in rows]
    expected = [
        ("W-AMS", "SKU-001", 5),
        ("W-AMS", "SKU-002", 3),
        ("W-AMS", "SKU-002", 4),
        ("W-AMS", "SKU-003", 2),
        ("W-BER", "SKU-001", 7),
        ("W-BER", "SKU-004", 1),
        ("W-CDG", "SKU-005", 4),
        ("W-CDG", "SKU-005", 5),
    ]
    assert actual == expected, f"Unexpected seeded ShelfCount rows: {actual}"


def test_seed_ledger_lines(gel_server):
    rows = _query_json(
        "select LedgerLine { w := .warehouse.code, s := .sku.code, q := .quantity } "
        "order by .warehouse.code then .sku.code then .quantity"
    )
    actual = [(r["w"], r["s"], r["q"]) for r in rows]
    expected = [
        ("W-AMS", "SKU-001", 5),
        ("W-AMS", "SKU-002", 7),
        ("W-AMS", "SKU-006", 2),
        ("W-BER", "SKU-001", 7),
        ("W-BER", "SKU-004", 1),
        ("W-CDG", "SKU-005", 4),
    ]
    assert actual == expected, f"Unexpected seeded LedgerLine rows: {actual}"


def test_required_computeds_not_present_yet(gel_server):
    rows = _query_json(
        "select schema::ObjectType { name, pointers: { name } } "
        "filter .name in {'default::Warehouse', 'default::Sku'}"
    )
    existing = {
        row["name"]: {p["name"] for p in row["pointers"]} for row in rows
    }
    assert "default::Warehouse" in existing, "default::Warehouse is missing from the schema."
    assert "default::Sku" in existing, "default::Sku is missing from the schema."
    for name in (
        "shelf_units",
        "ledger_units",
        "counted_skus",
        "ledger_skus",
        "unreconciled_skus",
        "is_balanced",
    ):
        assert name not in existing["default::Warehouse"], (
            f"Computed `{name}` already exists on default::Warehouse; "
            "the executor is expected to add it."
        )
    assert "sole_warehouse" not in existing["default::Sku"], (
        "Computed `sole_warehouse` already exists on default::Sku; "
        "the executor is expected to add it."
    )
