import json
import os
import shutil
import subprocess
import time

import pytest

PROJECT_DIR = "/home/user/catalog"
SCHEMA_DIR = os.path.join(PROJECT_DIR, "dbschema")
MIGRATIONS_DIR = os.path.join(SCHEMA_DIR, "migrations")
SCHEMA_FILE = os.path.join(SCHEMA_DIR, "default.gel")
GEL_TOML = os.path.join(PROJECT_DIR, "gel.toml")
REPORT_SCRIPT = os.path.join(PROJECT_DIR, "report.py")

EXPLICIT_TREE = {
    "electronics": None,
    "audio": "electronics",
    "headphones": "audio",
    "wireless": "headphones",
    "wired": "headphones",
    "speakers": "audio",
    "cameras": "electronics",
    "lenses": "cameras",
    "storage": "electronics",
    "movable": "storage",
    "sandbox": None,
    "loop-a": "loop-b",
    "loop-b": "loop-a",
    "loop-c": "loop-a",
}


def _run(args, timeout=180, cwd=PROJECT_DIR):
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _server_responds():
    proc = _run(["gel", "query", "select 1"], timeout=60)
    return proc.returncode == 0


@pytest.fixture(scope="session")
def gel_server():
    """Start the bundled Gel server (idempotent) and wait until it answers queries."""
    start = _run(["gel-start"], timeout=600, cwd="/")
    deadline = time.time() + 300
    last = ""
    while time.time() < deadline:
        if _server_responds():
            return True
        last = "gel-start rc=%s stdout=%s stderr=%s" % (
            start.returncode,
            start.stdout[-2000:],
            start.stderr[-2000:],
        )
        time.sleep(3)
    pytest.fail("Gel server never became ready. " + last)


def gel_json(query, timeout=180):
    """Run an EdgeQL query through the gel CLI and decode the json-lines output."""
    proc = _run(["gel", "query", "-F", "json-lines", query], timeout=timeout)
    assert proc.returncode == 0, (
        "EdgeQL query failed (%s): %s\nstderr: %s" % (proc.returncode, query, proc.stderr)
    )
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def gel_one(query, timeout=180):
    rows = gel_json(query, timeout=timeout)
    assert len(rows) == 1, "Expected exactly one result row for query: %s (got %d)" % (
        query,
        len(rows),
    )
    return rows[0]


def test_gel_cli_available():
    assert shutil.which("gel") is not None, "The `gel` CLI is not available in PATH."


def test_gel_start_helper_available():
    assert shutil.which("gel-start") is not None, (
        "The `gel-start` helper used to bring up the bundled Gel server is not in PATH."
    )


def test_python3_available():
    assert shutil.which("python3") is not None, "python3 is not available in PATH."


def test_pytest_available():
    assert shutil.which("pytest") is not None, "pytest is not available in PATH."


def test_project_directory_exists():
    assert os.path.isdir(PROJECT_DIR), "Project directory %s does not exist." % PROJECT_DIR


def test_gel_toml_exists():
    assert os.path.isfile(GEL_TOML), "Expected the project manifest %s to exist." % GEL_TOML


def test_schema_file_exists():
    assert os.path.isfile(SCHEMA_FILE), "Expected the schema file %s to exist." % SCHEMA_FILE


def test_migrations_directory_has_initial_migration():
    assert os.path.isdir(MIGRATIONS_DIR), (
        "Expected the migration history directory %s to exist." % MIGRATIONS_DIR
    )
    migrations = sorted(
        name for name in os.listdir(MIGRATIONS_DIR) if name.endswith(".edgeql")
    )
    assert len(migrations) >= 1, (
        "Expected at least one migration file in %s, found %r." % (MIGRATIONS_DIR, migrations)
    )


def test_report_script_not_present_yet():
    assert not os.path.exists(REPORT_SCRIPT), (
        "%s already exists; the task must start without it." % REPORT_SCRIPT
    )


