"""Initial-state verification for the gel_double_entry_ledger_transfers_py task.

These tests describe the environment that exists BEFORE the executor starts working:
a local Gel server with a bare `Account` model, one applied migration, four seeded
accounts, and a not-yet-implemented `ledger_api` module.
"""

import asyncio
import decimal
import glob
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time

import pytest

PROJECT_DIR = "/home/user/ledger"
SCHEMA_FILE = os.path.join(PROJECT_DIR, "dbschema", "default.gel")
MIGRATIONS_DIR = os.path.join(PROJECT_DIR, "dbschema", "migrations")
MODULE_PATH = os.path.join(PROJECT_DIR, "ledger_api.py")
START_SCRIPT = "/usr/local/bin/gel-start.sh"

SEEDED_ACCOUNTS = {
    "ACC-1001": decimal.Decimal("1000.00"),
    "ACC-1002": decimal.Decimal("500.00"),
    "ACC-1003": decimal.Decimal("250.50"),
    "ACC-1004": decimal.Decimal("0.00"),
}

API_FUNCTIONS = ["create_account", "create_transfer", "get_balance", "get_statement"]
API_EXCEPTIONS = [
    "LedgerError",
    "UnknownAccount",
    "DuplicateAccount",
    "InvalidAmount",
    "SameAccountTransfer",
    "InsufficientFunds",
]


