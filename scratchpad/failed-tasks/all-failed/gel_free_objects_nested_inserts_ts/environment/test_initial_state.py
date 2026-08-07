"""Initial-state verification for the gel_free_objects_nested_inserts_ts task.

These tests run BEFORE the executor starts working. They assert that the
pre-baked environment (local Gel 6 server, Node toolchain, project skeleton)
is in place and that none of the artifacts the executor must produce exist yet.
"""

import json
import os
import shutil
import subprocess

import pytest

PROJECT_DIR = "/home/user/catalog-ingest"
START_SERVER = "/usr/local/bin/start-gel.sh"
CREDENTIALS_FILE = "/etc/gel/credentials.json"


@pytest.fixture(scope="session")
def gel_server():
    """Make sure the local Gel server is running before any DB/CLI check."""
    proc = subprocess.run(
        ["bash", START_SERVER],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, (
        f"Failed to start the local Gel server: {proc.stdout}\n{proc.stderr}"
    )
    return True


def gel_query(query: str):
    proc = subprocess.run(
        ["gel", "query", "-F", "json", query],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=PROJECT_DIR,
    )
    assert proc.returncode == 0, (
        f"`gel query` failed for {query!r}: {proc.stdout}\n{proc.stderr}"
    )
    return json.loads(proc.stdout)


def test_gel_cli_available():
    assert shutil.which("gel") is not None, "The `gel` CLI was not found in PATH."


def test_node_toolchain_available():
    for binary in ("node", "npm", "npx"):
        assert shutil.which(binary) is not None, f"`{binary}` was not found in PATH."


def test_project_directory_exists():
    assert os.path.isdir(PROJECT_DIR), f"Project directory {PROJECT_DIR} does not exist."


def test_gel_toml_exists():
    path = os.path.join(PROJECT_DIR, "gel.toml")
    assert os.path.isfile(path), f"{path} does not exist."


def test_package_json_exists():
    path = os.path.join(PROJECT_DIR, "package.json")
    assert os.path.isfile(path), f"{path} does not exist."


def test_node_dependencies_preinstalled():
    node_modules = os.path.join(PROJECT_DIR, "node_modules")
    assert os.path.isdir(node_modules), (
        f"{node_modules} does not exist; npm dependencies must be pre-installed."
    )
    assert os.path.isdir(os.path.join(node_modules, "gel")), (
        "The `gel` npm client is not pre-installed in node_modules."
    )
    assert os.path.isfile(os.path.join(node_modules, ".bin", "tsx")), (
        "`tsx` is not pre-installed in node_modules/.bin."
    )
    assert os.path.isfile(os.path.join(node_modules, ".bin", "tsc")), (
        "`typescript` is not pre-installed in node_modules/.bin."
    )


def test_credentials_file_configured():
    assert os.environ.get("GEL_CREDENTIALS_FILE") == CREDENTIALS_FILE, (
        "GEL_CREDENTIALS_FILE must point at the pre-configured local credentials."
    )
    assert os.path.isfile(CREDENTIALS_FILE), (
        f"{CREDENTIALS_FILE} does not exist; the Gel connection is not configured."
    )


def test_ingest_script_not_written_yet():
    path = os.path.join(PROJECT_DIR, "src", "ingest.ts")
    assert not os.path.exists(path), (
        f"{path} already exists; the executor is supposed to create it."
    )


def test_no_migrations_yet():
    migrations = os.path.join(PROJECT_DIR, "dbschema", "migrations")
    existing = []
    if os.path.isdir(migrations):
        existing = [f for f in os.listdir(migrations) if f.endswith(".edgeql")]
    assert existing == [], (
        f"Unexpected pre-existing migrations in {migrations}: {existing}"
    )


def test_gel_server_is_reachable(gel_server):
    assert gel_query("select 1") == [1], "The local Gel server did not answer `select 1`."


def test_database_has_no_task_types_yet(gel_server):
    names = gel_query(
        "select schema::ObjectType { name } filter .name like 'default::%'"
    )
    found = sorted(entry["name"] for entry in names)
    assert found == [], (
        f"The database already contains user-defined object types: {found}"
    )


def test_migration_history_is_empty(gel_server):
    proc = subprocess.run(
        ["gel", "migration", "log", "--from-db"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=PROJECT_DIR,
    )
    assert proc.returncode == 0, (
        f"`gel migration log --from-db` failed: {proc.stdout}\n{proc.stderr}"
    )
    history = proc.stdout.strip()
    assert history in ("", "<no migrations>"), (
        f"Expected an empty migration history, got: {history}"
    )
