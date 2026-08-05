import glob
import os
import shutil
import subprocess
import time

import gel
import pytest

PROJECT_DIR = "/home/user/catalog"
INSTANCE_NAME = "geltask"

os.environ["HOME"] = "/root"
os.environ["GEL_INSTANCE"] = INSTANCE_NAME


def _run(args, cwd=None, timeout=300):
    return subprocess.run(
        args,
        cwd=cwd,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.fixture(scope="session")
def client():
    """Start the local Gel instance (idempotent) and return a connected client."""
    started = _run(["gel-start-instance"])
    assert started.returncode == 0, (
        "`gel-start-instance` failed to bring the local Gel instance up: "
        f"stdout={started.stdout!r} stderr={started.stderr!r}"
    )

    last_error = None
    for _ in range(20):
        try:
            c = gel.create_client(timeout=15)
            c.query_single("select 1")
            yield c
            c.close()
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(2)
    raise AssertionError(
        f"Could not connect to the local Gel instance '{INSTANCE_NAME}': {last_error}"
    )


def test_gel_cli_available():
    assert shutil.which("gel") is not None, "The `gel` CLI binary was not found in PATH."


def test_gel_python_client_importable():
    assert hasattr(gel, "create_async_client"), (
        "The installed `gel` Python package does not expose `create_async_client`."
    )


def test_project_directory_exists():
    assert os.path.isdir(PROJECT_DIR), f"Project directory {PROJECT_DIR} does not exist."


def test_gel_toml_exists():
    path = os.path.join(PROJECT_DIR, "gel.toml")
    assert os.path.isfile(path), f"Expected the Gel project manifest {path} to exist."


def test_schema_file_declares_supplier():
    path = os.path.join(PROJECT_DIR, "dbschema", "default.gel")
    assert os.path.isfile(path), f"Expected the schema file {path} to exist."
    content = path and open(path, encoding="utf-8").read()
    assert "Supplier" in content, (
        "The initial schema file dbschema/default.gel should declare the `Supplier` type."
    )


def test_exactly_one_initial_migration():
    migrations = sorted(
        glob.glob(os.path.join(PROJECT_DIR, "dbschema", "migrations", "*.edgeql"))
    )
    assert len(migrations) == 1, (
        "Expected exactly one pre-existing migration file under "
        f"{PROJECT_DIR}/dbschema/migrations, found {len(migrations)}: {migrations}"
    )


def test_ingestion_package_not_created_yet():
    path = os.path.join(PROJECT_DIR, "catalog_ingest")
    assert not os.path.exists(path), (
        f"{path} already exists; the executor is supposed to create the ingestion package."
    )


def test_instance_reachable_and_supplier_type_exists(client):
    names = client.query(
        "select schema::ObjectType { name } filter .name = 'default::Supplier'"
    )
    assert len(names) == 1, (
        "The object type `default::Supplier` should already exist in the linked instance."
    )


def test_supplier_has_exclusive_code(client):
    props = client.query(
        """
        select schema::ObjectType { properties: { name } }
        filter .name = 'default::Supplier'
        """
    )
    assert props, "Could not introspect `default::Supplier`."
    prop_names = {p.name for p in props[0].properties}
    assert {"code", "name"}.issubset(prop_names), (
        f"`default::Supplier` should have `code` and `name` properties, found: {sorted(prop_names)}"
    )


def test_product_type_does_not_exist_yet(client):
    found = client.query(
        "select schema::ObjectType { name } filter .name = 'default::Product'"
    )
    assert len(found) == 0, (
        "The object type `default::Product` already exists; the executor is supposed to add it."
    )


def test_migration_status_is_in_sync(client):
    proc = _run(["gel", "migration", "status"], cwd=PROJECT_DIR)
    combined = (proc.stdout + proc.stderr).lower()
    assert proc.returncode == 0, (
        "`gel migration status` should succeed in the initial project state. "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert "up to date" in combined, (
        f"The project should start in sync with the instance, got: {combined!r}"
    )
