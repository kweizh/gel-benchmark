"""Initial-state verification for the gel_multi_tenant_access_policies_py task.

Checks the baked environment BEFORE the executor starts working:
a running local Gel 6 server, the project skeleton at /home/user/mtsaas with one
applied migration, seeded multi-tenant data, and an unimplemented gateway stub
with no globals and no object-level security yet.
"""

import asyncio
import glob
import os
import re
import shutil
import subprocess
import sys

import pytest

PROJECT_DIR = "/home/user/mtsaas"
SCHEMA_FILE = os.path.join(PROJECT_DIR, "dbschema", "default.gel")
MIGRATIONS_DIR = os.path.join(PROJECT_DIR, "dbschema", "migrations")
GATEWAY_FILE = os.path.join(PROJECT_DIR, "app", "tenant_gateway.py")
START_SCRIPT = "/usr/local/bin/gel-start.sh"

TENANT_TYPES = (
    "default::Tenant",
    "default::Workspace",
    "default::Document",
    "default::Comment",
)


@pytest.fixture(scope="session")
def gel_server():
    """Make sure the local Gel server is up before any DB/CLI interaction."""
    assert os.path.isfile(START_SCRIPT), f"{START_SCRIPT} is missing."
    proc = subprocess.run(
        [START_SCRIPT], capture_output=True, text=True, timeout=300
    )
    assert proc.returncode == 0, (
        f"{START_SCRIPT} failed (rc={proc.returncode}).\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    return True


@pytest.fixture(scope="session")
def client(gel_server):
    import gel

    c = gel.create_client()
    try:
        c.ensure_connected()
    except Exception as exc:  # pragma: no cover - environment failure
        pytest.fail(f"Could not connect to the local Gel instance: {exc!r}")
    yield c
    c.close()


def test_gel_cli_available():
    assert shutil.which("gel") is not None, "The 'gel' CLI is not available in PATH."


def test_python_gel_client_importable():
    import gel

    assert hasattr(gel, "create_client"), "The 'gel' Python package looks unusable."
    assert hasattr(
        gel, "create_async_client"
    ), "The 'gel' Python package has no create_async_client()."


def test_pytest_and_ctrf_plugin_installed():
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--version"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, "pytest is not installed for the active interpreter."
    listing = subprocess.run(
        [sys.executable, "-m", "pip", "list"], capture_output=True, text=True
    )
    assert (
        "ctrf" in listing.stdout.lower()
    ), "pytest-json-ctrf is not installed (needed by the verifier)."


def test_project_directory_exists():
    assert os.path.isdir(PROJECT_DIR), f"Project directory {PROJECT_DIR} is missing."
    assert os.path.isfile(
        os.path.join(PROJECT_DIR, "gel.toml")
    ), f"{PROJECT_DIR}/gel.toml is missing."


def test_schema_file_present_with_base_types():
    assert os.path.isfile(SCHEMA_FILE), f"{SCHEMA_FILE} is missing."
    text = open(SCHEMA_FILE, encoding="utf-8").read()
    for type_name in ("Tenant", "Workspace", "Document", "Comment"):
        assert (
            f"type {type_name}" in text
        ), f"Object type {type_name} is not declared in {SCHEMA_FILE}."


def test_schema_file_has_no_globals_or_policies_yet():
    text = open(SCHEMA_FILE, encoding="utf-8").read()
    assert (
        "access policy" not in text
    ), f"{SCHEMA_FILE} already declares an access policy; the initial state must not."
    assert (
        "global " not in text
    ), f"{SCHEMA_FILE} already declares a global; the initial state must not."


def test_exactly_one_migration_is_present():
    scripts = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql")))
    assert len(scripts) == 1, (
        "Expected exactly one baked migration script in "
        f"{MIGRATIONS_DIR}, found {len(scripts)}: {scripts}"
    )
    name = os.path.basename(scripts[0])
    assert re.fullmatch(
        r"\d{5}-[a-z0-9]+\.edgeql", name
    ), f"Unexpected migration file name {name!r} (expected <index>-<hash>.edgeql)."


def test_gateway_stub_exists_and_is_unimplemented():
    assert os.path.isfile(GATEWAY_FILE), f"{GATEWAY_FILE} is missing."
    assert os.path.isfile(
        os.path.join(PROJECT_DIR, "app", "__init__.py")
    ), f"{PROJECT_DIR}/app/__init__.py is missing."
    text = open(GATEWAY_FILE, encoding="utf-8").read()
    assert (
        "NotImplementedError" in text
    ), f"{GATEWAY_FILE} does not look like an unimplemented stub."


def test_gateway_stub_raises_not_implemented():
    if PROJECT_DIR not in sys.path:
        sys.path.insert(0, PROJECT_DIR)
    import app.tenant_gateway as gateway

    assert asyncio.iscoroutinefunction(
        gateway.list_workspaces
    ), "app.tenant_gateway.list_workspaces should be a coroutine function stub."
    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(NotImplementedError):
            loop.run_until_complete(gateway.list_workspaces("acme", "member"))
    finally:
        loop.close()


def test_migration_history_is_in_sync(gel_server):
    proc = subprocess.run(
        ["gel", "migration", "status", "--schema-dir", os.path.join(PROJECT_DIR, "dbschema")],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, (
        "'gel migration status' reports the baked branch is not up to date.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


def test_one_migration_recorded_in_database(client):
    count = client.query_single("select count(schema::Migration)")
    assert count == 1, f"Expected 1 recorded migration in the branch, found {count}."


def test_no_globals_declared_yet(client):
    names = client.query(
        "select (select schema::Global filter .name like 'default::%').name"
    )
    assert list(names) == [], f"The baked schema already declares globals: {names}."


def test_no_access_policies_declared_yet(client):
    rows = client.query(
        """
        select schema::ObjectType {
            name,
            policy_count := count(.access_policies)
        }
        filter .name in array_unpack(<array<str>>$names)
        """,
        names=list(TENANT_TYPES),
    )
    found = {row.name: row.policy_count for row in rows}
    assert set(found) == set(TENANT_TYPES), (
        f"Expected the four object types {TENANT_TYPES} to exist, found {sorted(found)}."
    )
    for name, policy_count in found.items():
        assert policy_count == 0, (
            f"{name} already has {policy_count} access policies; "
            "the initial state must have none."
        )


def test_base_type_shape(client):
    rows = client.query(
        """
        select schema::ObjectType {
            name,
            pointer_names := (select .pointers.name)
        }
        filter .name in array_unpack(<array<str>>$names)
        """,
        names=list(TENANT_TYPES),
    )
    shape = {row.name: set(row.pointer_names) for row in rows}
    expected = {
        "default::Tenant": {"slug", "name"},
        "default::Workspace": {"name", "tenant", "archived"},
        "default::Document": {"title", "body", "workspace", "created_at"},
        "default::Comment": {"body", "author_email", "document"},
    }
    for type_name, pointers in expected.items():
        assert pointers <= shape.get(type_name, set()), (
            f"{type_name} is missing expected pointers "
            f"{sorted(pointers - shape.get(type_name, set()))}."
        )


def test_no_computed_backlinks_yet(client):
    rows = client.query(
        """
        select schema::ObjectType {
            name,
            link_names := (select .pointers[is schema::Link].name)
        }
        filter .name in {'default::Workspace', 'default::Document'}
        """
    )
    links = {row.name: set(row.link_names) for row in rows}
    assert "documents" not in links.get("default::Workspace", set()), (
        "Workspace already exposes a 'documents' link; the executor must add it."
    )
    assert "comments" not in links.get("default::Document", set()), (
        "Document already exposes a 'comments' link; the executor must add it."
    )


def test_seeded_tenants_are_present(client):
    rows = client.query("select Tenant { slug, name } order by .slug")
    pairs = [(row.slug, row.name) for row in rows]
    assert pairs == [
        ("acme", "Acme Corp"),
        ("globex", "Globex Inc"),
        ("initech", "Initech LLC"),
    ], f"Unexpected seeded tenants: {pairs}."


def test_seeded_workspaces_are_present(client):
    rows = client.query(
        "select Workspace { name, archived, slug := .tenant.slug } order by .name"
    )
    got = {row.name: (row.slug, row.archived) for row in rows}
    expected = {
        "alpha": ("acme", False),
        "beta": ("acme", False),
        "zeta-archived": ("acme", True),
        "gamma": ("globex", False),
        "delta": ("globex", False),
        "omega": ("initech", False),
    }
    assert got == expected, f"Unexpected seeded workspaces: {got}."


def test_seeded_documents_and_comments_are_present(client):
    docs = client.query(
        "select Document { title, ws := .workspace.name } order by .title"
    )
    got = {row.title: row.ws for row in docs}
    expected = {
        "Alpha Charter": "alpha",
        "Alpha Roadmap": "alpha",
        "Beta Notes": "beta",
        "Frozen Plan": "zeta-archived",
        "Gamma Spec": "gamma",
        "Gamma Budget": "gamma",
    }
    assert got == expected, f"Unexpected seeded documents: {got}."

    bodies = sorted(client.query("select Comment.body"))
    assert bodies == [
        "charter note one",
        "charter note two",
        "frozen note one",
        "roadmap note one",
        "spec note one",
    ], f"Unexpected seeded comments: {bodies}."


def test_data_is_wide_open_before_the_task(client):
    """Without policies, a client with no globals still sees everything."""
    counts = client.query_single(
        """
        select {
            tenants := count(Tenant),
            workspaces := count(Workspace),
            documents := count(Document),
            comments := count(Comment),
        }
        """
    )
    assert (
        counts.tenants,
        counts.workspaces,
        counts.documents,
        counts.comments,
    ) == (3, 6, 6, 5), (
        "Expected the baked branch to expose 3 tenants, 6 workspaces, 6 documents "
        f"and 5 comments to an unscoped client, got {counts!r}."
    )
