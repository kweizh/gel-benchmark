"""Initial-state verification for the gel_group_by_window_analytics_py task.

Validates the environment that exists BEFORE the executor starts working:
the Gel 7 server/CLI, the pre-existing project at /home/user/analytics, the
already-migrated Category/Sale schema, the seeded data and the refunds file.
"""

import json
import os
import shutil
import subprocess

import pytest

PROJECT_DIR = "/home/user/analytics"
SCHEMA_FILE = os.path.join(PROJECT_DIR, "dbschema", "default.gel")
MIGRATIONS_DIR = os.path.join(PROJECT_DIR, "dbschema", "migrations")
REFUNDS_FILE = os.path.join(PROJECT_DIR, "data", "refunds.json")
GEL_START = "/usr/local/bin/gel-start"

VALID_CHANNELS = {"web", "retail", "partner"}


@pytest.fixture(scope="session")
def gel_server():
    """Make sure the local Gel server is up.

    Every test that talks to the database (directly or through the ``gel``
    CLI) must request this fixture, otherwise it may run before the server
    finished starting and fail with ``Connection refused``.
    """
    assert os.path.isfile(GEL_START), (
        f"Server bootstrap helper {GEL_START} is missing from the image."
    )
    proc = subprocess.run(
        [GEL_START],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, (
        "Failed to start the local Gel server.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    return True


def _gel_query_json(query):
    proc = subprocess.run(
        ["gel", "query", "--output-format", "json", query],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, (
        f"`gel query` failed for {query!r}.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    return json.loads(proc.stdout)


def _gel_query_single(query):
    """`gel query --output-format json` always yields an array; unwrap it."""
    result = _gel_query_json(query)
    assert isinstance(result, list) and len(result) == 1, (
        f"Expected a single-element result set for {query!r}, got {result!r}."
    )
    return result[0]


# ---------------------------------------------------------------------------
# Tooling
# ---------------------------------------------------------------------------


def test_gel_cli_available():
    assert shutil.which("gel") is not None, "The `gel` CLI was not found in PATH."


def test_gel_python_client_importable():
    import gel  # noqa: F401

    assert gel is not None, "The `gel` Python package could not be imported."


def test_python3_available():
    assert shutil.which("python3") is not None, "python3 was not found in PATH."


def test_connection_environment_variables_present():
    assert os.environ.get("GEL_DSN"), (
        "GEL_DSN is not exported in the environment; clients cannot resolve the "
        "local Gel instance."
    )
    assert os.environ.get("GEL_CLIENT_SECURITY"), (
        "GEL_CLIENT_SECURITY is not exported in the environment."
    )


# ---------------------------------------------------------------------------
# Project layout
# ---------------------------------------------------------------------------


def test_project_directory_exists():
    assert os.path.isdir(PROJECT_DIR), f"Project directory {PROJECT_DIR} does not exist."


def test_schema_file_exists_with_seed_types():
    assert os.path.isfile(SCHEMA_FILE), f"Schema file {SCHEMA_FILE} does not exist."
    with open(SCHEMA_FILE, encoding="utf-8") as handle:
        content = handle.read()
    assert "module default" in content, (
        f"{SCHEMA_FILE} does not declare `module default`."
    )
    assert "type Category" in content, f"{SCHEMA_FILE} does not declare `type Category`."
    assert "type Sale" in content, f"{SCHEMA_FILE} does not declare `type Sale`."


def test_migrations_directory_has_initial_migration():
    assert os.path.isdir(MIGRATIONS_DIR), (
        f"Migrations directory {MIGRATIONS_DIR} does not exist."
    )
    migrations = [n for n in os.listdir(MIGRATIONS_DIR) if n.endswith(".edgeql")]
    assert len(migrations) >= 1, (
        f"Expected at least one applied migration file in {MIGRATIONS_DIR}, "
        f"found: {sorted(os.listdir(MIGRATIONS_DIR))}"
    )


def test_refunds_file_exists_and_is_well_formed():
    assert os.path.isfile(REFUNDS_FILE), f"Refunds file {REFUNDS_FILE} does not exist."
    with open(REFUNDS_FILE, encoding="utf-8") as handle:
        records = json.load(handle)
    assert isinstance(records, list), f"{REFUNDS_FILE} must contain a JSON array."
    assert len(records) > 0, f"{REFUNDS_FILE} must not be empty."
    expected_keys = {"external_id", "order_ref", "amount_cents", "refunded_at"}
    for record in records:
        assert isinstance(record, dict), (
            f"Every entry of {REFUNDS_FILE} must be a JSON object, got {type(record)}."
        )
        assert set(record.keys()) == expected_keys, (
            f"Refund record {record!r} does not have exactly the keys {sorted(expected_keys)}."
        )


def test_analytics_package_not_prepopulated():
    """The executor is expected to author the package; it must not already exist."""
    rollups = os.path.join(PROJECT_DIR, "analytics", "rollups.py")
    assert not os.path.exists(rollups), (
        f"{rollups} already exists; the starting environment must not contain the solution."
    )


# ---------------------------------------------------------------------------
# Database state
# ---------------------------------------------------------------------------


def test_migration_history_is_in_sync(gel_server):
    proc = subprocess.run(
        ["gel", "migration", "status"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, (
        "`gel migration status` reported that the branch is not in sync before the "
        f"task starts.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


def test_seed_types_exist_in_database(gel_server):
    names = _gel_query_json(
        "select schema::ObjectType { name } "
        "filter .name in {'default::Category', 'default::Sale'}"
    )
    found = sorted(item["name"] for item in names)
    assert found == ["default::Category", "default::Sale"], (
        f"Expected default::Category and default::Sale in the database, found {found}."
    )


def test_categories_are_seeded(gel_server):
    count = _gel_query_single("select count(Category)")
    assert isinstance(count, int) and count > 0, (
        f"Expected the database to be seeded with Category objects, got count={count!r}."
    )


def test_sales_are_seeded(gel_server):
    count = _gel_query_single("select count(Sale)")
    assert isinstance(count, int) and count > 0, (
        f"Expected the database to be seeded with Sale objects, got count={count!r}."
    )


def test_sales_use_only_the_documented_channels(gel_server):
    channels = _gel_query_json("select distinct Sale.channel")
    assert set(channels) <= VALID_CHANNELS, (
        f"Seeded sales use unexpected channels: {sorted(set(channels) - VALID_CHANNELS)}."
    )


def test_sale_has_the_documented_properties(gel_server):
    pointers = _gel_query_json(
        "select schema::ObjectType { pointers: { name } } filter .name = 'default::Sale'"
    )
    assert pointers, "default::Sale was not found during schema introspection."
    names = {p["name"] for p in pointers[0]["pointers"]}
    for expected in (
        "order_ref",
        "occurred_at",
        "amount_cents",
        "units",
        "channel",
        "category",
    ):
        assert expected in names, (
            f"default::Sale is missing the pre-existing pointer {expected!r}; found {sorted(names)}."
        )


def test_refund_type_absent_before_the_task(gel_server):
    found = _gel_query_json(
        "select schema::ObjectType { name } filter .name = 'default::Refund'"
    )
    assert found == [], (
        "default::Refund already exists in the starting environment; the executor is "
        "supposed to create it."
    )
