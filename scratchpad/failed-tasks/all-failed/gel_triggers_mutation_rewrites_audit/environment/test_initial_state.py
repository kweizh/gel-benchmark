import glob
import os
import shutil
import subprocess

import pytest

PROJECT_DIR = "/home/user/auditdb"
SCHEMA_DIR = os.path.join(PROJECT_DIR, "dbschema")
MIGRATIONS_DIR = os.path.join(SCHEMA_DIR, "migrations")
GEL_UP = "/usr/local/bin/gel-up"


@pytest.fixture(scope="session")
def client():
    """Make sure the local Gel server is up, then hand out a connected client."""
    proc = subprocess.run([GEL_UP], capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, (
        f"{GEL_UP} failed to start the local Gel server "
        f"(rc={proc.returncode}): {proc.stdout}\n{proc.stderr}"
    )
    import gel

    c = gel.create_client()
    try:
        c.ensure_connected()
        yield c
    finally:
        c.close()


def test_gel_cli_available():
    assert shutil.which("gel") is not None, "The 'gel' CLI binary was not found in PATH."


def test_gel_python_client_importable():
    import gel  # noqa: F401

    assert hasattr(gel, "create_client"), "The Python 'gel' client is not usable."


def test_gel_up_helper_is_executable():
    assert os.path.isfile(GEL_UP), f"{GEL_UP} does not exist."
    assert os.access(GEL_UP, os.X_OK), f"{GEL_UP} is not executable."


def test_project_directory_exists():
    assert os.path.isdir(PROJECT_DIR), f"Project directory {PROJECT_DIR} does not exist."


def test_gel_toml_exists():
    path = os.path.join(PROJECT_DIR, "gel.toml")
    assert os.path.isfile(path), f"Gel project manifest {path} does not exist."


def test_schema_file_exists():
    path = os.path.join(SCHEMA_DIR, "default.gel")
    assert os.path.isfile(path), f"Schema file {path} does not exist."


def test_existing_migration_history_present():
    assert os.path.isdir(MIGRATIONS_DIR), f"Migrations directory {MIGRATIONS_DIR} does not exist."
    migrations = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql")))
    assert len(migrations) >= 1, (
        f"Expected at least one applied migration file in {MIGRATIONS_DIR}, found {migrations}."
    )


def test_connection_environment_configured():
    for var in ("GEL_HOST", "GEL_PORT", "GEL_BRANCH"):
        assert os.environ.get(var), f"Environment variable {var} is not configured."


def test_migration_history_is_in_sync(client):
    proc = subprocess.run(
        ["gel", "migration", "status"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, (
        "The initial schema is expected to be already migrated and in sync, but "
        f"'gel migration status' exited {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    )


def test_seeded_documents_exist(client):
    rows = client.query(
        "select Document { slug, title, body } order by .slug"
    )
    got = {r.slug: (r.title, r.body) for r in rows}
    expected = {
        "seed-alpha": ("Alpha Doc", "alpha body"),
        "seed-beta": ("Beta Doc", "beta body"),
        "seed-gamma": ("Gamma Doc", "gamma body"),
    }
    for slug, (title, body) in expected.items():
        assert slug in got, f"Seeded Document '{slug}' is missing; found {sorted(got)}."
        assert got[slug] == (title, body), (
            f"Seeded Document '{slug}' should have title/body {(title, body)}, got {got[slug]}."
        )


def test_seeded_comments_exist(client):
    rows = client.query(
        "select Comment { body, doc := .document.slug } order by .body"
    )
    got = {r.body: r.doc for r in rows}
    expected = {
        "alpha comment one": "seed-alpha",
        "alpha comment two": "seed-alpha",
        "beta comment one": "seed-beta",
    }
    for body, slug in expected.items():
        assert body in got, f"Seeded Comment '{body}' is missing; found {sorted(got)}."
        assert got[body] == slug, (
            f"Seeded Comment '{body}' should link to Document '{slug}', got '{got[body]}'."
        )


def test_audit_machinery_not_present_yet(client):
    audit_types = client.query(
        "select schema::ObjectType { name } filter .name = 'default::AuditEntry'"
    )
    assert not audit_types, (
        "The 'default::AuditEntry' type must not exist in the initial schema; "
        "it is part of the work to be done."
    )

    doc_props = {
        r.name
        for r in client.query(
            """
            select schema::Property {
                name,
            }
            filter .source[is schema::ObjectType].name = 'default::Document'
            """
        )
    }
    for prop in ("version", "created_at", "modified_at"):
        assert prop not in doc_props, (
            f"Document.{prop} must not exist in the initial schema; it is part of the work to be done."
        )


def test_report_script_not_present_yet():
    path = os.path.join(PROJECT_DIR, "audit_report.py")
    assert not os.path.exists(path), (
        f"{path} must not exist before the task starts; the executor has to create it."
    )
