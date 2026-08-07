import glob
import json
import os
import shutil
import subprocess

import pytest

PROJECT_DIR = "/home/user/recovery"
BACKUPS_DIR = os.path.join(PROJECT_DIR, "backups")
SOURCE_DUMP = os.path.join(BACKUPS_DIR, "pre_incident.dump")
FRESH_DUMP = os.path.join(BACKUPS_DIR, "recovered.dump")
SCRIPTS_DIR = os.path.join(PROJECT_DIR, "scripts")
REPORT_SCRIPT = os.path.join(SCRIPTS_DIR, "verify_recovery.sh")


def run(cmd, timeout=180):
    return subprocess.run(
        cmd,
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.fixture(scope="session")
def gel_server():
    """Make sure the local Gel server accepts connections before any DB check."""
    proc = subprocess.run(
        ["gel-start"], capture_output=True, text=True, timeout=600
    )
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


def query_json(query, branch="main"):
    proc = run(["gel", "query", "-F", "json", "--branch", branch, query])
    assert proc.returncode == 0, (
        f"Query {query!r} on branch {branch!r} failed:\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    return json.loads(proc.stdout)


def test_gel_cli_available():
    assert shutil.which("gel") is not None, "The `gel` CLI was not found in PATH."


def test_gel_start_helper_available():
    assert shutil.which("gel-start") is not None, (
        "The `gel-start` helper used to boot the local Gel server was not found in PATH."
    )


def test_project_directory_exists():
    assert os.path.isdir(PROJECT_DIR), f"Project directory {PROJECT_DIR} does not exist."


def test_project_manifest_exists():
    manifest = os.path.join(PROJECT_DIR, "gel.toml")
    assert os.path.isfile(manifest), f"Gel project manifest {manifest} does not exist."


def test_schema_file_exists():
    schema = os.path.join(PROJECT_DIR, "dbschema", "default.gel")
    assert os.path.isfile(schema), f"Schema file {schema} does not exist."


def test_initial_schema_is_loose():
    schema = os.path.join(PROJECT_DIR, "dbschema", "default.gel")
    with open(schema, encoding="utf-8") as handle:
        content = handle.read()
    assert "type Warehouse" in content, "Warehouse type missing from dbschema/default.gel."
    assert "type Shipment" in content, "Shipment type missing from dbschema/default.gel."
    assert "regexp" not in content, (
        "dbschema/default.gel already contains a regexp constraint; the starting schema "
        "must still be the loose one."
    )
    assert "one_of" not in content, (
        "dbschema/default.gel already contains a one_of constraint; the starting schema "
        "must still be the loose one."
    )


def test_single_initial_migration_file():
    migrations = sorted(
        glob.glob(os.path.join(PROJECT_DIR, "dbschema", "migrations", "*.edgeql"))
    )
    assert len(migrations) == 1, (
        "Expected exactly one pre-existing migration file in dbschema/migrations, "
        f"found: {migrations}"
    )


def test_source_dump_present():
    assert os.path.isfile(SOURCE_DUMP), f"Pre-incident backup {SOURCE_DUMP} does not exist."
    assert os.path.getsize(SOURCE_DUMP) > 0, f"Pre-incident backup {SOURCE_DUMP} is empty."


def test_fresh_dump_not_created_yet():
    assert not os.path.exists(FRESH_DUMP), (
        f"{FRESH_DUMP} already exists; the task must not be pre-solved."
    )


def test_report_script_not_created_yet():
    assert not os.path.exists(REPORT_SCRIPT), (
        f"{REPORT_SCRIPT} already exists; the task must not be pre-solved."
    )


def test_main_branch_exists_and_recovered_does_not(gel_server):
    branches = query_json("select sys::Branch.name")
    assert "main" in branches, f"Branch 'main' is missing, found: {branches}"
    assert "recovered" not in branches, (
        f"Branch 'recovered' already exists; the task must not be pre-solved. Found: {branches}"
    )
    assert "verify_roundtrip" not in branches, (
        f"Branch 'verify_roundtrip' already exists before the task starts. Found: {branches}"
    )


def test_main_branch_holds_damaged_data(gel_server):
    warehouses = query_json("select count(Warehouse)")[0]
    shipments = query_json("select count(Shipment)")[0]
    assert warehouses == 4, f"Expected 4 warehouses on branch main, found {warehouses}."
    assert shipments == 24, f"Expected 24 shipments on branch main, found {shipments}."


def test_main_branch_has_cleared_origin_links(gel_server):
    orphans = query_json("select count((select Shipment filter not exists .origin))")[0]
    assert orphans > 0, (
        "Branch main was expected to contain shipments whose origin link was cleared by "
        "the botched maintenance script."
    )


def test_main_branch_has_single_migration(gel_server):
    migrations = query_json("select count(schema::Migration)")[0]
    assert migrations == 1, (
        f"Expected exactly one migration in branch main's history, found {migrations}."
    )
