"""Initial-state verification for the gel_triggers_audit_trail_cli task.

These checks describe the environment that exists BEFORE the executor starts:
an initialized Gel project with a single ``default::Product`` object type, one
applied migration and eight seeded products -- and no audit subsystem yet.
"""

import glob
import json
import os
import shutil
import subprocess

import pytest

PROJECT_DIR = "/home/user/pricing-audit"
SCHEMA_FILE = os.path.join(PROJECT_DIR, "dbschema", "default.gel")
MIGRATIONS_DIR = os.path.join(PROJECT_DIR, "dbschema", "migrations")
SCRIPTS_DIR = os.path.join(PROJECT_DIR, "scripts")
REFERENCE_MIGRATION = "/opt/task-reference/00001-original.edgeql"

SEEDED_SKUS = [
    "AX-100",
    "AX-200",
    "BX-110",
    "BX-220",
    "CX-130",
    "CX-240",
    "DX-150",
    "SKU-FROZEN",
]


def run(args, timeout=180):
    return subprocess.run(
        args,
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def gel_json(query, timeout=180):
    proc = run(["gel", "query", "-F", "json", query], timeout=timeout)
    assert proc.returncode == 0, (
        f"gel query failed ({proc.returncode}) for {query!r}:\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    return json.loads(proc.stdout)


@pytest.fixture(scope="session")
def server():
    """Ensure the local Gel server is reachable before DB-dependent checks."""
    starter = shutil.which("gel-start.sh") or "/usr/local/bin/gel-start.sh"
    proc = subprocess.run(
        [starter], capture_output=True, text=True, timeout=600
    )
    assert proc.returncode == 0, (
        "gel-start.sh failed to bring up the local Gel server:\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    return True


def test_gel_cli_available():
    assert shutil.which("gel") is not None, "The `gel` CLI was not found in PATH."


def test_gel_start_helper_available():
    starter = shutil.which("gel-start.sh") or "/usr/local/bin/gel-start.sh"
    assert os.path.isfile(starter) and os.access(starter, os.X_OK), (
        "The idempotent server starter `gel-start.sh` is missing or not executable."
    )


def test_project_directory_exists():
    assert os.path.isdir(PROJECT_DIR), f"Project directory {PROJECT_DIR} does not exist."


def test_gel_toml_exists():
    path = os.path.join(PROJECT_DIR, "gel.toml")
    assert os.path.isfile(path), f"Expected the Gel project manifest at {path}."


def test_schema_file_exists_with_product_type():
    assert os.path.isfile(SCHEMA_FILE), f"Expected the SDL schema file at {SCHEMA_FILE}."
    content = open(SCHEMA_FILE).read()
    assert "type Product" in content, (
        "The initial schema file must declare the `Product` object type."
    )
    for prop in ("sku", "name", "price_cents", "stock"):
        assert prop in content, (
            f"The initial `Product` declaration is missing the `{prop}` property."
        )


def test_initial_schema_has_no_audit_subsystem():
    content = open(SCHEMA_FILE).read()
    for token in (
        "AuditEvent",
        "AuditBatch",
        "trigger",
        "rewrite",
        "revision",
        "price_history",
    ):
        assert token not in content, (
            f"The initial schema file must not already contain {token!r}; "
            "the audit subsystem is the executor's job."
        )


def test_scripts_directory_exists_and_is_empty_of_task_scripts():
    assert os.path.isdir(SCRIPTS_DIR), f"Expected the scripts directory at {SCRIPTS_DIR}."
    for name in ("apply_price_change.sh", "audit_report.sh"):
        assert not os.path.exists(os.path.join(SCRIPTS_DIR, name)), (
            f"{name} must not exist yet; the executor has to create it."
        )


def test_exactly_one_initial_migration_matching_reference():
    files = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql")))
    assert len(files) == 1, (
        f"Expected exactly one initial migration in {MIGRATIONS_DIR}, found {files}."
    )
    assert os.path.basename(files[0]).startswith("00001-"), (
        f"The initial migration should be the content-hashed 00001-* file, got {files[0]}."
    )
    assert os.path.isfile(REFERENCE_MIGRATION), (
        f"Expected the pristine reference copy of the first migration at {REFERENCE_MIGRATION}."
    )
    assert open(files[0]).read() == open(REFERENCE_MIGRATION).read(), (
        "The initial migration file must match the pristine reference copy."
    )


def test_migration_status_is_up_to_date(server):
    proc = run(["gel", "migration", "status"])
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0, (
        f"`gel migration status` failed initially: {combined}"
    )
    assert "up to date" in combined, (
        f"The database should start up-to-date with the filesystem migrations: {combined}"
    )


def test_product_type_exists_in_database(server):
    rows = gel_json(
        "select schema::ObjectType { name } filter .name = 'default::Product'"
    )
    assert len(rows) == 1, "The database schema must already contain default::Product."


def test_eight_seeded_products_exist(server):
    rows = gel_json("select Product { sku } order by .sku")
    skus = sorted(row["sku"] for row in rows)
    assert skus == sorted(SEEDED_SKUS), (
        f"Expected exactly the 8 seeded product skus {sorted(SEEDED_SKUS)}, got {skus}."
    )


def test_audit_types_do_not_exist_yet(server):
    rows = gel_json(
        "select schema::ObjectType { name } "
        "filter .name in {'default::AuditEvent', 'default::AuditBatch'}"
    )
    assert rows == [], (
        f"AuditEvent/AuditBatch must not exist before the task is solved, found {rows}."
    )


def test_no_triggers_or_rewrites_exist_yet(server):
    triggers = gel_json("select schema::Trigger { name }")
    assert triggers == [], (
        f"No schema triggers should exist in the initial state, found {triggers}."
    )
    rewrites = gel_json(
        "select schema::ObjectType { name, "
        "properties: { name, rewrites: { kind } } filter exists .rewrites } "
        "filter .name = 'default::Product' and exists .properties.rewrites"
    )
    assert rewrites == [], (
        f"No mutation rewrites should exist on Product initially, found {rewrites}."
    )
