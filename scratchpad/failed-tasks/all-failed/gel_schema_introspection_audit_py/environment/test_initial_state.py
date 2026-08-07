"""Initial-state verification for the gel_schema_introspection_audit_py task.

These checks run BEFORE the executor starts working. They only assert facts that
the task description states are already present in the environment.
"""

import glob
import json
import os
import shutil
import subprocess

import pytest

PROJECT_DIR = "/home/user/gel-audit"
PACKAGE_DIR = os.path.join(PROJECT_DIR, "schema_audit")
SCHEMA_DIR = os.path.join(PROJECT_DIR, "dbschema")
SCHEMA_FILE = os.path.join(SCHEMA_DIR, "default.gel")
MIGRATIONS_DIR = os.path.join(SCHEMA_DIR, "migrations")
START_SCRIPT = "/usr/local/bin/gel-start.sh"


@pytest.fixture(scope="module")
def gel_server():
    """Start the local Gel server (idempotent) and wait until it is ready.

    Every check that talks to the database (directly or through the CLI) must
    request this fixture, otherwise it can race the server startup.
    """
    proc = subprocess.run(
        [START_SCRIPT],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, (
        f"{START_SCRIPT} failed with exit code {proc.returncode}.\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    return True


def run_cli(args, timeout=120):
    return subprocess.run(
        args,
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_gel_cli_available():
    assert shutil.which("gel") is not None, "The 'gel' CLI was not found in PATH."


def test_gel_python_client_importable():
    proc = subprocess.run(
        ["python3", "-c", "import gel; print(gel.__name__)"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        "The Gel Python client ('gel' package) is not importable.\n"
        f"stderr:\n{proc.stderr}"
    )


def test_pytest_available():
    proc = subprocess.run(
        ["python3", "-m", "pytest", "--version"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"pytest is not installed: {proc.stderr}"


def test_project_directory_exists():
    assert os.path.isdir(PROJECT_DIR), f"Project directory {PROJECT_DIR} does not exist."


def test_gel_toml_exists():
    gel_toml = os.path.join(PROJECT_DIR, "gel.toml")
    assert os.path.isfile(gel_toml), f"Expected the Gel project file {gel_toml} to exist."


def test_schema_file_exists():
    assert os.path.isfile(SCHEMA_FILE), f"Expected the schema file {SCHEMA_FILE} to exist."
    content = open(SCHEMA_FILE, encoding="utf-8").read()
    assert "module default" in content, (
        f"{SCHEMA_FILE} does not declare the 'default' module."
    )


def test_schema_file_has_injection_point():
    content = open(SCHEMA_FILE, encoding="utf-8").read()
    assert "TEST INJECTION POINT" in content, (
        f"{SCHEMA_FILE} is missing the injection-point marker comment."
    )


def test_migrations_directory_has_migrations():
    assert os.path.isdir(MIGRATIONS_DIR), (
        f"Expected the migrations directory {MIGRATIONS_DIR} to exist."
    )
    # Migration filenames are content-hashed, so they must be globbed.
    files = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql")))
    assert files, f"No migration files found in {MIGRATIONS_DIR}."


def test_start_script_is_executable():
    assert os.path.isfile(START_SCRIPT), f"Expected {START_SCRIPT} to exist."
    assert os.access(START_SCRIPT, os.X_OK), f"Expected {START_SCRIPT} to be executable."


def test_stub_package_exists():
    assert os.path.isdir(PACKAGE_DIR), f"Expected the stub package {PACKAGE_DIR} to exist."
    init_file = os.path.join(PACKAGE_DIR, "__init__.py")
    assert os.path.isfile(init_file), f"Expected {init_file} to exist."
    main_file = os.path.join(PACKAGE_DIR, "__main__.py")
    assert os.path.isfile(main_file), f"Expected {main_file} to exist."


def test_stub_is_not_implemented():
    proc = run_cli(
        [
            "python3",
            "-c",
            "import schema_audit\n"
            "try:\n"
            "    schema_audit.main(['audit', '--out', '/tmp/initial-state-probe.json'])\n"
            "except NotImplementedError:\n"
            "    print('NOT_IMPLEMENTED')\n",
        ]
    )
    assert "NOT_IMPLEMENTED" in proc.stdout, (
        "Expected the baked schema_audit stub to raise NotImplementedError.\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert not os.path.exists("/tmp/initial-state-probe.json"), (
        "The stub must not write an audit document."
    )


def test_server_answers_queries(gel_server):
    proc = run_cli(["gel", "query", "-F", "json", "select 1"])
    assert proc.returncode == 0, (
        f"'gel query' failed: exit={proc.returncode}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


def test_migration_history_is_applied(gel_server):
    proc = run_cli(
        ["gel", "query", "-F", "json", "select count(schema::Migration)"]
    )
    assert proc.returncode == 0, f"Failed to count migrations: {proc.stderr}"
    payload = json.loads(proc.stdout.strip())
    count = payload[0] if isinstance(payload, list) else payload
    assert int(count) >= 1, "Expected at least one applied migration in the database."


def test_migration_status_is_in_sync(gel_server):
    proc = run_cli(["gel", "migration", "status"])
    combined = (proc.stdout + proc.stderr).lower()
    assert proc.returncode == 0 or "up to date" in combined, (
        "Expected the baked migration history to be up to date.\n"
        f"exit={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


def test_baked_schema_has_user_object_types(gel_server):
    proc = run_cli(
        [
            "gel",
            "query",
            "-F",
            "json",
            "select schema::ObjectType { name } "
            "filter not .builtin and not .from_alias and not .compound_type",
        ]
    )
    assert proc.returncode == 0, f"Introspection query failed: {proc.stderr}"
    names = {row["name"] for row in json.loads(proc.stdout)}
    assert len(names) >= 10, (
        f"Expected the baked schema to define at least 10 object types, got: {sorted(names)}"
    )


def test_baked_schema_has_link_properties(gel_server):
    # Reaching a link's own pointers requires the schema::Link type intersection.
    proc = run_cli(
        [
            "gel",
            "query",
            "-F",
            "json",
            "select schema::ObjectType { "
            "  name, "
            "  pointers: { name, [is schema::Link].pointers: { name } } "
            "} filter not .builtin and not .from_alias and not .compound_type",
        ]
    )
    assert proc.returncode == 0, f"Link-property introspection failed: {proc.stderr}"
    rows = json.loads(proc.stdout)
    found = []
    for row in rows:
        for pointer in row.get("pointers") or []:
            for nested in pointer.get("pointers") or []:
                if nested["name"] not in ("source", "target"):
                    found.append(f"{row['name']}.{pointer['name']}@{nested['name']}")
    assert found, "Expected the baked schema to declare at least one link property."


def test_baked_schema_has_globals_policies_and_triggers(gel_server):
    proc = run_cli(
        [
            "gel",
            "query",
            "-F",
            "json",
            "select { "
            "  globals := count((select schema::Global filter not .builtin)), "
            "  policies := count((select schema::AccessPolicy filter not .builtin)), "
            "  triggers := count((select schema::Trigger filter not .builtin)), "
            "  functions := count((select schema::Function filter not .builtin)), "
            "  aliases := count((select schema::Alias filter not .builtin)) "
            "}",
        ]
    )
    assert proc.returncode == 0, f"Introspection query failed: {proc.stderr}"
    payload = json.loads(proc.stdout)
    row = payload[0] if isinstance(payload, list) else payload
    for key in ("globals", "policies", "triggers", "functions", "aliases"):
        assert int(row[key]) >= 1, (
            f"Expected the baked schema to declare at least one {key}, got {row[key]}."
        )
