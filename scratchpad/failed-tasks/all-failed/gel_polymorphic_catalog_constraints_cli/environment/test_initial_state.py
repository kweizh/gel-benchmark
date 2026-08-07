import glob
import json
import os
import shutil
import subprocess

import pytest

PROJECT_DIR = "/home/user/catalog"
SCHEMA_DIR = os.path.join(PROJECT_DIR, "dbschema")
MIGRATIONS_DIR = os.path.join(SCHEMA_DIR, "migrations")
SNAPSHOT_PATH = "/opt/task/initial_state.json"


def _run(args, cwd=PROJECT_DIR, timeout=120):
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.fixture(scope="session", autouse=True)
def gel_server():
    """Make sure the bundled local Gel server is up before any DB access."""
    proc = _run(["gel-start"], cwd="/")
    assert proc.returncode == 0, (
        "gel-start failed to bring up the local Gel server.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    return True


def query_json(edgeql):
    proc = _run(["gel", "query", "-F", "json", edgeql])
    assert proc.returncode == 0, (
        f"gel query failed for {edgeql!r}.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    data = json.loads(proc.stdout)
    return data if isinstance(data, list) else [data]


def test_gel_cli_available():
    assert shutil.which("gel") is not None, "The `gel` CLI binary was not found in PATH."


def test_gel_start_helper_available():
    assert shutil.which("gel-start") is not None, (
        "The `gel-start` helper used to bring up the local Gel server was not found in PATH."
    )


def test_python_test_tooling_available():
    assert shutil.which("python3") is not None, "python3 was not found in PATH."


def test_project_directory_exists():
    assert os.path.isdir(PROJECT_DIR), f"Project directory {PROJECT_DIR} does not exist."


def test_schema_directory_and_schema_file_exist():
    assert os.path.isdir(SCHEMA_DIR), f"Schema directory {SCHEMA_DIR} does not exist."
    schema_files = glob.glob(os.path.join(SCHEMA_DIR, "*.gel"))
    assert schema_files, f"No .gel schema file found in {SCHEMA_DIR}."


def test_two_migrations_present_on_disk():
    assert os.path.isdir(MIGRATIONS_DIR), (
        f"Migrations directory {MIGRATIONS_DIR} does not exist."
    )
    files = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql")))
    assert len(files) == 2, (
        f"Expected exactly 2 pre-existing migration files in {MIGRATIONS_DIR}, found {len(files)}: {files}"
    )


def test_initial_state_snapshot_exists_and_is_consistent():
    assert os.path.isfile(SNAPSHOT_PATH), (
        f"Reference snapshot {SNAPSHOT_PATH} is missing from the image."
    )
    with open(SNAPSHOT_PATH) as handle:
        snapshot = json.load(handle)
    assert isinstance(snapshot.get("migrations"), list) and len(snapshot["migrations"]) == 2, (
        "Snapshot must record exactly the 2 migration names applied before the task starts."
    )
    products = snapshot.get("products")
    assert isinstance(products, list) and len(products) == 12, (
        f"Snapshot must record exactly 12 seeded products, found {products if products is None else len(products)}."
    )
    for product in products:
        for key in ("id", "sku", "title", "price_cents", "stock", "kind"):
            assert key in product, f"Snapshot product entry is missing the key {key!r}: {product}"
        assert " | " in product["title"], (
            f"Snapshot title {product['title']!r} must contain the ' | ' separator."
        )


def test_migration_history_starts_in_sync(gel_server):
    proc = _run(["gel", "migration", "status", "--quiet"])
    assert proc.returncode == 0, (
        "The pre-existing migration history is not in sync with the database.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


def test_two_migrations_applied_in_database(gel_server):
    count = query_json("select count((select schema::Migration filter not .builtin))")[0]
    assert count == 2, f"Expected 2 migrations recorded in the database, found {count}."


def test_seeded_products_present(gel_server):
    count = query_json("select count(Product)")[0]
    assert count == 12, f"Expected 12 seeded Product rows in the database, found {count}."


def test_initial_types_are_the_weak_model(gel_server):
    rows = query_json(
        "select schema::ObjectType { name, abstract } "
        "filter .name in {'default::Product', 'default::Book', 'default::Apparel', "
        "'default::DigitalDownload'}"
    )
    by_name = {row["name"]: row for row in rows}
    assert "default::Product" in by_name, "Object type default::Product is missing."
    assert by_name["default::Product"]["abstract"] is True, (
        "default::Product must already be an abstract type."
    )
    for name in ("default::Book", "default::Apparel"):
        assert name in by_name, f"Object type {name} is missing from the initial schema."
    assert "default::DigitalDownload" not in by_name, (
        "default::DigitalDownload must NOT exist yet; the executor has to create it."
    )


def test_initial_product_properties(gel_server):
    rows = query_json(
        "select schema::ObjectType { pointers: { name, expr } } "
        "filter .name = 'default::Product'"
    )
    assert rows, "Could not introspect default::Product."
    pointers = {p["name"]: p for p in rows[0]["pointers"]}
    for name in ("sku", "title", "price_cents", "stock"):
        assert name in pointers, (
            f"Property {name!r} is expected on default::Product in the initial schema; "
            f"found {sorted(pointers)}."
        )
    assert pointers["title"].get("expr") in (None, ""), (
        "In the initial schema `title` must be a plain stored property, not a computed one."
    )
    for name in ("brand", "name", "units_in_stock", "listing_status", "discount_cents"):
        assert name not in pointers, (
            f"Property {name!r} must NOT exist yet on default::Product; the executor has to add it."
        )
