import glob
import json
import os
import shutil
import subprocess

import pytest

PROJECT_DIR = "/home/user/functions-lab"
GEL_TOML = os.path.join(PROJECT_DIR, "gel.toml")
DBSCHEMA_DIR = os.path.join(PROJECT_DIR, "dbschema")
MIGRATIONS_DIR = os.path.join(DBSCHEMA_DIR, "migrations")
SEED_FILE = os.path.join(PROJECT_DIR, "seed", "data.json")
QUOTE_SCRIPT = os.path.join(PROJECT_DIR, "quote.sh")
START_HELPER = "/usr/local/bin/gel-start"


def _run(args, cwd=None, timeout=180):
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _gel_json(query, cwd=None):
    proc = _run(["gel", "query", "-F", "json", query], cwd=cwd)
    assert proc.returncode == 0, (
        f"'gel query' failed for {query!r}:\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    return json.loads(proc.stdout)


@pytest.fixture(scope="session")
def client():
    """Ensure the local Gel instance is up before any database-dependent check."""
    probe = _run(["gel", "query", "-F", "json", "select 1"])
    if probe.returncode != 0:
        assert os.path.isfile(START_HELPER), (
            f"Gel is not reachable and the start helper {START_HELPER} is missing."
        )
        started = _run(["bash", START_HELPER], timeout=300)
        assert started.returncode == 0, (
            f"Failed to start the local Gel instance:\n{started.stdout}\n{started.stderr}"
        )
    probe = _run(["gel", "query", "-F", "json", "select 1"])
    assert probe.returncode == 0, (
        f"Local Gel instance is not reachable:\nstdout={probe.stdout}\nstderr={probe.stderr}"
    )
    return True


def test_gel_cli_available():
    assert shutil.which("gel") is not None, "The 'gel' CLI was not found in PATH."


def test_gel_server_binary_available():
    candidates = [c for c in glob.glob("/usr/bin/gel-server*") if os.access(c, os.X_OK)]
    assert candidates, "No executable gel-server binary was found under /usr/bin."


def test_project_directory_exists():
    assert os.path.isdir(PROJECT_DIR), f"Project directory {PROJECT_DIR} does not exist."


def test_gel_toml_exists():
    assert os.path.isfile(GEL_TOML), f"Expected the project manifest {GEL_TOML} to exist."


def test_dbschema_directory_exists():
    assert os.path.isdir(DBSCHEMA_DIR), f"Expected the schema directory {DBSCHEMA_DIR} to exist."


def test_initial_migration_file_present():
    migrations = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql")))
    assert len(migrations) >= 1, (
        f"Expected at least one applied migration file in {MIGRATIONS_DIR}, found {migrations}."
    )


def test_seed_data_file_present_and_shaped():
    assert os.path.isfile(SEED_FILE), f"Expected the seed data file {SEED_FILE} to exist."
    with open(SEED_FILE) as handle:
        data = json.load(handle)
    assert isinstance(data, dict), f"{SEED_FILE} must contain a JSON object."
    assert set(data) == {"parcels", "shipments"}, (
        f"{SEED_FILE} must have exactly the keys 'parcels' and 'shipments', got {sorted(data)}."
    )
    assert len(data["parcels"]) == 4, (
        f"Expected 4 seed parcels in {SEED_FILE}, found {len(data['parcels'])}."
    )
    assert len(data["shipments"]) == 2, (
        f"Expected 2 seed shipments in {SEED_FILE}, found {len(data['shipments'])}."
    )


def test_quote_script_not_created_yet():
    assert not os.path.exists(QUOTE_SCRIPT), (
        f"{QUOTE_SCRIPT} already exists; the task must start without the report command."
    )


def test_gel_instance_version_and_branch(client):
    version = _gel_json("select sys::get_version_as_str()")[0]
    assert version.startswith("6.11"), f"Expected a Gel 6.11 instance, got version {version!r}."
    branch = _gel_json("select sys::get_current_branch()")[0]
    assert branch == "main", f"Expected the active branch to be 'main', got {branch!r}."


def test_existing_carrier_rows_present(client):
    rows = _gel_json("select Carrier { name, hub_code } order by .name")
    assert rows == [
        {"name": "Halcyon", "hub_code": "SEA"},
        {"name": "Northwind", "hub_code": "PDX"},
    ], f"Expected the two pre-existing Carrier rows, got {rows}."


def test_migration_history_starts_in_sync(client):
    proc = _run(["gel", "migration", "status"], cwd=PROJECT_DIR)
    combined = (proc.stdout + proc.stderr).lower()
    assert proc.returncode == 0 and "up to date" in combined, (
        f"Expected the initial migration history to be in sync:\n{proc.stdout}\n{proc.stderr}"
    )


def test_logistics_module_not_present_yet(client):
    functions = _gel_json(
        "select schema::Function { name } filter .name like 'logistics::%'"
    )
    assert functions == [], f"Module 'logistics' already declares functions: {functions}."
    scalars = _gel_json(
        "select schema::ScalarType { name } filter .name like 'logistics::%'"
    )
    assert scalars == [], f"Module 'logistics' already declares scalar types: {scalars}."
    constraints = _gel_json(
        "select schema::Constraint { name } filter .name like 'logistics::%'"
    )
    assert constraints == [], f"Module 'logistics' already declares constraints: {constraints}."


def test_domain_types_not_present_yet(client):
    types = _gel_json(
        "select schema::ObjectType { name } "
        "filter .name in {'default::Parcel', 'default::Shipment'}"
    )
    assert types == [], f"Types Parcel/Shipment already exist: {types}."
