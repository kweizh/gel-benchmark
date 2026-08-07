import json
import os
import shutil
import subprocess

import pytest

PROJECT_DIR = "/home/user/collections"
DATA_FILE = os.path.join(PROJECT_DIR, "data", "instruments.json")
ENSURE_SCRIPT = "/usr/local/bin/gel-ensure.sh"

REQUIRED_RECORD_KEYS = {"code", "labels", "tags", "span", "coverage", "origin"}


@pytest.fixture(scope="session")
def gel_server():
    """Start the local Gel server (idempotent) and make sure it answers queries."""
    proc = subprocess.run(
        [ENSURE_SCRIPT],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, (
        f"{ENSURE_SCRIPT} failed with exit code {proc.returncode}. "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    return True


def test_gel_cli_available():
    assert shutil.which("gel") is not None, "The 'gel' CLI binary was not found in PATH."


def test_gel_python_client_importable():
    import gel  # noqa: F401

    assert hasattr(gel, "create_async_client"), (
        "The Python 'gel' client library is installed but does not expose "
        "create_async_client()."
    )


def test_gel_ensure_script_is_executable():
    assert os.path.isfile(ENSURE_SCRIPT), f"{ENSURE_SCRIPT} does not exist."
    assert os.access(ENSURE_SCRIPT, os.X_OK), f"{ENSURE_SCRIPT} is not executable."


def test_connection_environment_variables_present():
    for name in ("GEL_HOST", "GEL_PORT", "GEL_USER", "GEL_BRANCH"):
        assert os.environ.get(name), (
            f"Environment variable {name} is not set; the Gel connection is expected "
            "to be preconfigured through GEL_* environment variables."
        )
    assert os.environ.get("GEL_PORT") == "5656", (
        "The local Gel server is expected to listen on port 5656, "
        f"but GEL_PORT={os.environ.get('GEL_PORT')!r}."
    )


def test_project_directory_exists():
    assert os.path.isdir(PROJECT_DIR), f"Project directory {PROJECT_DIR} does not exist."


def test_dbschema_directory_exists():
    dbschema = os.path.join(PROJECT_DIR, "dbschema")
    assert os.path.isdir(dbschema), f"Schema directory {dbschema} does not exist."


def test_input_data_file_exists_and_is_readable():
    assert os.path.isfile(DATA_FILE), f"Input data file {DATA_FILE} does not exist."
    with open(DATA_FILE, encoding="utf-8") as handle:
        payload = json.load(handle)
    assert isinstance(payload, list) and payload, (
        f"{DATA_FILE} is expected to contain a non-empty JSON array of records."
    )
    for record in payload:
        assert isinstance(record, dict), f"{DATA_FILE} must contain JSON objects."
        missing = REQUIRED_RECORD_KEYS - set(record)
        assert not missing, (
            f"A record in {DATA_FILE} is missing the key(s) {sorted(missing)}."
        )


def test_gel_server_answers_queries(gel_server):
    proc = subprocess.run(
        ["gel", "query", "select 1"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=PROJECT_DIR,
    )
    assert proc.returncode == 0, (
        "The local Gel server did not answer a trivial query. "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert "1" in proc.stdout, f"Unexpected query output: {proc.stdout!r}"


def test_database_has_no_instrument_type(gel_server):
    proc = subprocess.run(
        [
            "gel",
            "query",
            "-F",
            "json",
            "select count(schema::ObjectType filter .name = 'default::Instrument')",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=PROJECT_DIR,
    )
    assert proc.returncode == 0, (
        f"Schema introspection failed. stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert json.loads(proc.stdout) == [0], (
        "The database is expected to start without a 'default::Instrument' object type, "
        f"but introspection returned {proc.stdout!r}."
    )
