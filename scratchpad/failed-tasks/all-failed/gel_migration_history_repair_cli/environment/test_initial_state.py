"""Initial-state verification for the gel_migration_history_repair_cli task.

Checks that the baked image really ships a *drifted* Gel project:
  * one single migration file on disk while the database records three revisions,
  * one of those revisions was produced by bare DDL,
  * the live schema contains objects that the SDL on disk does not describe,
  * the seeded application data is present.
"""

import glob
import json
import os
import re
import shutil
import subprocess
import time

import pytest

PROJECT_DIR = "/home/user/inventory"
SCHEMA_DIR = os.path.join(PROJECT_DIR, "dbschema")
MIGRATIONS_DIR = os.path.join(SCHEMA_DIR, "migrations")
REPORT_PATH = os.path.join(PROJECT_DIR, "repair_report.json")
SNAPSHOT_PATH = "/opt/harbor/initial_state.json"
START_SCRIPT = "/usr/local/bin/gel-start.sh"

MIGRATION_HEADER_RE = re.compile(r"CREATE\s+MIGRATION\s+([A-Za-z0-9_]+)", re.IGNORECASE)


def _run_gel(args, branch="main", timeout=180):
    """Run the gel CLI inside the project directory."""
    env = dict(os.environ)
    env["GEL_BRANCH"] = branch
    env.setdefault("GEL_HOST", "127.0.0.1")
    env.setdefault("GEL_PORT", "5656")
    env.setdefault("GEL_USER", "admin")
    env.setdefault("GEL_CLIENT_TLS_SECURITY", "insecure")
    return subprocess.run(
        ["gel"] + args,
        cwd=PROJECT_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _query_json(query, branch="main"):
    proc = _run_gel(["query", "-F", "json", query], branch=branch)
    assert proc.returncode == 0, (
        f"EdgeQL query failed ({proc.returncode}): {query}\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    return json.loads(proc.stdout)


def _server_ready():
    try:
        proc = _run_gel(["query", "-F", "json", "select 1"], timeout=60)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0


@pytest.fixture(scope="session")
def gel_server():
    """Guarantee that the local Gel server is up before any CLI-based check."""
    if not _server_ready():
        if os.path.isfile(START_SCRIPT):
            subprocess.run(
                [START_SCRIPT], capture_output=True, text=True, timeout=180, check=False
            )
        deadline = time.time() + 180
        while time.time() < deadline:
            if _server_ready():
                break
            time.sleep(3)
        else:
            pytest.fail("Local Gel server never became reachable on 127.0.0.1:5656.")
    return True


@pytest.fixture(scope="session")
def snapshot():
    assert os.path.isfile(SNAPSHOT_PATH), f"Missing baked snapshot {SNAPSHOT_PATH}."
    with open(SNAPSHOT_PATH) as fh:
        return json.load(fh)


def _fs_revisions():
    files = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql")))
    revisions = []
    for path in files:
        with open(path) as fh:
            match = MIGRATION_HEADER_RE.search(fh.read())
        assert match is not None, f"No CREATE MIGRATION header found in {path}."
        revisions.append(match.group(1))
    return files, revisions


def test_gel_cli_available():
    assert shutil.which("gel") is not None, "The `gel` CLI is not available on PATH."


def test_start_script_available():
    assert os.path.isfile(START_SCRIPT), f"Missing helper script {START_SCRIPT}."
    assert os.access(START_SCRIPT, os.X_OK), f"{START_SCRIPT} is not executable."


def test_project_layout_exists():
    assert os.path.isdir(PROJECT_DIR), f"Project directory {PROJECT_DIR} is missing."
    assert os.path.isfile(
        os.path.join(PROJECT_DIR, "gel.toml")
    ), "Project marker gel.toml is missing."
    assert os.path.isfile(
        os.path.join(SCHEMA_DIR, "default.gel")
    ), "Schema file dbschema/default.gel is missing."
    assert os.path.isdir(MIGRATIONS_DIR), "dbschema/migrations directory is missing."


def test_only_one_migration_file_on_disk():
    files, revisions = _fs_revisions()
    assert len(files) == 1, (
        "Expected the drifted project to ship exactly one migration file, "
        f"found {len(files)}: {files}"
    )
    assert os.path.basename(files[0]).startswith(
        "00001"
    ), f"The single migration file should be the first revision, got {files[0]}."
    assert len(revisions) == 1, "Could not parse the revision id from the migration file."


def test_report_file_not_present_yet():
    assert not os.path.exists(
        REPORT_PATH
    ), f"{REPORT_PATH} must be produced by the executor, not baked in."


def test_sdl_does_not_describe_out_of_band_objects():
    sdl = ""
    for path in sorted(glob.glob(os.path.join(SCHEMA_DIR, "*.gel"))):
        with open(path) as fh:
            sdl += fh.read()
    assert sdl.strip(), "dbschema contains no SDL."
    for expected in ("Warehouse", "Part", "StockLevel"):
        assert expected in sdl, f"SDL should already describe {expected}."
    for unexpected in ("AuditEvent", "ReorderRule"):
        assert (
            unexpected not in sdl
        ), f"SDL must not describe {unexpected} in the initial (drifted) state."


def test_snapshot_has_expected_sections(snapshot):
    for key in (
        "fs_revisions",
        "db_revisions",
        "ddl_revision",
        "warehouses",
        "parts",
        "stock_levels",
        "audit_events",
    ):
        assert key in snapshot, f"Baked snapshot is missing the '{key}' section."
    assert len(snapshot["fs_revisions"]) == 1, "Snapshot should record one on-disk revision."
    assert len(snapshot["db_revisions"]) == 3, "Snapshot should record three database revisions."
    assert snapshot["ddl_revision"], "Snapshot should record the bare-DDL revision id."


def test_database_history_is_ahead_of_filesystem(gel_server, snapshot):
    rows = _query_json(
        "select schema::Migration { name, gb := <str>.generated_by } filter not .builtin"
    )
    db_names = {row["name"] for row in rows}
    assert len(db_names) == 3, f"Expected 3 recorded revisions in the database, got {sorted(db_names)}."
    _, fs_revisions = _fs_revisions()
    missing = db_names - set(fs_revisions)
    assert len(missing) == 2, (
        "Expected exactly two database revisions to be missing from "
        f"dbschema/migrations, got {sorted(missing)}."
    )
    assert set(fs_revisions) <= db_names, "The on-disk revision is not recorded in the database."
    assert set(snapshot["fs_revisions"]) == set(fs_revisions), "Snapshot/filesystem revisions differ."
    assert {r["name"] for r in snapshot["db_revisions"]} == db_names, (
        "Snapshot/database revisions differ."
    )


def test_exactly_one_bare_ddl_revision_recorded(gel_server, snapshot):
    rows = _query_json(
        "select schema::Migration { name, gb := <str>.generated_by } filter not .builtin"
    )
    ddl = [row["name"] for row in rows if row["gb"] == "DDLStatement"]
    assert ddl == [snapshot["ddl_revision"]], (
        "Expected exactly one revision generated by bare DDL matching the snapshot, "
        f"got {ddl} vs {snapshot['ddl_revision']}."
    )


def test_migration_status_reports_drift(gel_server):
    proc = _run_gel(["migration", "status"])
    assert proc.returncode != 0, (
        "`gel migration status` must fail in the drifted initial state, "
        f"but it exited 0.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


def test_live_schema_contains_out_of_band_objects(gel_server):
    rows = _query_json(
        "select schema::ObjectType { name, pointers: { name } } "
        "filter .name in {'default::Part', 'default::StockLevel', 'default::AuditEvent'}"
    )
    by_name = {row["name"]: {p["name"] for p in row["pointers"]} for row in rows}
    assert "default::AuditEvent" in by_name, "Live schema should already contain default::AuditEvent."
    assert "default::Part" in by_name, "Live schema should already contain default::Part."
    assert "default::StockLevel" in by_name, "Live schema should already contain default::StockLevel."
    assert {"event", "note", "seq"} <= by_name["default::AuditEvent"], (
        "Live default::AuditEvent should already have `event`, `note` and `seq`."
    )


def test_reorder_rule_does_not_exist_yet(gel_server):
    rows = _query_json(
        "select schema::ObjectType { name } filter .name = 'default::ReorderRule'"
    )
    assert rows == [], "default::ReorderRule must be created by the executor, not baked in."


def test_seeded_data_matches_snapshot(gel_server, snapshot):
    live = _query_json(
        "select {"
        " warehouses := (select Warehouse { id, code, name } order by .code),"
        " parts := (select Part { id, sku, description, unit_price_cents } order by .sku),"
        " stock_levels := (select StockLevel { id, quantity,"
        "   part_sku := .part.sku, warehouse_code := .warehouse.code }"
        "   order by .part.sku then .warehouse.code),"
        " audit_events := (select AuditEvent { id, event, note, seq } order by .seq)"
        "}"
    )
    assert len(live) == 1, "Unexpected shape returned for the seeded-data query."
    live = live[0]
    for section in ("warehouses", "parts", "stock_levels", "audit_events"):
        assert live[section] == snapshot[section], (
            f"Seeded {section} do not match the baked snapshot.\n"
            f"live: {live[section]}\nsnapshot: {snapshot[section]}"
        )
    assert len(live["parts"]) >= 5, "Expected at least five seeded parts."
    assert len(live["warehouses"]) >= 3, "Expected at least three seeded warehouses."
