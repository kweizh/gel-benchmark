import glob
import json
import os
import shutil
import subprocess

import pytest

PROJECT_DIR = "/home/user/usage-report"
DBSCHEMA_DIR = os.path.join(PROJECT_DIR, "dbschema")
MIGRATIONS_DIR = os.path.join(DBSCHEMA_DIR, "migrations")
DATA_DIR = os.path.join(PROJECT_DIR, "data")

EXPECTED_TENANT_ROWS = [
    "acme-us,Acme Corp,America/New_York,2024-03-08",
    "globex-de,Globex GmbH,Europe/Berlin,2024-10-25",
    "initech-in,Initech Pvt Ltd,Asia/Kolkata,2024-02-27",
]


def _run(args, timeout=120):
    return subprocess.run(
        args,
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.fixture(scope="session")
def gel_server():
    """Make sure the local Gel server is up before any CLI-dependent test runs."""
    starter = "/usr/local/bin/gel-up"
    if os.path.isfile(starter) and os.access(starter, os.X_OK):
        subprocess.run([starter], capture_output=True, text=True, timeout=300)
    proc = _run(["gel", "query", "-F", "json", "select 1"])
    assert proc.returncode == 0, (
        "Local Gel server is not reachable with the preconfigured environment: "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    return True


def test_gel_cli_available():
    assert shutil.which("gel") is not None, "The `gel` CLI was not found in PATH."


def test_jq_available():
    assert shutil.which("jq") is not None, "`jq` was not found in PATH."


def test_project_directory_exists():
    assert os.path.isdir(PROJECT_DIR), f"Project directory {PROJECT_DIR} does not exist."


def test_gel_toml_exists():
    path = os.path.join(PROJECT_DIR, "gel.toml")
    assert os.path.isfile(path), f"{path} does not exist."


def test_schema_file_has_starter_tenant_only():
    path = os.path.join(DBSCHEMA_DIR, "default.gel")
    assert os.path.isfile(path), f"{path} does not exist."
    with open(path, encoding="utf-8") as fh:
        content = fh.read()
    assert "module default" in content, f"{path} does not declare `module default`."
    assert "Tenant" in content, f"{path} does not declare the starter `Tenant` object type."
    assert "display_name" in content, f"{path} does not declare `display_name` on Tenant."
    assert "UsageSession" not in content, (
        f"{path} already declares `UsageSession`; the executor is supposed to add it."
    )
    assert "billing_anchor" not in content, (
        f"{path} already declares `billing_anchor`; the executor is supposed to add it."
    )


def test_exactly_one_initial_migration_present():
    files = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql")))
    assert len(files) == 1, (
        f"Expected exactly 1 initial migration in {MIGRATIONS_DIR}, found {len(files)}: {files}"
    )


def test_tenants_csv_present_with_reference_rows():
    path = os.path.join(DATA_DIR, "tenants.csv")
    assert os.path.isfile(path), f"{path} does not exist."
    lines = [ln.strip() for ln in open(path, encoding="utf-8").read().splitlines() if ln.strip()]
    assert lines[0] == "code,display_name,tz,billing_anchor", (
        f"Unexpected header in {path}: {lines[0]!r}"
    )
    assert lines[1:] == EXPECTED_TENANT_ROWS, (
        f"Unexpected tenant rows in {path}: {lines[1:]!r}"
    )


def test_sessions_csv_present_with_18_rows():
    path = os.path.join(DATA_DIR, "sessions.csv")
    assert os.path.isfile(path), f"{path} does not exist."
    lines = [ln.strip() for ln in open(path, encoding="utf-8").read().splitlines() if ln.strip()]
    assert lines[0] == "session_key,tenant_code,started_at,ended_at", (
        f"Unexpected header in {path}: {lines[0]!r}"
    )
    assert len(lines) - 1 == 18, (
        f"Expected 18 session rows in {path}, found {len(lines) - 1}."
    )


def test_gel_server_reachable(gel_server):
    assert gel_server is True


def test_tenant_type_exists_and_usage_session_does_not(gel_server):
    proc = _run(
        [
            "gel",
            "query",
            "-F",
            "json",
            "select schema::ObjectType { name } "
            "filter .name in {'default::Tenant', 'default::UsageSession'}",
        ]
    )
    assert proc.returncode == 0, f"Introspection query failed: {proc.stderr}"
    names = {row["name"] for row in json.loads(proc.stdout)}
    assert "default::Tenant" in names, "`default::Tenant` is missing from the initial database."
    assert "default::UsageSession" not in names, (
        "`default::UsageSession` already exists; the executor is supposed to create it."
    )


def test_database_has_no_tenant_objects(gel_server):
    proc = _run(["gel", "query", "-F", "json", "select count(Tenant)"])
    assert proc.returncode == 0, f"Count query failed: {proc.stderr}"
    assert json.loads(proc.stdout) == [0], (
        f"Expected an empty Tenant set in the initial database, got {proc.stdout!r}."
    )


def test_migration_history_is_in_sync(gel_server):
    proc = _run(["gel", "migration", "status"])
    combined = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, (
        f"`gel migration status` failed in the initial environment: {combined}"
    )
    assert "up to date" in combined.lower(), (
        f"Initial migration history is not in sync: {combined}"
    )


def test_solution_artifacts_are_absent():
    for rel in ("scripts/report.sh", "scripts/calendar.sh", "scripts/seed.sh"):
        path = os.path.join(PROJECT_DIR, rel)
        assert not os.path.exists(path), (
            f"{path} already exists; the executor is supposed to create it."
        )
