import json
import os
import shutil
import subprocess
import time

import gel
import pytest

PROJECT_DIR = "/home/user/socialgraph"
GEL_TOML = os.path.join(PROJECT_DIR, "gel.toml")
DBSCHEMA_DIR = os.path.join(PROJECT_DIR, "dbschema")
DATA_DIR = os.path.join(PROJECT_DIR, "data")
SEED_FILE = os.path.join(DATA_DIR, "seed.json")
GRAPH_PY = os.path.join(PROJECT_DIR, "graph.py")

GEL_START = "gel-start"
EXPECTED_MEMBERS = 200
EXPECTED_CONNECTIONS = 1914
CONNECTION_KEYS = {"from", "to", "weight", "role", "established", "confirmed"}


def _start_server() -> None:
    """Make sure the local Gel server is running. Safe to call repeatedly."""
    binary = shutil.which(GEL_START)
    if binary is not None:
        try:
            subprocess.run([binary], capture_output=True, text=True, timeout=1200)
        except subprocess.TimeoutExpired:
            pass


def _wait_for_client(deadline_seconds: int = 600) -> gel.Client:
    last_error = None
    deadline = time.time() + deadline_seconds
    while time.time() < deadline:
        try:
            candidate = gel.create_client(timeout=120)
            candidate.query_single("select 1")
            return candidate
        except Exception as exc:  # noqa: BLE001 - server may still be booting
            last_error = exc
            time.sleep(3)
    raise AssertionError(
        "The local Gel server never became ready for queries. Last error: %r" % (last_error,)
    )


@pytest.fixture(scope="session")
def client():
    _start_server()
    connection = _wait_for_client()
    try:
        yield connection
    finally:
        try:
            connection.close()
        except Exception:  # noqa: BLE001 - best effort cleanup
            pass


def test_gel_cli_available():
    assert shutil.which("gel") is not None, "The `gel` CLI was not found in PATH."


def test_gel_start_helper_available():
    assert shutil.which(GEL_START) is not None, (
        "The `gel-start` helper (which starts the local Gel server) was not found in PATH."
    )


def test_gel_python_client_importable():
    assert hasattr(gel, "create_client"), (
        "The Gel Python client is installed but `gel.create_client` is missing."
    )


def test_connection_settings_exported():
    assert os.environ.get("GEL_HOST"), (
        "GEL_HOST is not exported in the environment, so clients cannot find the local server."
    )
    assert os.environ.get("GEL_PORT"), (
        "GEL_PORT is not exported in the environment, so clients cannot find the local server."
    )


def test_project_directory_exists():
    assert os.path.isdir(PROJECT_DIR), f"Project directory {PROJECT_DIR} does not exist."


def test_gel_toml_exists():
    assert os.path.isfile(GEL_TOML), f"Expected the project manifest {GEL_TOML} to exist."


def test_dbschema_directory_exists_and_is_empty():
    assert os.path.isdir(DBSCHEMA_DIR), f"Expected the directory {DBSCHEMA_DIR} to exist."
    entries = sorted(os.listdir(DBSCHEMA_DIR))
    assert entries == [], (
        f"Expected {DBSCHEMA_DIR} to start out empty, but it contains: {entries}"
    )


def test_seed_file_exists():
    assert os.path.isfile(SEED_FILE), f"Expected the seed dataset {SEED_FILE} to exist."


def test_seed_file_structure():
    with open(SEED_FILE, encoding="utf-8") as handle:
        payload = json.load(handle)

    assert isinstance(payload, dict), f"{SEED_FILE} must contain a JSON object."
    assert set(payload) == {"members", "connections"}, (
        f"{SEED_FILE} must have exactly the top-level keys 'members' and 'connections', "
        f"found {sorted(payload)}."
    )

    members = payload["members"]
    connections = payload["connections"]
    assert len(members) == EXPECTED_MEMBERS, (
        f"Expected {EXPECTED_MEMBERS} members in {SEED_FILE}, found {len(members)}."
    )
    assert len(connections) == EXPECTED_CONNECTIONS, (
        f"Expected {EXPECTED_CONNECTIONS} connections in {SEED_FILE}, "
        f"found {len(connections)}."
    )

    handles = [member["handle"] for member in members]
    assert len(set(handles)) == len(handles), f"Handles in {SEED_FILE} are not unique."
    for member in members:
        assert set(member) == {"handle", "display_name"}, (
            f"Unexpected member entry keys in {SEED_FILE}: {sorted(member)}"
        )

    known = set(handles)
    seen_pairs = set()
    for entry in connections:
        assert set(entry) == CONNECTION_KEYS, (
            f"Unexpected connection entry keys in {SEED_FILE}: {sorted(entry)}"
        )
        assert entry["from"] in known and entry["to"] in known, (
            f"Connection {entry['from']} -> {entry['to']} references an unknown handle."
        )
        assert entry["from"] != entry["to"], "The seed dataset must not contain self edges."
        assert isinstance(entry["weight"], int) and 1 <= entry["weight"] <= 100, (
            f"Connection {entry['from']} -> {entry['to']} has an out-of-range weight."
        )
        assert isinstance(entry["confirmed"], bool), (
            f"Connection {entry['from']} -> {entry['to']} has a non-boolean 'confirmed'."
        )
        pair = (entry["from"], entry["to"])
        assert pair not in seen_pairs, f"Duplicate connection entry for {pair} in {SEED_FILE}."
        seen_pairs.add(pair)


def test_graph_entrypoint_not_created_yet():
    assert not os.path.exists(GRAPH_PY), (
        f"{GRAPH_PY} already exists; the executor is supposed to create it."
    )


def test_server_answers_queries(client):
    assert client.query_single("select 1") == 1, "The local Gel server did not answer 'select 1'."


def test_server_version_is_gel_7_1(client):
    version = json.loads(client.query_single_json("select sys::get_version()"))
    assert version["major"] == 7, f"Expected a Gel 7 server, got major version {version['major']}."
    assert version["minor"] == 1, f"Expected Gel 7.1, got minor version {version['minor']}."


def test_current_branch_is_main(client):
    branch = client.query_single("select sys::get_current_branch()")
    assert branch == "main", f"Expected to be connected to branch 'main', got {branch!r}."


def test_gel_cli_can_query_the_server(client):
    binary = shutil.which("gel")
    assert binary is not None, "The `gel` CLI was not found in PATH."
    proc = subprocess.run(
        [binary, "query", "select 1"],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=PROJECT_DIR,
    )
    assert proc.returncode == 0, (
        f"`gel query 'select 1'` failed with exit code {proc.returncode}: {proc.stderr}"
    )
    assert "1" in proc.stdout, f"Unexpected output from the `gel` CLI: {proc.stdout!r}"


def test_default_module_has_no_object_types_yet(client):
    rows = json.loads(
        client.query_json(
            "select schema::ObjectType { name } filter .name like 'default::%'"
        )
    )
    names = sorted(row["name"] for row in rows)
    assert names == [], (
        f"The 'default' module should start out empty, but it already contains: {names}"
    )


def test_no_members_exist_yet(client):
    count = client.query_single(
        """
        select count(schema::ObjectType filter .name = 'default::Member')
        """
    )
    assert count == 0, "A 'default::Member' object type already exists in the database."
