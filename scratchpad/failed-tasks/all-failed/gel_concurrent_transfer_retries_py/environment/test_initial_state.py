"""Initial-state checks for the gel_concurrent_transfer_retries_py task.

These tests run BEFORE the executor starts working. They validate that the
container ships a running-able Gel 7.1 server, the pre-existing project
skeleton at /home/user/ledger, and the seeded Account data - and that the
artifacts the executor is expected to produce do not exist yet.
"""

import json
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request

import pytest

PROJECT_DIR = "/home/user/ledger"
GEL_TOML = os.path.join(PROJECT_DIR, "gel.toml")
SCHEMA_FILE = os.path.join(PROJECT_DIR, "dbschema", "default.gel")
START_SCRIPT = os.path.join(PROJECT_DIR, "start.sh")
GEL_START_SCRIPT = "/usr/local/bin/start-gel.sh"

READY_URL = "http://localhost:5656/server/status/ready"
APP_PORT = 8080

SEEDED_ACC_PREFIX = "ACC-"
SEEDED_ACC_COUNT = 1000
SEEDED_ACC_TOTAL = 250000000
RESERVED_ACCOUNTS = {
    "RSV-AUDIT-1": 900000,
    "RSV-AUDIT-2": 125000,
    "RSV-AUDIT-3": 0,
    "RSV-AUDIT-4": 7,
}


