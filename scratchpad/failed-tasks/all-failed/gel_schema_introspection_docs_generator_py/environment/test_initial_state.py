import json
import os
import shutil
import subprocess

import pytest

PROJECT_DIR = "/home/user/museum"
START_SCRIPT = "start-gel"


@pytest.fixture(scope="session")
def gel_server():
    """Make sure the local Gel server is running before any DB-dependent check."""
    start = shutil.which(START_SCRIPT)
    assert start is not None, (
        f"The '{START_SCRIPT}' helper script is not available in PATH; "
        "the local Gel server cannot be started."
    )
    proc = subprocess.run([start], capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, (
        f"'{START_SCRIPT}' failed with exit code {proc.returncode}.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    return True


def _gel(args, timeout=180):
    return subprocess.run(
        ["gel"] + args,
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_gel_cli_available():
    assert shutil.which("gel") is not None, "The 'gel' CLI binary was not found in PATH."


def test_python_gel_client_importable():
    import gel  # noqa: F401

    assert gel is not None, "The Python 'gel' client package could not be imported."


def test_python3_available():
    assert shutil.which("python3") is not None, "python3 was not found in PATH."


def test_project_directory_exists():
    assert os.path.isdir(PROJECT_DIR), f"Project directory {PROJECT_DIR} does not exist."


def test_gel_toml_exists():
    path = os.path.join(PROJECT_DIR, "gel.toml")
    assert os.path.isfile(path), f"Expected the Gel project manifest {path} to exist."


def test_dbschema_directory_exists():
    path = os.path.join(PROJECT_DIR, "dbschema")
    assert os.path.isdir(path), f"Expected the schema directory {path} to exist."


def test_generator_not_yet_written():
    path = os.path.join(PROJECT_DIR, "tools", "schema_docs.py")
    assert not os.path.exists(path), (
        f"{path} already exists, but the executor is supposed to create it."
    )


def test_gel_server_is_reachable(gel_server):
    proc = _gel(["query", "select 1"])
    assert proc.returncode == 0, (
        "Could not query the local Gel instance from the project directory.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


def test_project_is_linked_to_instance(gel_server):
    proc = _gel(["query", "--output-format", "json", "select sys::get_version_as_str()"])
    assert proc.returncode == 0, (
        "The project at /home/user/museum does not resolve to a linked Gel instance.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert payload, "Unexpected empty server version payload."


def test_starting_schema_has_no_user_object_types(gel_server):
    proc = _gel(
        [
            "query",
            "--output-format",
            "json",
            "select schema::ObjectType { name } "
            "filter .name like 'default::%' order by .name",
        ]
    )
    assert proc.returncode == 0, (
        f"Introspection query failed.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    types = json.loads(proc.stdout)
    names = [t["name"] for t in types]
    assert names == [], (
        f"Expected the starting database to contain no user-defined object types, found: {names}"
    )
