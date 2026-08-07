"""Initial-state verification for the gel_hierarchy_path_rollup_cli task.

Checks that the baked environment contains a working local Gel 7.1 server, the
seeded catalog project, and that none of the artifacts the executor has to build
are present yet.
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

SEEDED_CATEGORY_SLUGS = {
    "tools",
    "garden",
    "home",
    "power",
    "hand",
    "storage",
    "clearance",
    "watering",
    "seeds",
    "furniture",
    "kitchen",
    "lighting",
    "drills",
    "saws",
    "sanders",
    "wrenches",
    "screwdrivers",
    "spares",
    "hoses",
    "sprinklers",
    "cookware",
    "cutlery",
    "bulbs",
    "cordless",
    "hammer",
    "pans",
}
SEEDED_CATEGORY_COUNT = 26
SEEDED_PRODUCT_COUNT = 112


def run_gel(args, timeout=180):
    """Run the gel CLI from the project root (flags always after the subcommand)."""
    return subprocess.run(
        ["gel", *args],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def server_responds():
    try:
        proc = run_gel(["query", "select 1"], timeout=30)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return proc.returncode == 0


@pytest.fixture(scope="session")
def gel_server():
    """Make sure the local Gel server accepts queries; start it if it is down."""
    if server_responds():
        return True
    starter = shutil.which("gel-start")
    assert starter is not None, "gel-start helper script not found in PATH."
    subprocess.run([starter], capture_output=True, text=True, timeout=600)
    deadline = time.time() + 300
    while time.time() < deadline:
        if server_responds():
            return True
        time.sleep(3)
    pytest.fail("Local Gel server did not become ready after running gel-start.")


def query_json(query):
    proc = run_gel(["query", "-F", "json", query])
    assert proc.returncode == 0, (
        f"gel query failed for {query!r}: stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    return json.loads(proc.stdout)


def test_gel_cli_available():
    assert shutil.which("gel") is not None, "gel CLI binary not found in PATH."


def test_gel_start_helper_available():
    assert shutil.which("gel-start") is not None, (
        "gel-start helper script (documented start command) not found in PATH."
    )


def test_project_directory_exists():
    assert os.path.isdir(PROJECT_DIR), f"Project directory {PROJECT_DIR} does not exist."


def test_gel_toml_exists():
    toml_path = os.path.join(PROJECT_DIR, "gel.toml")
    assert os.path.isfile(toml_path), f"{toml_path} does not exist."


def test_schema_file_exists():
    assert os.path.isfile(SCHEMA_FILE), f"{SCHEMA_FILE} does not exist."


def test_exactly_one_migration_present():
    migrations = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql")))
    assert len(migrations) == 1, (
        f"Expected exactly one baked migration file in {MIGRATIONS_DIR}, found {migrations}."
    )


def test_reporting_queries_not_created_yet():
    for name in ("tree.edgeql", "subtree_rollup.edgeql", "root_totals.edgeql"):
        path = os.path.join(PROJECT_DIR, "queries", name)
        assert not os.path.exists(path), (
            f"{path} already exists; the executor is supposed to create it."
        )


def test_server_is_reachable(gel_server):
    rows = query_json("select 1 + 1")
    assert rows == [2], f"Unexpected result from the local Gel server: {rows!r}"


def test_migrations_are_applied(gel_server):
    proc = run_gel(["migration", "status"])
    combined = (proc.stdout + proc.stderr).lower()
    assert proc.returncode == 0 and "up to date" in combined, (
        f"Baked migration is not applied: stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )


def test_seeded_categories(gel_server):
    rows = query_json("select Category { slug, parent_slug := .parent.slug }")
    assert len(rows) == SEEDED_CATEGORY_COUNT, (
        f"Expected {SEEDED_CATEGORY_COUNT} seeded categories, found {len(rows)}."
    )
    slugs = {row["slug"] for row in rows}
    assert slugs == SEEDED_CATEGORY_SLUGS, (
        f"Seeded category slugs differ from expectation: missing="
        f"{sorted(SEEDED_CATEGORY_SLUGS - slugs)} unexpected={sorted(slugs - SEEDED_CATEGORY_SLUGS)}"
    )
    roots = [row for row in rows if row["parent_slug"] is None]
    assert len(roots) == 3, f"Expected 3 root categories, found {len(roots)}: {roots!r}"


def test_seeded_tree_is_multi_level(gel_server):
    rows = query_json("select Category { slug, parent_slug := .parent.slug }")
    parent_of = {row["slug"]: row["parent_slug"] for row in rows}
    depths = {}
    for slug in parent_of:
        depth = 0
        cursor = parent_of[slug]
        while cursor is not None:
            depth += 1
            cursor = parent_of[cursor]
            assert depth <= 10, f"Seeded tree looks cyclic around {slug!r}."
        depths[slug] = depth
    assert max(depths.values()) == 3, (
        f"Expected the seeded tree to be 4 levels deep (max depth 3), got {max(depths.values())}."
    )


def test_seeded_products(gel_server):
    rows = query_json("select count(Product)")
    assert rows == [SEEDED_PRODUCT_COUNT], (
        f"Expected {SEEDED_PRODUCT_COUNT} seeded products, got {rows!r}"
    )


def test_products_reference_categories(gel_server):
    rows = query_json("select count((select Product filter not exists .category))")
    assert rows == [0], f"Every seeded product must link to a category, got {rows!r}"


def test_categories_with_empty_subtrees_exist(gel_server):
    rows = query_json(
        "select count((select Category filter not exists .<category[is Product]))"
    )
    assert rows and rows[0] >= 2, (
        f"Expected at least two seeded categories without products of their own, got {rows!r}"
    )


def test_category_has_no_ancestry_pointers_yet(gel_server):
    rows = query_json(
        "select schema::ObjectType { pointer_names := .pointers.name } "
        "filter .name = 'default::Category'"
    )
    assert rows, "Object type default::Category is missing from the baked schema."
    names = set(rows[0]["pointer_names"])
    for forbidden in ("depth", "path", "children", "ancestors"):
        assert forbidden not in names, (
            f"default::Category already declares {forbidden!r}; the executor must add it."
        )
    for required in ("slug", "name", "parent"):
        assert required in names, (
            f"default::Category is missing the baked pointer {required!r}: {sorted(names)}"
        )


def test_product_type_shape(gel_server):
    rows = query_json(
        "select schema::ObjectType { pointer_names := .pointers.name } "
        "filter .name = 'default::Product'"
    )
    assert rows, "Object type default::Product is missing from the baked schema."
    names = set(rows[0]["pointer_names"])
    for required in ("sku", "name", "price", "stock", "category"):
        assert required in names, (
            f"default::Product is missing the baked pointer {required!r}: {sorted(names)}"
        )


def test_relocation_type_absent(gel_server):
    rows = query_json(
        "select schema::ObjectType { name } filter .name = 'default::Relocation'"
    )
    assert rows == [], "default::Relocation already exists; the executor must create it."


def test_no_triggers_or_rewrites_yet(gel_server):
    rows = query_json(
        "select schema::ObjectType { name, trigger_names := .triggers.name } "
        "filter not .builtin and .name like 'default::%'"
    )
    for row in rows:
        assert not row["trigger_names"], (
            f"{row['name']} already declares triggers {row['trigger_names']!r}."
        )
    rewrites = query_json(
        "select count((select schema::Rewrite "
        "filter .subject[is schema::Pointer].source[is schema::ObjectType]"
        ".name like 'default::%'))"
    )
    assert rewrites == [0], f"The baked schema already contains mutation rewrites: {rewrites!r}"


def test_pytest_tooling_available():
    proc = subprocess.run(
        ["python3", "-m", "pytest", "--version"], capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, (
        f"python3 -m pytest is not usable: stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