def _server_ready(timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(READY_URL, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


@pytest.fixture(scope="session")
def gel_server():
    """Start the bundled Gel server (idempotent) and wait until it is ready."""
    if not _server_ready():
        proc = subprocess.run(
            ["bash", GEL_START_SCRIPT],
            capture_output=True,
            text=True,
            timeout=900,
        )
        if proc.returncode != 0 and not _server_ready():
            pytest.fail(
                "Failed to start the local Gel server via "
                f"{GEL_START_SCRIPT}: rc={proc.returncode}\n"
                f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
            )

    deadline = time.time() + 600
    while time.time() < deadline:
        if _server_ready():
            break
        time.sleep(2)
    else:
        pytest.fail(
            "The local Gel server did not become ready within 600s "
            f"(polled {READY_URL})."
        )
    return True


@pytest.fixture(scope="session")
def client(gel_server):
    """A connected Gel client (also guarantees the server is up)."""
    import gel

    last_error = None
    deadline = time.time() + 180
    while time.time() < deadline:
        try:
            conn = gel.create_client(timeout=120)
            conn.query_single("select 1")
            break
        except Exception as exc:  # pragma: no cover - startup robustness
            last_error = exc
            time.sleep(3)
    else:
        pytest.fail(f"Could not query the local Gel instance: {last_error!r}")

    try:
        yield conn
    finally:
        conn.close()


def _query_json(conn, query, **kwargs):
    return json.loads(conn.query_json(query, **kwargs))


def test_gel_cli_available():
    assert shutil.which("gel") is not None, (
        "The 'gel' CLI binary was not found in PATH."
    )


def test_python_gel_client_importable():
    import gel

    assert hasattr(gel, "create_client"), (
        "The Python package 'gel' is installed but does not expose "
        "create_client()."
    )


def test_helper_scripts_present():
    assert os.path.isfile(GEL_START_SCRIPT), (
        f"The Gel server start helper {GEL_START_SCRIPT} does not exist."
    )
    assert os.access(GEL_START_SCRIPT, os.X_OK), (
        f"{GEL_START_SCRIPT} exists but is not executable."
    )


def test_project_skeleton_present():
    assert os.path.isdir(PROJECT_DIR), (
        f"Project directory {PROJECT_DIR} does not exist."
    )
    assert os.path.isfile(GEL_TOML), f"{GEL_TOML} does not exist."
    assert os.path.isdir(os.path.join(PROJECT_DIR, "dbschema")), (
        f"{PROJECT_DIR}/dbschema does not exist."
    )
    assert os.path.isfile(SCHEMA_FILE), f"{SCHEMA_FILE} does not exist."


def test_start_script_not_created_yet():
    assert not os.path.exists(START_SCRIPT), (
        f"{START_SCRIPT} already exists; the executor is supposed to create it."
    )


def test_app_port_not_served_yet():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2)
    try:
        connected = sock.connect_ex(("127.0.0.1", APP_PORT)) == 0
    finally:
        sock.close()
    assert not connected, (
        f"Something is already serving TCP port {APP_PORT} before the task "
        "starts."
    )


def test_gel_server_is_reachable(client):
    assert client.query_single("select 1") == 1, (
        "Could not run a trivial query against the local Gel instance."
    )


def test_gel_cli_can_query_instance(client):
    proc = subprocess.run(
        ["gel", "query", "-F", "json", "select 1"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, (
        "'gel query' failed against the local instance: "
        f"rc={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "1" in proc.stdout, (
        f"'gel query select 1' produced unexpected output: {proc.stdout!r}"
    )


def test_account_type_exists_with_expected_pointers(client):
    rows = _query_json(
        client,
        """
        select schema::ObjectType {
            name,
            properties: { name, required, cardinality, target: { name } },
        }
        filter .name = 'default::Account'
        """,
    )
    assert len(rows) == 1, (
        "Expected exactly one object type named 'default::Account' in branch "
        f"main, found {len(rows)}."
    )
    props = {p["name"]: p for p in rows[0]["properties"]}
    for name, target in (("code", "std::str"), ("balance_cents", "std::int64")):
        assert name in props, (
            f"default::Account is missing the seeded property '{name}'."
        )
        assert props[name]["required"] is True, (
            f"default::Account.{name} is expected to be required."
        )
        assert props[name]["cardinality"] == "One", (
            f"default::Account.{name} is expected to be single-valued."
        )
        assert props[name]["target"]["name"] == target, (
            f"default::Account.{name} is expected to be of type {target}, got "
            f"{props[name]['target']['name']}."
        )


def test_account_code_is_exclusive(client):
    rows = _query_json(
        client,
        """
        select schema::ObjectType {
            name,
            properties: { name, constraints: { name } },
        }
        filter .name = 'default::Account'
        """,
    )
    assert rows, "Could not introspect default::Account."
    code_props = [p for p in rows[0]["properties"] if p["name"] == "code"]
    assert code_props, "default::Account has no property named 'code'."
    constraint_names = {c["name"] for c in code_props[0]["constraints"]}
    assert "std::exclusive" in constraint_names, (
        "default::Account.code is expected to carry an exclusive constraint, "
        f"found {sorted(constraint_names)}."
    )


def test_ledger_entry_type_does_not_exist_yet(client):
    rows = _query_json(
        client,
        """
        select schema::ObjectType { name }
        filter .name = 'default::LedgerEntry'
        """,
    )
    assert rows == [], (
        "default::LedgerEntry already exists; the executor is supposed to "
        "create it."
    )


def test_seeded_filler_accounts(client):
    count = _query_json(
        client,
        """
        select count((
            select default::Account filter .code like <str>$prefix ++ '%'
        ))
        """,
        prefix=SEEDED_ACC_PREFIX,
    )[0]
    assert count == SEEDED_ACC_COUNT, (
        f"Expected {SEEDED_ACC_COUNT} seeded accounts with a code starting "
        f"with '{SEEDED_ACC_PREFIX}', found {count}."
    )
    total = _query_json(
        client,
        """
        select sum((
            select default::Account filter .code like <str>$prefix ++ '%'
        ).balance_cents)
        """,
        prefix=SEEDED_ACC_PREFIX,
    )[0]
    assert total == SEEDED_ACC_TOTAL, (
        f"Expected the seeded '{SEEDED_ACC_PREFIX}' accounts to hold a total "
        f"of {SEEDED_ACC_TOTAL} cents, found {total}."
    )


def test_seeded_reserved_accounts(client):
    rows = _query_json(
        client,
        """
        select default::Account { code, balance_cents }
        filter .code like 'RSV-%'
        """,
    )
    found = {row["code"]: row["balance_cents"] for row in rows}
    assert found == RESERVED_ACCOUNTS, (
        f"Expected the reserved seeded accounts {RESERVED_ACCOUNTS}, found "
        f"{found}."
    )


def test_no_ledger_data_seeded(client):
    rows = _query_json(
        client,
        """
        select schema::ObjectType { name }
        filter .name like 'default::%'
        """,
    )
    names = sorted(row["name"] for row in rows)
    assert names == ["default::Account"], (
        "Branch main is expected to start with exactly one user-defined object "
        f"type (default::Account), found {names}."
    )