def _run_gel(args, timeout=120):
    """Run a `gel` CLI command inside the project directory."""
    return subprocess.run(
        ["gel"] + args,
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.fixture(scope="session")
def gel_server():
    """Start the local Gel server (idempotent) and wait until it answers queries."""
    proc = subprocess.run(
        ["bash", START_SCRIPT], capture_output=True, text=True, timeout=300
    )
    deadline = time.time() + 180
    last = ""
    while time.time() < deadline:
        probe = _run_gel(["query", "-F", "json", "select 1"], timeout=60)
        if probe.returncode == 0:
            return True
        last = (probe.stdout or "") + (probe.stderr or "")
        time.sleep(2)
    raise AssertionError(
        "The local Gel server did not become reachable.\n"
        f"start script stdout: {proc.stdout}\nstart script stderr: {proc.stderr}\n"
        f"last probe output: {last}"
    )


def _query_list(query):
    """Run a query through the CLI and always return the result set as a list."""
    proc = _run_gel(["query", "-F", "json", query])
    assert proc.returncode == 0, (
        f"`gel query` failed for {query!r}: stdout={proc.stdout} stderr={proc.stderr}"
    )
    data = json.loads(proc.stdout)
    return data if isinstance(data, list) else [data]


def _query_one(query):
    data = _query_list(query)
    return data[0] if data else None


def test_gel_cli_available():
    assert shutil.which("gel") is not None, "The `gel` CLI was not found in PATH."


def test_gel_python_client_importable():
    assert importlib.util.find_spec("gel") is not None, (
        "The `gel` Python client is not installed."
    )


def test_pytest_available():
    assert importlib.util.find_spec("pytest") is not None, "pytest is not installed."


def test_start_script_present_and_executable():
    assert os.path.isfile(START_SCRIPT), f"{START_SCRIPT} does not exist."
    assert os.access(START_SCRIPT, os.X_OK), f"{START_SCRIPT} is not executable."


def test_project_directory_exists():
    assert os.path.isdir(PROJECT_DIR), f"Project directory {PROJECT_DIR} does not exist."


def test_gel_toml_exists():
    toml_path = os.path.join(PROJECT_DIR, "gel.toml")
    assert os.path.isfile(toml_path), f"{toml_path} does not exist."


def test_schema_file_exists_with_bare_account_model():
    assert os.path.isfile(SCHEMA_FILE), f"{SCHEMA_FILE} does not exist."
    with open(SCHEMA_FILE, encoding="utf-8") as handle:
        content = handle.read()
    assert "Account" in content, (
        f"{SCHEMA_FILE} is expected to declare the `Account` type initially."
    )
    assert "opening_balance" in content, (
        f"{SCHEMA_FILE} is expected to declare `opening_balance` on `Account`."
    )
    assert "LedgerEntry" not in content, (
        f"{SCHEMA_FILE} must not declare `LedgerEntry` before the task is solved."
    )


def test_exactly_one_migration_is_baked():
    assert os.path.isdir(MIGRATIONS_DIR), f"{MIGRATIONS_DIR} does not exist."
    migrations = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql")))
    assert len(migrations) == 1, (
        f"Expected exactly one baked migration in {MIGRATIONS_DIR}, found: {migrations}"
    )


def test_ledger_api_stub_exists():
    assert os.path.isfile(MODULE_PATH), f"{MODULE_PATH} does not exist."


def _load_ledger_api():
    if PROJECT_DIR not in sys.path:
        sys.path.insert(0, PROJECT_DIR)
    spec = importlib.util.spec_from_file_location("ledger_api", MODULE_PATH)
    assert spec is not None and spec.loader is not None, (
        f"{MODULE_PATH} could not be loaded as the module `ledger_api`."
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ledger_api_declares_the_public_interface():
    module = _load_ledger_api()
    for name in API_FUNCTIONS:
        assert hasattr(module, name), f"`ledger_api.{name}` is missing from the stub."
        assert asyncio.iscoroutinefunction(getattr(module, name)), (
            f"`ledger_api.{name}` is expected to be a coroutine function."
        )
    for name in API_EXCEPTIONS:
        assert hasattr(module, name), f"`ledger_api.{name}` is missing from the stub."


def test_ledger_api_exceptions_share_a_base_class():
    module = _load_ledger_api()
    base = getattr(module, "LedgerError")
    assert issubclass(base, Exception), "`LedgerError` must derive from `Exception`."
    for name in API_EXCEPTIONS:
        if name == "LedgerError":
            continue
        assert issubclass(getattr(module, name), base), (
            f"`ledger_api.{name}` must be a subclass of `LedgerError`."
        )


def test_ledger_api_is_not_implemented_yet():
    module = _load_ledger_api()
    with pytest.raises(NotImplementedError):
        asyncio.run(
            module.create_transfer(
                None,
                sender="ACC-1001",
                recipient="ACC-1002",
                amount=decimal.Decimal("1.00"),
                idempotency_key="initial-state-probe",
            )
        )
    with pytest.raises(NotImplementedError):
        asyncio.run(module.get_balance(None, account="ACC-1001"))


def test_server_answers_queries(gel_server):
    assert _query_one("select 1") == 1, (
        "The local Gel server did not answer a trivial query."
    )


def test_migration_history_is_in_sync(gel_server):
    proc = _run_gel(["migration", "status"])
    assert proc.returncode == 0, (
        "`gel migration status` reports that the baked migration history is not "
        f"in sync: stdout={proc.stdout} stderr={proc.stderr}"
    )


def test_account_type_exists_in_database(gel_server):
    names = _query_list(
        "select schema::ObjectType { name } filter .name = 'default::Account'"
    )
    assert len(names) == 1, (
        f"`default::Account` was not found in the applied schema: {names}"
    )


def test_transfer_and_ledger_entry_types_do_not_exist_yet(gel_server):
    for type_name in ("default::Transfer", "default::LedgerEntry"):
        found = _query_list(
            "select schema::ObjectType { name } filter .name = "
            f"'{type_name}'"
        )
        assert found == [], (
            f"{type_name} already exists before the task is solved: {found}"
        )


def test_seeded_accounts_are_present(gel_server):
    rows = _query_list(
        "select Account { code, ob := <str>.opening_balance } order by .code"
    )
    actual = {row["code"]: decimal.Decimal(row["ob"]) for row in rows}
    assert set(actual) == set(SEEDED_ACCOUNTS), (
        f"Expected exactly the seeded accounts {sorted(SEEDED_ACCOUNTS)}, got {sorted(actual)}"
    )
    for code, expected in SEEDED_ACCOUNTS.items():
        assert actual[code] == expected, (
            f"Seeded account {code} should have opening_balance {expected}, got {actual[code]}"
        )


def test_no_ledger_data_exists_yet(gel_server):
    assert _query_one("select count(Account)") == 4, (
        "The initial state must contain exactly the four seeded accounts."
    )
