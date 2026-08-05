"""Initial-state verification for the gel_custom_scalars_constraints_validation task.

Only facts that the task environment must provide BEFORE the executor starts are
checked here.
"""

import glob
import os
import shutil
import subprocess
import time

import pytest

PROJECT_DIR = "/home/user/labreg"
SCHEMA_FILE = os.path.join(PROJECT_DIR, "dbschema", "default.gel")
MIGRATIONS_DIR = os.path.join(PROJECT_DIR, "dbschema", "migrations")
GEL_START = "/usr/local/bin/gel-start"


def _run(args, cwd=PROJECT_DIR, timeout=180):
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


@pytest.fixture(scope="session")
def gel_server():
    """Ensure the bundled local Gel server is running and reachable."""
    assert os.path.isfile(GEL_START), (
        f"Server startup helper {GEL_START} is missing from the environment."
    )
    start = _run([GEL_START], cwd="/", timeout=300)
    deadline = time.time() + 180.0
    last_output = start.stdout + start.stderr
    while time.time() < deadline:
        probe = _run(["gel", "query", "select 1"])
        if probe.returncode == 0 and "1" in probe.stdout:
            return True
        last_output = probe.stdout + probe.stderr
        time.sleep(3.0)
    pytest.fail(
        "The local Gel server did not become reachable from "
        f"{PROJECT_DIR}. Last output: {last_output}"
    )


def test_gel_cli_available():
    assert shutil.which("gel") is not None, "The `gel` CLI was not found in PATH."


def test_gel_cli_runs():
    proc = _run(["gel", "--version"], cwd="/")
    assert proc.returncode == 0, f"`gel --version` failed: {proc.stdout} {proc.stderr}"


def test_python_gel_client_importable():
    proc = _run(["python3", "-c", "import gel; print('ok')"], cwd="/")
    assert proc.returncode == 0, (
        f"The Python `gel` client is not importable: {proc.stdout} {proc.stderr}"
    )
    assert "ok" in proc.stdout, "Importing the Python `gel` client did not succeed."


def test_project_directory_exists():
    assert os.path.isdir(PROJECT_DIR), f"Project directory {PROJECT_DIR} does not exist."


def test_gel_toml_exists():
    gel_toml = os.path.join(PROJECT_DIR, "gel.toml")
    assert os.path.isfile(gel_toml), f"Project manifest {gel_toml} does not exist."


def test_schema_file_exists_and_is_empty_of_domain_types():
    assert os.path.isfile(SCHEMA_FILE), f"Schema file {SCHEMA_FILE} does not exist."
    content = open(SCHEMA_FILE, encoding="utf-8").read()
    for name in ("Sample", "BloodSample", "UrineSample", "Measurement",
                 "SpecimenCode", "AnalyteCode", "MeasuredValue", "clean_label"):
        assert name not in content, (
            f"Schema file {SCHEMA_FILE} already declares '{name}'; the initial "
            "schema must not contain the registry model."
        )


def test_migrations_directory_is_empty():
    assert os.path.isdir(MIGRATIONS_DIR), (
        f"Migrations directory {MIGRATIONS_DIR} does not exist."
    )
    existing = glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql"))
    assert existing == [], (
        f"Migrations directory {MIGRATIONS_DIR} must start out without migration "
        f"files, found: {existing}"
    )


def test_validation_module_not_present_yet():
    module_path = os.path.join(PROJECT_DIR, "labreg", "validation.py")
    assert not os.path.exists(module_path), (
        f"{module_path} must be created by the executor, not by the environment."
    )


def test_gel_server_reachable_without_connection_flags(gel_server):
    proc = _run(["gel", "query", "select 1"])
    assert proc.returncode == 0, (
        "`gel query` must work inside the project directory without connection "
        f"flags: {proc.stdout} {proc.stderr}"
    )
    assert "1" in proc.stdout, f"Unexpected `gel query` output: {proc.stdout!r}"


def test_python_client_connects_without_arguments(gel_server):
    script = (
        "import gel\n"
        "c = gel.create_client()\n"
        "print(c.query_single('select 1'))\n"
        "c.close()\n"
    )
    proc = _run(["python3", "-c", script])
    assert proc.returncode == 0, (
        "gel.create_client() must connect with no arguments from the project "
        f"directory: {proc.stdout} {proc.stderr}"
    )
    assert "1" in proc.stdout, f"Unexpected client output: {proc.stdout!r}"


def test_database_schema_has_no_registry_types(gel_server):
    proc = _run([
        "gel",
        "query",
        "select count((select schema::ObjectType filter .name in "
        "{'default::Sample', 'default::BloodSample', 'default::UrineSample', "
        "'default::Measurement'}))",
    ])
    assert proc.returncode == 0, (
        f"Introspection query failed: {proc.stdout} {proc.stderr}"
    )
    assert proc.stdout.strip() == "0", (
        "The database must not contain the registry object types before the task "
        f"starts, got: {proc.stdout!r}"
    )


def test_database_has_no_migrations_applied(gel_server):
    proc = _run(["gel", "query", "select count(schema::Migration)"])
    assert proc.returncode == 0, (
        f"Introspection query failed: {proc.stdout} {proc.stderr}"
    )
    assert proc.stdout.strip() == "0", (
        "The database must start with an empty migration history, got: "
        f"{proc.stdout!r}"
    )
