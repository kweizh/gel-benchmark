import glob
import json
import os
import shutil
import subprocess

import pytest

PROJECT_DIR = "/home/user/booking"
SCHEMA_FILE = os.path.join(PROJECT_DIR, "dbschema", "default.gel")
START_SCRIPT = "/usr/local/bin/gel-start.sh"


@pytest.fixture(scope="session")
def gel_server():
    proc = subprocess.run(
        [START_SCRIPT],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, (
        f"{START_SCRIPT} failed to start the local Gel instance: "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    return True


def _gel_query(query):
    return subprocess.run(
        ["gel", "query", "-F", "json", query],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_gel_cli_available():
    assert shutil.which("gel") is not None, "The 'gel' CLI was not found in PATH."


def test_gel_python_client_importable():
    proc = subprocess.run(
        ["python3", "-c", "import gel; print(gel.__version__)"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        "The Python 'gel' client is not importable: " f"{proc.stderr!r}"
    )


def test_start_script_present_and_executable():
    assert os.path.isfile(START_SCRIPT), f"{START_SCRIPT} does not exist."
    assert os.access(START_SCRIPT, os.X_OK), f"{START_SCRIPT} is not executable."


def test_project_directory_exists():
    assert os.path.isdir(PROJECT_DIR), f"Project directory {PROJECT_DIR} does not exist."


def test_project_files_exist():
    for name in ("gel.toml", "README.md"):
        path = os.path.join(PROJECT_DIR, name)
        assert os.path.isfile(path), f"Expected {path} to exist in the initial state."
    assert os.path.isfile(SCHEMA_FILE), f"Expected schema file {SCHEMA_FILE} to exist."


def test_schema_module_is_still_empty():
    with open(SCHEMA_FILE) as f:
        content = f.read()
    assert "module default" in content, (
        f"{SCHEMA_FILE} should declare an (empty) 'module default' block."
    )
    for name in ("Resource", "Reservation"):
        assert name not in content, (
            f"{SCHEMA_FILE} must not declare '{name}' in the initial state."
        )


def test_no_migrations_created_yet():
    migrations = glob.glob(os.path.join(PROJECT_DIR, "dbschema", "migrations", "*.edgeql"))
    assert migrations == [], (
        f"No migration files should exist in the initial state, found: {migrations}"
    )


def test_solution_files_absent():
    for name in ("booking_service.py", "booking_cli.py"):
        path = os.path.join(PROJECT_DIR, name)
        assert not os.path.exists(path), (
            f"{path} must not exist in the initial state (the executor creates it)."
        )


def test_instance_reachable_and_schema_empty(gel_server):
    proc = _gel_query(
        "select <str>count((select schema::ObjectType "
        "filter .name in {'default::Resource', 'default::Reservation'}))"
    )
    assert proc.returncode == 0, (
        f"Failed to query the local Gel instance: stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert json.loads(proc.stdout) == ["0"], (
        "The database must not already contain the 'Resource'/'Reservation' object types: "
        f"{proc.stdout!r}"
    )


def test_connection_settings_provided_by_environment():
    assert os.environ.get("GEL_DSN"), (
        "The Gel connection DSN must be provided through the environment (GEL_DSN)."
    )


def test_no_reservations_stored_yet(gel_server):
    proc = _gel_query("select <str>count(schema::Migration)")
    assert proc.returncode == 0, (
        f"Failed to query the local Gel instance: stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert json.loads(proc.stdout) == ["0"], (
        f"The initial database must have no applied migrations: {proc.stdout!r}"
    )
