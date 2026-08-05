"""Initial-state verification for the gel_fulltext_search_ranking_py task."""

import json
import os
import shutil
import subprocess

import pytest

PROJECT_DIR = "/home/user/kbsearch"
GEL_TOML = os.path.join(PROJECT_DIR, "gel.toml")
SCHEMA_FILE = os.path.join(PROJECT_DIR, "dbschema", "default.gel")
SEED_DATA = os.path.join(PROJECT_DIR, "seed_data.json")
START_SCRIPT = "start-gel.sh"
VALID_STATUSES = {"draft", "published", "archived"}
REQUIRED_RECORD_KEYS = {"slug", "title", "summary", "body", "status", "tags"}


@pytest.fixture(scope="session")
def gel_server():
    """Start the bundled local Gel server (idempotent) and return once it answers."""
    script = shutil.which(START_SCRIPT)
    assert script is not None, f"{START_SCRIPT} was not found in PATH."
    proc = subprocess.run([script], capture_output=True, text=True, timeout=420)
    assert proc.returncode == 0, (
        f"{START_SCRIPT} failed with exit code {proc.returncode}.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    return True


def _gel_query(query):
    gel_bin = shutil.which("gel")
    assert gel_bin is not None, "gel CLI not found in PATH."
    return subprocess.run(
        [gel_bin, "query", query],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=PROJECT_DIR,
    )


def test_gel_cli_available():
    assert shutil.which("gel") is not None, "The gel CLI binary is not available in PATH."


def test_gel_server_binary_available():
    assert (
        shutil.which("gel-server-6") is not None
        or os.path.isfile("/usr/bin/gel-server-6")
    ), "The local gel-server-6 binary is not installed in the container."


def test_start_script_available():
    assert shutil.which(START_SCRIPT) is not None, (
        f"{START_SCRIPT} (used to bring up the local Gel server) is not in PATH."
    )


def test_gel_python_client_importable():
    import gel  # noqa: PLC0415

    assert hasattr(gel, "create_async_client"), (
        "The installed gel Python client does not expose create_async_client."
    )


def test_pytest_available_for_default_python():
    proc = subprocess.run(
        ["python3", "-c", "import pytest, gel; print('ok')"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0 and "ok" in proc.stdout, (
        "The default python3 must provide both pytest and the gel client.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


def test_connection_env_vars_exported():
    assert os.environ.get("GEL_DSN"), "GEL_DSN is not exported in the environment."
    assert os.environ.get("GEL_CLIENT_TLS_SECURITY"), (
        "GEL_CLIENT_TLS_SECURITY is not exported in the environment."
    )


def test_project_directory_exists():
    assert os.path.isdir(PROJECT_DIR), f"Project directory {PROJECT_DIR} does not exist."


def test_gel_toml_exists():
    assert os.path.isfile(GEL_TOML), f"Expected the project manifest {GEL_TOML} to exist."


def test_schema_file_exists_and_is_empty():
    assert os.path.isfile(SCHEMA_FILE), f"Expected the schema file {SCHEMA_FILE} to exist."
    content = open(SCHEMA_FILE, encoding="utf-8").read()
    assert "Article" not in content, (
        f"{SCHEMA_FILE} must not declare the Article model before the task starts."
    )
    assert "ArticleStatus" not in content, (
        f"{SCHEMA_FILE} must not declare ArticleStatus before the task starts."
    )


def test_seed_data_file_contract():
    assert os.path.isfile(SEED_DATA), f"Expected the corpus file {SEED_DATA} to exist."
    records = json.loads(open(SEED_DATA, encoding="utf-8").read())
    assert isinstance(records, list), f"{SEED_DATA} must contain a JSON array."
    assert len(records) == 30, f"{SEED_DATA} must contain 30 records, found {len(records)}."
    slugs = set()
    for record in records:
        assert isinstance(record, dict), f"Every record in {SEED_DATA} must be a JSON object."
        assert REQUIRED_RECORD_KEYS.issubset(record.keys()), (
            f"Record {record.get('slug')!r} in {SEED_DATA} is missing required keys; "
            f"expected at least {sorted(REQUIRED_RECORD_KEYS)}."
        )
        assert record["status"] in VALID_STATUSES, (
            f"Record {record['slug']!r} has an unexpected status {record['status']!r}."
        )
        assert isinstance(record["tags"], list), (
            f"Record {record['slug']!r} must carry its tags as a JSON array."
        )
        slugs.add(record["slug"])
    assert len(slugs) == len(records), f"Slugs in {SEED_DATA} are not unique."


def test_gel_server_answers_queries(gel_server):
    proc = _gel_query("select 1")
    assert proc.returncode == 0, (
        "The local Gel server did not answer a trivial query after being started.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


def test_gel_server_is_version_6(gel_server):
    proc = _gel_query("select sys::get_version().major")
    assert proc.returncode == 0, (
        f"Could not read the server version.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "6" in proc.stdout, f"Expected a Gel 6 server, got: {proc.stdout!r}"


def test_database_has_no_article_type_yet(gel_server):
    proc = _gel_query(
        "select count((select schema::ObjectType filter .name = 'default::Article'))"
    )
    assert proc.returncode == 0, (
        f"Schema introspection failed.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert proc.stdout.strip() == "0", (
        "The database must not contain default::Article before the task starts, "
        f"introspection returned: {proc.stdout!r}"
    )


def test_no_migrations_applied_yet(gel_server):
    proc = _gel_query("select count(schema::Migration)")
    assert proc.returncode == 0, (
        f"Could not count applied migrations.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert proc.stdout.strip() == "0", (
        f"No migration must be applied before the task starts, got: {proc.stdout!r}"
    )
