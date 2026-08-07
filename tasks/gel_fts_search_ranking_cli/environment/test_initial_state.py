import json
import os
import shutil
import subprocess

import pytest

PROJECT_DIR = "/home/user/kb-search"
SCHEMA_FILE = os.path.join(PROJECT_DIR, "dbschema", "default.gel")
CORPUS_FILE = os.path.join(PROJECT_DIR, "data", "corpus.json")
FIXTURE_DIR = "/opt/kb-fixtures"
CORPUS_KEYS = {"slug", "title", "summary", "body", "section", "published"}


def run(args, cwd=None, timeout=180):
    env = os.environ.copy()
    return subprocess.run(
        args,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.fixture(scope="module")
def gel_server():
    """Start the bundled local Gel instance (idempotent) before DB checks."""
    proc = run(["gel-start"], timeout=300)
    assert proc.returncode == 0, (
        f"'gel-start' failed to start the local Gel instance: "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    probe = run(["gel", "query", "-F", "json", "select 1"], cwd=PROJECT_DIR)
    assert probe.returncode == 0, (
        f"Local Gel instance does not answer queries after 'gel-start': "
        f"stdout={probe.stdout!r} stderr={probe.stderr!r}"
    )
    return True


def test_gel_cli_available():
    assert shutil.which("gel") is not None, "The 'gel' CLI binary was not found in PATH."


def test_gel_start_helper_available():
    assert shutil.which("gel-start") is not None, (
        "The 'gel-start' helper used to start the local Gel instance was not found in PATH."
    )


def test_connection_environment_configured():
    assert os.environ.get("GEL_DSN"), (
        "GEL_DSN is not set; the local instance connection settings are missing."
    )


def test_project_directory_exists():
    assert os.path.isdir(PROJECT_DIR), f"Project directory {PROJECT_DIR} does not exist."


def test_gel_toml_exists():
    path = os.path.join(PROJECT_DIR, "gel.toml")
    assert os.path.isfile(path), f"Expected the Gel project manifest {path} to exist."


def test_schema_module_file_exists_and_is_empty():
    assert os.path.isfile(SCHEMA_FILE), f"Expected schema file {SCHEMA_FILE} to exist."
    content = open(SCHEMA_FILE).read()
    assert "module default" in content, (
        f"{SCHEMA_FILE} should declare an (empty) 'module default' block, got: {content!r}"
    )
    assert "Article" not in content, (
        f"{SCHEMA_FILE} must not already declare the Article type (task must not be pre-solved)."
    )
    assert "fts::" not in content, (
        f"{SCHEMA_FILE} must not already contain any full-text search declaration."
    )


def test_no_migrations_applied_yet():
    migrations_dir = os.path.join(PROJECT_DIR, "dbschema", "migrations")
    existing = []
    if os.path.isdir(migrations_dir):
        existing = [n for n in os.listdir(migrations_dir) if n.endswith(".edgeql")]
    assert existing == [], (
        f"Expected no migration files in {migrations_dir} at task start, found: {existing}"
    )


def test_scripts_directory_exists_and_is_empty():
    scripts_dir = os.path.join(PROJECT_DIR, "scripts")
    assert os.path.isdir(scripts_dir), f"Expected scripts directory {scripts_dir} to exist."
    for name in ("load.sh", "search.sh"):
        path = os.path.join(scripts_dir, name)
        assert not os.path.exists(path), (
            f"{path} already exists; the task must not be pre-solved."
        )


def test_corpus_file_is_valid():
    assert os.path.isfile(CORPUS_FILE), f"Expected corpus file {CORPUS_FILE} to exist."
    with open(CORPUS_FILE) as fh:
        corpus = json.load(fh)
    assert isinstance(corpus, list), f"{CORPUS_FILE} must contain a JSON array."
    assert len(corpus) == 31, f"Expected 31 corpus records in {CORPUS_FILE}, found {len(corpus)}."
    for record in corpus:
        assert isinstance(record, dict), f"Corpus record is not an object: {record!r}"
        assert set(record.keys()) == CORPUS_KEYS, (
            f"Corpus record {record!r} does not have exactly the keys {sorted(CORPUS_KEYS)}."
        )
    slugs = [record["slug"] for record in corpus]
    assert len(set(slugs)) == len(slugs), "Corpus contains duplicated slugs."


def test_corpus_checksum_fixture_recorded():
    checksum_file = os.path.join(FIXTURE_DIR, "corpus.sha256")
    assert os.path.isfile(checksum_file), (
        f"Expected the recorded corpus checksum {checksum_file} to exist."
    )
    assert open(checksum_file).read().strip(), f"{checksum_file} is empty."


def test_gel_client_library_not_installed():
    probe = run(["python3", "-c", "import gel"])
    assert probe.returncode != 0, (
        "A Gel client library is importable; this task must be solved with the 'gel' CLI only."
    )


def test_database_has_no_article_type(gel_server):
    proc = run(
        [
            "gel",
            "query",
            "-F",
            "json",
            "select count(schema::ObjectType filter .name = 'default::Article')",
        ],
        cwd=PROJECT_DIR,
    )
    assert proc.returncode == 0, (
        f"Failed to introspect the database schema: stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert json.loads(proc.stdout) == [0], (
        f"Expected no 'default::Article' object type in the initial database, got {proc.stdout!r}"
    )


def test_database_has_no_migration_history(gel_server):
    proc = run(
        ["gel", "query", "-F", "json", "select count(schema::Migration)"],
        cwd=PROJECT_DIR,
    )
    assert proc.returncode == 0, (
        f"Failed to query the migration history: stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert json.loads(proc.stdout) == [0], (
        f"Expected an empty migration history in the initial database, got {proc.stdout!r}"
    )
