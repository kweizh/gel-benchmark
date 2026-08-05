"""Validate the initial state of the gel_multitenant_access_policies_py environment.

These checks run BEFORE the executor starts working. They assert that the
pre-existing project skeleton, the shipped dataset and a startable Gel 7.1
server are all in place, and that the task has not already been solved.
"""

import json
import os
import shutil
import subprocess

import pytest

PROJECT_DIR = "/home/user/tenantdesk"
APP_PATH = os.path.join(PROJECT_DIR, "app.py")
SCHEMA_PATH = os.path.join(PROJECT_DIR, "dbschema", "default.gel")
DATASET_PATH = os.path.join(PROJECT_DIR, "seed", "dataset.json")

EXPECTED_TENANTS = {
    "acme": "Acme Industrial",
    "globex": "Globex Systems",
    "initech": "Initech Holdings",
}

EXPECTED_ACTORS = {
    "ava@acme.example": ("acme", "admin"),
    "ben@acme.example": ("acme", "agent"),
    "cleo@acme.example": ("acme", "readonly"),
    "dan@globex.example": ("globex", "admin"),
    "eve@globex.example": ("globex", "agent"),
    "fay@initech.example": ("initech", "admin"),
    "gus@initech.example": ("initech", "readonly"),
}

EXPECTED_TICKET_COUNTS = {"acme": 300, "globex": 200, "initech": 100}


@pytest.fixture(scope="session")
def gel_server():
    """Start the local Gel server (idempotent) and wait until it is ready."""
    gel_up = shutil.which("gel-up")
    assert gel_up is not None, "The 'gel-up' helper is not available in PATH."
    proc = subprocess.run(
        [gel_up],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, (
        "'gel-up' failed to bring the local Gel server up.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    return True


@pytest.fixture(scope="session")
def client(gel_server):
    """A Gel client connected to the local instance."""
    import gel

    handle = gel.create_client()
    try:
        handle.ensure_connected()
        yield handle
    finally:
        handle.close()


def test_gel_cli_available():
    assert shutil.which("gel") is not None, "The 'gel' CLI is not available in PATH."


def test_python3_available():
    assert shutil.which("python3") is not None, "'python3' is not available in PATH."


def test_gel_python_package_importable():
    import gel

    assert hasattr(gel, "create_client"), (
        "The 'gel' Python package is installed but does not expose create_client()."
    )


def test_gel_up_helper_available():
    assert shutil.which("gel-up") is not None, (
        "The 'gel-up' helper that starts the local Gel server is not available in PATH."
    )


def test_connection_settings_present_in_environment():
    assert os.environ.get("GEL_DSN"), "GEL_DSN is not set in the environment."
    assert os.environ.get("GEL_CLIENT_TLS_SECURITY"), (
        "GEL_CLIENT_TLS_SECURITY is not set in the environment."
    )


def test_project_directory_exists():
    assert os.path.isdir(PROJECT_DIR), f"Project directory {PROJECT_DIR} does not exist."


def test_app_entrypoint_exists():
    assert os.path.isfile(APP_PATH), f"CLI entry point {APP_PATH} does not exist."
    assert os.path.getsize(APP_PATH) > 0, f"CLI entry point {APP_PATH} is empty."


def test_schema_file_exists():
    assert os.path.isfile(SCHEMA_PATH), f"Schema file {SCHEMA_PATH} does not exist."
    assert os.path.getsize(SCHEMA_PATH) > 0, f"Schema file {SCHEMA_PATH} is empty."


def test_schema_file_declares_the_contract_types():
    with open(SCHEMA_PATH, encoding="utf-8") as handle:
        content = handle.read()
    for name in ("module default", "Tenant", "Actor", "Ticket"):
        assert name in content, f"Expected {SCHEMA_PATH} to mention '{name}'."


def test_dataset_file_exists():
    assert os.path.isfile(DATASET_PATH), f"Dataset file {DATASET_PATH} does not exist."


def test_dataset_tenants():
    with open(DATASET_PATH, encoding="utf-8") as handle:
        data = json.load(handle)
    tenants = data.get("tenants")
    assert isinstance(tenants, list), "Dataset key 'tenants' must be a list."
    assert len(tenants) == 3, f"Expected 3 tenants in the dataset, found {len(tenants)}."
    found = {entry["slug"]: entry["name"] for entry in tenants}
    assert found == EXPECTED_TENANTS, f"Unexpected tenants in the dataset: {found}."


def test_dataset_actors():
    with open(DATASET_PATH, encoding="utf-8") as handle:
        data = json.load(handle)
    actors = data.get("actors")
    assert isinstance(actors, list), "Dataset key 'actors' must be a list."
    assert len(actors) == 7, f"Expected 7 actors in the dataset, found {len(actors)}."
    found = {entry["email"]: (entry["tenant"], entry["role"]) for entry in actors}
    assert found == EXPECTED_ACTORS, f"Unexpected actors in the dataset: {found}."


def test_dataset_tickets():
    with open(DATASET_PATH, encoding="utf-8") as handle:
        data = json.load(handle)
    tickets = data.get("tickets")
    assert isinstance(tickets, list), "Dataset key 'tickets' must be a list."
    assert len(tickets) == 600, (
        f"Expected 600 tickets in the dataset, found {len(tickets)}."
    )
    per_tenant = {}
    for entry in tickets:
        for key in ("ref", "subject", "status", "tenant"):
            assert key in entry, f"Dataset ticket entry is missing '{key}': {entry}."
        assert entry["status"] in ("open", "pending", "closed"), (
            f"Unexpected ticket status in the dataset: {entry}."
        )
        per_tenant[entry["tenant"]] = per_tenant.get(entry["tenant"], 0) + 1
    assert per_tenant == EXPECTED_TICKET_COUNTS, (
        f"Unexpected ticket distribution in the dataset: {per_tenant}."
    )
    refs = {entry["ref"] for entry in tickets}
    for ref in ("ACME-0001", "ACME-0300", "GLOBEX-0200", "INITECH-0100"):
        assert ref in refs, f"Expected the dataset to contain the ticket ref {ref}."


def test_gel_server_is_reachable(client):
    result = client.query_single("select 1")
    assert result == 1, "The local Gel instance did not answer a trivial query."


def test_gel_cli_can_reach_the_instance(client):
    proc = subprocess.run(
        ["gel", "query", "select 1"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        "'gel query' could not reach the local instance.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


def test_database_has_no_task_schema_yet(client):
    rows = client.query(
        """
        select schema::ObjectType { name }
        filter .name in {
            'default::Tenant', 'default::Actor', 'default::Ticket'
        }
        """
    )
    names = [row.name for row in rows]
    assert names == [], (
        f"The database already defines the task's object types: {names}."
    )


def test_cli_does_not_implement_the_new_interface_yet():
    proc = subprocess.run(
        ["python3", APP_PATH, "whoami", "--actor", "ava@acme.example"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=PROJECT_DIR,
    )
    assert proc.returncode != 0, (
        "The shipped CLI already implements the 'whoami' subcommand; "
        "the task appears to be pre-solved."
    )
