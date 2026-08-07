"""Initial-state verification for the gel_stdlib_string_regex_normalization_cli task.

Validates that the environment handed to the executor already contains a running
local Gel 6 server, a pre-initialized project at /home/user/catalog and the 14
seeded RawProduct rows.
"""

import glob
import json
import os
import shutil
import subprocess
import time

import pytest

PROJECT_DIR = "/home/user/catalog"
SCHEMA_FILE = os.path.join(PROJECT_DIR, "dbschema", "default.gel")
MIGRATIONS_DIR = os.path.join(PROJECT_DIR, "dbschema", "migrations")
START_SCRIPT = "/usr/local/bin/gel-start.sh"

EXPECTED_SOURCE_IDS = [
    "R-0001",
    "R-0002",
    "R-0003",
    "R-0004",
    "R-0005",
    "R-0006",
    "R-0007",
    "R-0008",
    "R-0009",
    "R-0010",
    "R-0011",
    "R-0012",
    "R-0013",
    "R-0014",
]


def _gel_env():
    env = dict(os.environ)
    env.setdefault("GEL_HOST", "127.0.0.1")
    env.setdefault("GEL_PORT", "5656")
    env.setdefault("GEL_USER", "admin")
    env.setdefault("GEL_BRANCH", "main")
    env.setdefault("GEL_CLIENT_TLS_SECURITY", "insecure")
    return env


def _gel_query(query, timeout=60):
    return subprocess.run(
        ["gel", "query", "-F", "json", query],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_gel_env(),
        cwd=PROJECT_DIR if os.path.isdir(PROJECT_DIR) else "/",
    )


def _server_ready():
    try:
        proc = _gel_query("select 1", timeout=30)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return proc.returncode == 0


@pytest.fixture(scope="session")
def gel_server():
    """Make sure the local Gel server is up before any CLI/DB assertion runs."""
    if _server_ready():
        return True
    assert os.path.isfile(START_SCRIPT), (
        f"Gel server is not reachable and the start script {START_SCRIPT} is missing."
    )
    subprocess.run(
        ["bash", START_SCRIPT],
        capture_output=True,
        text=True,
        timeout=300,
        env=_gel_env(),
    )
    deadline = time.time() + 180
    while time.time() < deadline:
        if _server_ready():
            return True
        time.sleep(3)
    pytest.fail("Local Gel server did not become reachable within 180 seconds.")


def test_gel_cli_available():
    assert shutil.which("gel") is not None, "The `gel` CLI binary was not found in PATH."


def test_project_directory_exists():
    assert os.path.isdir(PROJECT_DIR), f"Project directory {PROJECT_DIR} does not exist."


def test_gel_toml_exists():
    path = os.path.join(PROJECT_DIR, "gel.toml")
    assert os.path.isfile(path), f"Expected the project manifest {path} to exist."


def test_schema_file_declares_raw_product():
    assert os.path.isfile(SCHEMA_FILE), f"Expected schema file {SCHEMA_FILE} to exist."
    content = open(SCHEMA_FILE, encoding="utf-8").read()
    assert "RawProduct" in content, (
        f"Expected {SCHEMA_FILE} to declare the RawProduct object type."
    )
    for prop in ("source_id", "raw_name", "raw_sku", "raw_contact", "raw_tags"):
        assert prop in content, (
            f"Expected {SCHEMA_FILE} to declare the RawProduct property `{prop}`."
        )


def test_initial_migration_present():
    assert os.path.isdir(MIGRATIONS_DIR), (
        f"Expected the migrations directory {MIGRATIONS_DIR} to exist."
    )
    migrations = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql")))
    assert len(migrations) >= 1, (
        f"Expected at least one applied migration file in {MIGRATIONS_DIR}, found none."
    )


def test_server_is_reachable(gel_server):
    proc = _gel_query("select 1")
    assert proc.returncode == 0, (
        f"`gel query 'select 1'` failed (rc={proc.returncode}): {proc.stderr}"
    )


def test_migration_history_applied(gel_server):
    proc = _gel_query("select count(schema::Migration)")
    assert proc.returncode == 0, f"Failed to count migrations: {proc.stderr}"
    count = json.loads(proc.stdout)[0]
    assert count >= 1, f"Expected at least one applied migration in the database, got {count}."


def test_raw_products_seeded(gel_server):
    proc = _gel_query("select RawProduct.source_id order by RawProduct.source_id")
    assert proc.returncode == 0, f"Failed to query RawProduct: {proc.stderr}"
    source_ids = json.loads(proc.stdout)
    assert source_ids == EXPECTED_SOURCE_IDS, (
        f"Expected the 14 seeded RawProduct rows {EXPECTED_SOURCE_IDS}, got {source_ids}."
    )


def test_raw_product_payload_is_dirty(gel_server):
    proc = _gel_query(
        "select RawProduct { source_id, raw_name, raw_sku, raw_contact, raw_tags } "
        "filter .source_id = 'R-0001'"
    )
    assert proc.returncode == 0, f"Failed to query RawProduct R-0001: {proc.stderr}"
    rows = json.loads(proc.stdout)
    assert len(rows) == 1, f"Expected exactly one RawProduct with source_id R-0001, got {rows}."
    row = rows[0]
    assert row["raw_name"] == "  Espresso   Machine  Deluxe ", (
        f"Unexpected seeded raw_name for R-0001: {row['raw_name']!r}"
    )
    assert row["raw_sku"] == "esp-0042-a1", (
        f"Unexpected seeded raw_sku for R-0001: {row['raw_sku']!r}"
    )
    assert row["raw_contact"] == "sales@Example.COM or +1-555-0142", (
        f"Unexpected seeded raw_contact for R-0001: {row['raw_contact']!r}"
    )
    assert row["raw_tags"] == "coffee, kitchen; kitchen", (
        f"Unexpected seeded raw_tags for R-0001: {row['raw_tags']!r}"
    )
