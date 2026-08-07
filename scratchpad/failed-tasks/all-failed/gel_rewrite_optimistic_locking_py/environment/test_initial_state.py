import glob
import json
import os
import shutil
import subprocess

import pytest

PROJECT_DIR = "/home/user/wikiapp"
SCHEMA_FILE = os.path.join(PROJECT_DIR, "dbschema", "default.gel")
MIGRATIONS_DIR = os.path.join(PROJECT_DIR, "dbschema", "migrations")
START_SCRIPT = "/usr/local/bin/start-gel.sh"


def _single_json_value(raw):
    """`gel query -F json` wraps the result set in a JSON array."""
    value = json.loads(raw.strip())
    if isinstance(value, list):
        assert len(value) == 1, f"Expected a single result, got {value!r}."
        return value[0]
    return value


@pytest.fixture(scope="session")
def gel_server():
    """Start the local Gel server (idempotent) so CLI/queries can be used."""
    proc = subprocess.run(
        [START_SCRIPT],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, (
        f"{START_SCRIPT} failed to start the local Gel server: "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    return True


def test_gel_cli_available():
    assert shutil.which("gel") is not None, "The `gel` CLI binary was not found in PATH."


def test_gel_server_binary_available():
    candidates = [c for c in glob.glob("/usr/bin/gel-server*") if os.path.isfile(c)]
    if shutil.which("gel-server") is None:
        assert candidates, (
            "No gel-server binary found: neither `gel-server` in PATH nor "
            "/usr/bin/gel-server* candidates exist."
        )


def test_start_script_present_and_executable():
    assert os.path.isfile(START_SCRIPT), f"{START_SCRIPT} does not exist."
    assert os.access(START_SCRIPT, os.X_OK), f"{START_SCRIPT} is not executable."


def test_python_gel_client_importable():
    proc = subprocess.run(
        ["python3", "-c", "import gel; print(gel.create_async_client is not None)"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        "The Gel Python client is not importable: "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )


def test_pytest_tooling_available():
    proc = subprocess.run(
        ["python3", "-m", "pytest", "--version"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"pytest is not usable: stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )


def test_project_directory_exists():
    assert os.path.isdir(PROJECT_DIR), f"Project directory {PROJECT_DIR} does not exist."


def test_project_manifest_exists():
    manifest = os.path.join(PROJECT_DIR, "gel.toml")
    assert os.path.isfile(manifest), f"{manifest} does not exist."


def test_schema_file_exists():
    assert os.path.isfile(SCHEMA_FILE), f"{SCHEMA_FILE} does not exist."


def test_schema_file_has_no_document_type():
    with open(SCHEMA_FILE) as fh:
        content = fh.read()
    assert "Document" not in content, (
        f"{SCHEMA_FILE} already mentions a Document type; the task must not be pre-solved."
    )


def test_no_migrations_applied_yet():
    if os.path.isdir(MIGRATIONS_DIR):
        existing = glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql"))
        assert not existing, (
            f"Migration files already exist in {MIGRATIONS_DIR}: {existing}. "
            "The task must not be pre-solved."
        )


def test_solution_files_absent():
    for name in ("docstore.py", "wikicli.py"):
        path = os.path.join(PROJECT_DIR, name)
        assert not os.path.exists(path), (
            f"{path} already exists; the task must not be pre-solved."
        )


def test_gel_server_answers_queries(gel_server):
    proc = subprocess.run(
        ["gel", "query", "-F", "json", "select 1 + 1"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, (
        "`gel query` against the local instance failed: "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert _single_json_value(proc.stdout) == 2, (
        f"Unexpected query result: {proc.stdout!r}"
    )


def test_document_type_not_in_database(gel_server):
    proc = subprocess.run(
        [
            "gel",
            "query",
            "-F",
            "json",
            "select count(schema::ObjectType filter .name = 'default::Document')",
        ],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, (
        "Schema introspection query failed: "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert _single_json_value(proc.stdout) == 0, (
        "default::Document already exists in the database; the task must not be pre-solved."
    )