def test_seeded_schema_declares_base_types(gel_server):
    rows = gel_json(
        "select schema::ObjectType { name } "
        "filter .name in {'default::Node', 'default::Category', 'default::Listing', "
        "'default::Product', 'default::Bundle', 'default::CategoryAudit'} "
        "order by .name"
    )
    names = [row["name"] for row in rows]
    assert names == [
        "default::Bundle",
        "default::Category",
        "default::CategoryAudit",
        "default::Listing",
        "default::Node",
        "default::Product",
    ], "Seeded object types are missing or renamed: %r" % (names,)


def test_seeded_category_pointers(gel_server):
    row = gel_one(
        "select schema::ObjectType { pointers: { name } } filter .name = 'default::Category'"
    )
    names = sorted(
        p["name"] for p in row["pointers"] if p["name"] not in ("id", "__type__")
    )
    assert names == ["label", "parent", "rank", "slug"], (
        "Unexpected initial pointer set on default::Category: %r" % (names,)
    )


def test_computed_pointers_not_present_yet(gel_server):
    row = gel_one(
        "select schema::ObjectType { pointers: { name } } filter .name = 'default::Category'"
    )
    names = {p["name"] for p in row["pointers"]}
    for missing in ("children", "products", "audit"):
        assert missing not in names, (
            "default::Category already declares `%s`; the task must start without it." % missing
        )


def test_migration_history_is_in_sync(gel_server):
    proc = _run(["gel", "migration", "status", "--quiet"], timeout=180)
    assert proc.returncode == 0, (
        "The seeded instance is not in sync with %s (rc=%s): %s%s"
        % (SCHEMA_DIR, proc.returncode, proc.stdout, proc.stderr)
    )


def test_seeded_object_counts(gel_server):
    counts = {
        "Category": 854,
        "Product": 1609,
        "Bundle": 2,
        "CategoryAudit": 2,
    }
    for type_name, expected in counts.items():
        actual = gel_one("select count(%s)" % type_name)
        assert actual == expected, (
            "Expected %d seeded %s objects, found %s." % (expected, type_name, actual)
        )


def test_seeded_explicit_tree(gel_server):
    rows = gel_json(
        "select Category { slug, parent_slug := .parent.slug } "
        "filter .slug in {%s}" % ", ".join("'%s'" % slug for slug in EXPLICIT_TREE)
    )
    actual = {row["slug"]: row.get("parent_slug") for row in rows}
    assert actual == EXPLICIT_TREE, (
        "Seeded explicit category tree does not match the expected shape: %r" % (actual,)
    )


def test_seeded_spine_and_bins(gel_server):
    spine = gel_one("select count((select Category filter .slug like 'spine-%'))")
    bins = gel_one("select count((select Category filter .slug like 'bin-%'))")
    assert spine == 40, "Expected 40 seeded spine categories, found %s." % spine
    assert bins == 800, "Expected 800 seeded bin categories, found %s." % bins
    deepest = gel_one(
        "select Category { slug, parent_slug := .parent.slug } filter .slug = 'spine-39'"
    )
    assert deepest["parent_slug"] == "spine-38", (
        "Expected spine-39 to hang off spine-38, found %r." % (deepest,)
    )


def test_seeded_listings(gel_server):
    rows = gel_json(
        "select Listing { slug } filter .category.slug = 'wireless' order by .slug"
    )
    slugs = [row["slug"] for row in rows]
    assert slugs == ["hp-wl-1", "wl-bundle"], (
        "Expected the seeded listings of `wireless` to be hp-wl-1 and wl-bundle, found %r."
        % (slugs,)
    )
    row = gel_one("select Listing { price_cents } filter .slug = 'wl-bundle'")
    assert row["price_cents"] == 25000, (
        "Expected wl-bundle to cost 25000 cents, found %r." % (row,)
    )


def test_seeded_audits(gel_server):
    rows = gel_json(
        "select CategoryAudit { checked_by, category_slug := .category.slug } "
        "order by .checked_by"
    )
    actual = [(row["category_slug"], row["checked_by"]) for row in rows]
    assert actual == [("audio", "qa-anna"), ("electronics", "qa-bob")], (
        "Seeded CategoryAudit rows do not match the expected fixture: %r" % (actual,)
    )
