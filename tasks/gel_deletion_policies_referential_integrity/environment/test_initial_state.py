import importlib.util
import os
import shutil
import subprocess

import pytest

PROJECT_DIR = "/home/user/docmgr"
GEL_TOML = os.path.join(PROJECT_DIR, "gel.toml")
SCHEMA_FILE = os.path.join(PROJECT_DIR, "dbschema", "default.gel")


@pytest.fixture(scope="session")
def gel_server():
    """Make sure the local Gel server is running before any DB-dependent check."""
    serve = shutil.which("gel-serve")
    assert serve is not None, "The 'gel-serve' helper is not available in PATH."
    proc = subprocess.run([serve], capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, (
        "Failed to start the local Gel server with 'gel-serve': "
        f"stdout={proc.stdout} stderr={proc.stderr}"
    )
    return True


def test_gel_cli_available():
    assert shutil.which("gel") is not None, "The 'gel' CLI binary was not found in PATH."


def test_gel_cli_is_the_real_binary():
    proc = subprocess.run(
        ["gel", "--version"], capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, (
        f"'gel --version' failed: stdout={proc.stdout} stderr={proc.stderr}"
    )
    assert "Gel CLI" in proc.stdout, (
        f"Unexpected output from 'gel --version': {proc.stdout!r}"
    )


def test_gel_python_client_importable():
    assert importlib.util.find_spec("gel") is not None, (
        "The Gel Python client ('gel' package) is not importable."
    )


def test_project_directory_exists():
    assert os.path.isdir(PROJECT_DIR), f"Project directory {PROJECT_DIR} does not exist."


def test_gel_toml_exists():
    assert os.path.isfile(GEL_TOML), f"Expected project manifest {GEL_TOML} to exist."


def test_default_schema_file_exists():
    assert os.path.isfile(SCHEMA_FILE), (
        f"Expected schema file {SCHEMA_FILE} to exist in the project skeleton."
    )


def test_connection_settings_present_in_environment():
    assert os.environ.get("GEL_DSN"), (
        "GEL_DSN is not set; the environment must provide the local connection settings."
    )


def test_server_reachable_from_project_directory(gel_server):
    proc = subprocess.run(
        ["gel", "query", "select 1 + 1"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"'gel query' failed in {PROJECT_DIR}: stdout={proc.stdout} stderr={proc.stderr}"
    )
    assert "2" in proc.stdout, f"Unexpected query result: {proc.stdout!r}"


def test_default_module_has_no_user_types_yet(gel_server):
    proc = subprocess.run(
        [
            "gel",
            "query",
            "select count((select schema::ObjectType "
            "filter .name like 'default::%'))",
        ],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"Schema introspection failed: stdout={proc.stdout} stderr={proc.stderr}"
    )
    assert proc.stdout.strip() == "0", (
        "The 'default' module is expected to be empty in the initial state, "
        f"introspection returned: {proc.stdout!r}"
    )
