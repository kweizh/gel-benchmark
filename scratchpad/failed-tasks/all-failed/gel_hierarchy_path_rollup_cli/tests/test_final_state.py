"""Final-state verification for the gel_hierarchy_path_rollup_cli task.

Everything is checked against the real local Gel server through the `gel` CLI:
schema introspection, the stored ancestry data, the three reporting query files,
and the behaviour of inserts / relocations performed by this test suite.

All expected values are recomputed here from the raw `(slug, parent)` graph and
the raw product rows, so no roll-up number is hardcoded.
"""

import glob
import json
import os
import shutil
import subprocess
import time
from collections import defaultdict

import pytest

PROJECT_DIR = "/home/user/catalog"
MIGRATIONS_DIR = os.path.join(PROJECT_DIR, "dbschema", "migrations")
QUERIES_DIR = os.path.join(PROJECT_DIR, "queries")

TREE_QUERY = "queries/tree.edgeql"
ROLLUP_QUERY = "queries/subtree_rollup.edgeql"
ROOT_TOTALS_QUERY = "queries/root_totals.edgeql"

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
SEEDED_PRODUCT_COUNT = 112

# Unique-per-run suffix so the categories inserted by this suite never collide
# with anything already in the database (paths are unique).
RUN_TAG = "t{}".format(int(time.time() * 1000) % 100000000)


# --------------------------------------------------------------------------
# CLI plumbing
# --------------------------------------------------------------------------
def run_gel(args, timeout=300):
    """Run the gel CLI from the project root; flags always follow the subcommand."""
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
    """Guarantee the local Gel server is up before any CLI call is made."""
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


def gel_json(query):
    proc = run_gel(["query", "-F", "json", query])
    assert proc.returncode == 0, (
        f"gel query failed.\nquery: {query}\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        pytest.fail(f"gel query returned invalid JSON for {query!r}: {exc}; raw={proc.stdout!r}")


def gel_expect_failure(query):
    """Run a statement that must be rejected; return the combined CLI output."""
    proc = run_gel(["query", query])
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0, (
        f"Statement was expected to fail but succeeded.\nquery: {query}\noutput: {combined}"
    )
    return combined


def gel_query_file(relpath):
    path = os.path.join(PROJECT_DIR, relpath)
    assert os.path.isfile(path), f"Required query file {path} is missing."
    proc = run_gel(["query", "-F", "json", "-f", relpath])
    assert proc.returncode == 0, (
        f"`gel query -F json -f {relpath}` failed.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"{relpath} did not return valid JSON: {exc}; raw={proc.stdout!r}")


# --------------------------------------------------------------------------
# Independent recomputation of the expected state
# --------------------------------------------------------------------------
def fetch_categories():
    rows = gel_json("select Category { id, slug, name, parent_id := .parent.id, path, depth }")
    assert rows, "No Category objects found in the database."
    return rows


def fetch_products():
    return gel_json("select Product { sku, price, stock, cat_id := .category.id }")


def build_expected(rows):
    """Recompute path/depth/ancestors/children per category id from (slug, parent)."""
    slug = {r["id"]: r["slug"] for r in rows}
    parent = {r["id"]: r["parent_id"] for r in rows}
    for cid, pid in parent.items():
        assert pid is None or pid in slug, (
            f"Category {slug[cid]!r} points at an unknown parent id {pid!r}."
        )

    ancestors = {}

    def chain(cid):
        if cid in ancestors:
            return ancestors[cid]
        acc = []
        cursor = parent[cid]
        seen = set()
        while cursor is not None:
            assert cursor not in seen, f"Cycle detected in the category tree at {slug[cid]!r}."
            seen.add(cursor)
            acc.append(cursor)
            cursor = parent[cursor]
        acc.reverse()  # root first
        ancestors[cid] = acc
        return acc

    expected = {}
    children = defaultdict(list)
    for cid in slug:
        chain(cid)
        pid = parent[cid]
        if pid is not None:
            children[pid].append(cid)
    for cid in slug:
        path = "/" + "/".join([slug[a] for a in ancestors[cid]] + [slug[cid]])
        expected[cid] = {
            "slug": slug[cid],
            "path": path,
            "depth": len(ancestors[cid]),
            "ancestors": ancestors[cid],
            "children": sorted(children.get(cid, [])),
            "parent": parent[cid],
        }
    return expected


def descendant_map(expected):
    desc = defaultdict(list)
    for cid, info in expected.items():
        for anc in info["ancestors"]:
            desc[anc].append(cid)
    return desc


def expected_rollups(expected, products):
    desc = descendant_map(expected)
    by_cat = defaultdict(list)
    for prod in products:
        by_cat[prod["cat_id"]].append(prod)
    out = {}
    for cid, info in expected.items():
        ids = [cid] + desc.get(cid, [])
        rows = [p for i in ids for p in by_cat.get(i, [])]
        prices = [float(p["price"]) for p in rows]
        out[info["path"]] = {
            "slug": info["slug"],
            "depth": info["depth"],
            "product_count": len(rows),
            "total_stock": sum(int(p["stock"]) for p in rows),
            "min_price": min(prices) if prices else None,
            "max_price": max(prices) if prices else None,
            "avg_price": (sum(prices) / len(prices)) if prices else None,
        }
    return out


def expected_root_totals(expected, products):
    by_cat = defaultdict(list)
    for prod in products:
        by_cat[prod["cat_id"]].append(prod)
    grouped = defaultdict(list)
    for cid, info in expected.items():
        root_id = info["ancestors"][0] if info["ancestors"] else cid
        grouped[expected[root_id]["slug"]].extend(by_cat.get(cid, []))
    out = {}
    for root_slug, rows in grouped.items():
        if not rows:
            continue
        prices = [float(p["price"]) for p in rows]
        out[root_slug] = {
            "product_count": len(rows),
            "total_stock": sum(int(p["stock"]) for p in rows),
            "avg_price": sum(prices) / len(prices),
        }
    return out


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_two_decimals(value):
    return abs(float(value) - round(float(value), 2)) <= 1e-9


def object_of(name):
    rows = gel_json(
        "select schema::ObjectType { "
        "  name, "
        "  prop_details := (select .pointers[is schema::Property] { "
        "     name, required, cardinality, expr, target_name := .target.name, "
        "     rewrite_kinds := .rewrites.kind }), "
        "  link_details := (select .pointers[is schema::Link] { "
        "     name, required, cardinality, expr, target_name := .target.name, "
        "     rewrite_kinds := .rewrites.kind }), "
        "  constraint_details := (select .constraints { name, subjectexpr }), "
        "  trigger_details := (select .triggers { name, kinds, timing }) "
        "} filter .name = '" + name + "'"
    )
    assert rows, f"Object type {name} does not exist in the schema."
    return rows[0]


def pointer(details, name):
    for item in details:
        if item["name"] == name:
            return item
    return None


def category_snapshot():
    rows = fetch_categories()
    return {r["id"]: (r["path"], r["depth"], r["parent_id"]) for r in rows}


def category_ref(cid):
    return f"assert_single((select detached Category filter .id = <uuid>'{cid}'))"


def relocation_stmt(category_id, new_parent_id=None):
    """Build the single statement that relocates a category (and its subtree)."""
    if new_parent_id is None:
        stmt = (
            "select (insert Relocation { category := "
            + category_ref(category_id)
            + " }) { id }"
        )
    else:
        stmt = (
            "select (insert Relocation { category := "
            + category_ref(category_id)
            + ", new_parent := "
            + category_ref(new_parent_id)
            + " }) { id }"
        )
    return stmt


# --------------------------------------------------------------------------
# 1. Migrations
# --------------------------------------------------------------------------
def test_migrations_applied_and_in_sync(gel_server):
    migrations = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql")))
    assert len(migrations) >= 2, (
        f"Expected at least two migration files in {MIGRATIONS_DIR} (the baked one plus the "
        f"schema change), found: {migrations}"
    )
    proc = run_gel(["migration", "status"])
    combined = (proc.stdout + proc.stderr).lower()
    assert proc.returncode == 0 and "up to date" in combined, (
        f"`gel migration status` does not report the database up to date.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


# --------------------------------------------------------------------------
# 2-6. Schema contract
# --------------------------------------------------------------------------
def test_depth_and_path_are_stored_properties_with_insert_rewrites(gel_server):
    category = object_of("default::Category")
    for name, scalar in (("depth", "std::int64"), ("path", "std::str")):
        prop = pointer(category["prop_details"], name)
        assert prop is not None, f"default::Category has no property {name!r}."
        assert prop["target_name"] == scalar, (
            f"Category.{name} must be {scalar}, found {prop['target_name']!r}."
        )
        assert prop["required"] is True, f"Category.{name} must be declared required."
        assert prop["cardinality"] == "One", (
            f"Category.{name} must be single-cardinality, found {prop['cardinality']!r}."
        )
        assert prop["expr"] is None, (
            f"Category.{name} must be a stored property, but it is computed "
            f"(expr={prop['expr']!r})."
        )
        assert "Insert" in (prop["rewrite_kinds"] or []), (
            f"Category.{name} must carry a mutation rewrite for insert, found rewrites "
            f"{prop['rewrite_kinds']!r}."
        )


def test_children_and_ancestors_are_computed_multi_links(gel_server):
    category = object_of("default::Category")
    for name in ("children", "ancestors"):
        link = pointer(category["link_details"], name)
        assert link is not None, f"default::Category has no link {name!r}."
        assert link["cardinality"] == "Many", (
            f"Category.{name} must be a multi link, found cardinality {link['cardinality']!r}."
        )
        assert link["target_name"] == "default::Category", (
            f"Category.{name} must target default::Category, found {link['target_name']!r}."
        )
        assert link["expr"], f"Category.{name} must be a computed link (non-empty expression)."


def test_exclusive_path_constraint_declared(gel_server):
    category = object_of("default::Category")
    matches = [
        c
        for c in category["constraint_details"]
        if c["name"] == "std::exclusive" and c["subjectexpr"] and "path" in c["subjectexpr"]
    ]
    assert matches, (
        "default::Category must declare a std::exclusive constraint on the path; found "
        f"constraints: {category['constraint_details']!r}"
    )


def test_relocation_type_shape(gel_server):
    relocation = object_of("default::Relocation")

    category_link = pointer(relocation["link_details"], "category")
    assert category_link is not None, "default::Relocation has no link 'category'."
    assert category_link["target_name"] == "default::Category", (
        f"Relocation.category must target default::Category, found {category_link['target_name']!r}."
    )
    assert category_link["required"] is True, "Relocation.category must be required."
    assert category_link["cardinality"] == "One", "Relocation.category must be a single link."

    new_parent = pointer(relocation["link_details"], "new_parent")
    assert new_parent is not None, "default::Relocation has no link 'new_parent'."
    assert new_parent["target_name"] == "default::Category", (
        f"Relocation.new_parent must target default::Category, found {new_parent['target_name']!r}."
    )
    assert new_parent["required"] is False, "Relocation.new_parent must be optional."
    assert new_parent["cardinality"] == "One", "Relocation.new_parent must be a single link."

    for name in ("from_path", "to_path"):
        prop = pointer(relocation["prop_details"], name)
        assert prop is not None, f"default::Relocation has no property {name!r}."
        assert prop["target_name"] == "std::str", (
            f"Relocation.{name} must be std::str, found {prop['target_name']!r}."
        )
        assert prop["required"] is True, f"Relocation.{name} must be required."
        assert "Insert" in (prop["rewrite_kinds"] or []), (
            f"Relocation.{name} must be filled in by a mutation rewrite on insert, found "
            f"rewrites {prop['rewrite_kinds']!r}."
        )


def test_relocation_triggers_declared(gel_server):
    relocation = object_of("default::Relocation")
    triggers = {t["name"]: t for t in relocation["trigger_details"]}
    for name in ("apply_relocation", "reject_cycles"):
        assert name in triggers, (
            f"default::Relocation must declare a trigger named {name!r}; found "
            f"{sorted(triggers)}."
        )
        assert "Insert" in (triggers[name]["kinds"] or []), (
            f"Trigger {name!r} must fire on insert, found kinds {triggers[name]['kinds']!r}."
        )
        assert triggers[name]["timing"] == "After", (
            f"Trigger {name!r} must be an `after insert` trigger, found timing "
            f"{triggers[name]['timing']!r}."
        )


# --------------------------------------------------------------------------
# 7-9. Data correctness
# --------------------------------------------------------------------------
def test_seeded_data_intact(gel_server):
    rows = fetch_categories()
    slugs = {r["slug"] for r in rows}
    missing = sorted(SEEDED_CATEGORY_SLUGS - slugs)
    assert not missing, f"Seeded categories were removed: {missing}"
    counts = gel_json("select count(Product)")
    assert counts == [SEEDED_PRODUCT_COUNT], (
        f"Expected the {SEEDED_PRODUCT_COUNT} seeded products to remain, got {counts!r}"
    )


def test_stored_path_and_depth_match_the_tree(gel_server):
    rows = fetch_categories()
    expected = build_expected(rows)
    problems = []
    for row in rows:
        exp = expected[row["id"]]
        if row["path"] != exp["path"] or row["depth"] != exp["depth"]:
            problems.append(
                f"{row['slug']}: stored (path={row['path']!r}, depth={row['depth']!r}) "
                f"!= expected (path={exp['path']!r}, depth={exp['depth']!r})"
            )
    assert not problems, "Stored ancestry data is wrong:\n" + "\n".join(problems)
    paths = [row["path"] for row in rows]
    assert len(set(paths)) == len(paths), f"Category paths are not unique: {sorted(paths)}"


def test_ancestors_and_children_links_match_the_tree(gel_server):
    rows = fetch_categories()
    expected = build_expected(rows)
    live = gel_json(
        "select Category { id, slug, ancestor_ids := .ancestors.id, child_ids := .children.id }"
    )
    problems = []
    for row in live:
        exp = expected[row["id"]]
        if sorted(row["ancestor_ids"]) != sorted(exp["ancestors"]):
            problems.append(
                f"{row['slug']}: ancestors link returned "
                f"{sorted(row['ancestor_ids'])} but expected {sorted(exp['ancestors'])}"
            )
        if sorted(row["child_ids"]) != exp["children"]:
            problems.append(
                f"{row['slug']}: children link returned "
                f"{sorted(row['child_ids'])} but expected {exp['children']}"
            )
    assert not problems, "Computed navigation links are wrong:\n" + "\n".join(problems)


# --------------------------------------------------------------------------
# 10-13. Reporting query files
# --------------------------------------------------------------------------
def test_query_files_exist():
    for relpath in (TREE_QUERY, ROLLUP_QUERY, ROOT_TOTALS_QUERY):
        path = os.path.join(PROJECT_DIR, relpath)
        assert os.path.isfile(path), f"Missing required query file {path}."
        assert os.path.getsize(path) > 0, f"Query file {path} is empty."
    with open(os.path.join(PROJECT_DIR, ROOT_TOTALS_QUERY)) as handle:
        source = handle.read()
    assert "group" in source, (
        f"{ROOT_TOTALS_QUERY} must be built with EdgeQL's top-level `group` statement."
    )


def check_tree_output():
    rows = fetch_categories()
    expected = build_expected(rows)
    result = gel_query_file(TREE_QUERY)
    assert isinstance(result, list), f"{TREE_QUERY} must return a JSON array, got {type(result)}."
    assert len(result) == len(expected), (
        f"{TREE_QUERY} returned {len(result)} rows for {len(expected)} categories."
    )
    wanted_keys = {"path", "slug", "name", "depth", "parent_path", "child_count"}
    by_path = {}
    for item in result:
        assert isinstance(item, dict), f"{TREE_QUERY} elements must be JSON objects: {item!r}"
        assert set(item) == wanted_keys, (
            f"{TREE_QUERY} element keys must be exactly {sorted(wanted_keys)}, got "
            f"{sorted(item)}."
        )
        by_path[item["path"]] = item
    paths = [item["path"] for item in result]
    assert paths == sorted(paths), f"{TREE_QUERY} must be ordered ascending by path, got {paths}."

    names = {expected[row["id"]]["path"]: row["name"] for row in rows}
    for cid, info in expected.items():
        item = by_path.get(info["path"])
        assert item is not None, f"{TREE_QUERY} is missing the category at path {info['path']!r}."
        assert item["slug"] == info["slug"], (
            f"{TREE_QUERY}: slug for {info['path']!r} should be {info['slug']!r}, got "
            f"{item['slug']!r}."
        )
        assert item["name"] == names[info["path"]], (
            f"{TREE_QUERY}: name for {info['path']!r} should be "
            f"{names[info['path']]!r}, got {item['name']!r}."
        )
        assert item["depth"] == info["depth"], (
            f"{TREE_QUERY}: depth for {info['path']!r} should be {info['depth']}, got "
            f"{item['depth']!r}."
        )
        assert isinstance(item["depth"], int), (
            f"{TREE_QUERY}: depth must be a JSON integer, got {item['depth']!r}."
        )
        parent_path = expected[info["parent"]]["path"] if info["parent"] else None
        assert item["parent_path"] == parent_path, (
            f"{TREE_QUERY}: parent_path for {info['path']!r} should be {parent_path!r}, got "
            f"{item['parent_path']!r}."
        )
        assert item["child_count"] == len(info["children"]), (
            f"{TREE_QUERY}: child_count for {info['path']!r} should be "
            f"{len(info['children'])}, got {item['child_count']!r}."
        )
        assert isinstance(item["child_count"], int), (
            f"{TREE_QUERY}: child_count must be a JSON integer, got {item['child_count']!r}."
        )


def check_rollup_output():
    rows = fetch_categories()
    products = fetch_products()
    expected = expected_rollups(build_expected(rows), products)
    result = gel_query_file(ROLLUP_QUERY)
    assert isinstance(result, list), f"{ROLLUP_QUERY} must return a JSON array."
    assert len(result) == len(expected), (
        f"{ROLLUP_QUERY} returned {len(result)} rows for {len(expected)} categories."
    )
    wanted_keys = {
        "path",
        "slug",
        "depth",
        "product_count",
        "total_stock",
        "min_price",
        "max_price",
        "avg_price",
    }
    seen = {}
    for item in result:
        assert isinstance(item, dict), f"{ROLLUP_QUERY} elements must be JSON objects: {item!r}"
        assert set(item) == wanted_keys, (
            f"{ROLLUP_QUERY} element keys must be exactly {sorted(wanted_keys)}, got "
            f"{sorted(item)}."
        )
        seen[item["path"]] = item
    paths = [item["path"] for item in result]
    assert paths == sorted(paths), f"{ROLLUP_QUERY} must be ordered ascending by path, got {paths}."

    empty_subtrees = 0
    for path, exp in expected.items():
        item = seen.get(path)
        assert item is not None, f"{ROLLUP_QUERY} is missing the category at path {path!r}."
        assert item["slug"] == exp["slug"], (
            f"{ROLLUP_QUERY}: slug for {path!r} should be {exp['slug']!r}, got {item['slug']!r}."
        )
        assert item["depth"] == exp["depth"], (
            f"{ROLLUP_QUERY}: depth for {path!r} should be {exp['depth']}, got {item['depth']!r}."
        )
        assert item["product_count"] == exp["product_count"], (
            f"{ROLLUP_QUERY}: product_count for {path!r} should be {exp['product_count']} "
            f"(products of the category and of every descendant), got {item['product_count']!r}."
        )
        assert isinstance(item["product_count"], int), (
            f"{ROLLUP_QUERY}: product_count must be a JSON integer, got {item['product_count']!r}."
        )
        assert item["total_stock"] == exp["total_stock"], (
            f"{ROLLUP_QUERY}: total_stock for {path!r} should be {exp['total_stock']}, got "
            f"{item['total_stock']!r}."
        )
        assert isinstance(item["total_stock"], int), (
            f"{ROLLUP_QUERY}: total_stock must be a JSON integer, got {item['total_stock']!r}."
        )
        if exp["product_count"] == 0:
            empty_subtrees += 1
            for key in ("min_price", "max_price", "avg_price"):
                assert item[key] is None, (
                    f"{ROLLUP_QUERY}: {key} for the product-free subtree {path!r} must be null, "
                    f"got {item[key]!r}."
                )
            continue
        for key in ("min_price", "max_price", "avg_price"):
            assert is_number(item[key]), (
                f"{ROLLUP_QUERY}: {key} for {path!r} must be a number, got {item[key]!r}."
            )
        assert abs(float(item["min_price"]) - exp["min_price"]) <= 1e-6, (
            f"{ROLLUP_QUERY}: min_price for {path!r} should be {exp['min_price']}, got "
            f"{item['min_price']!r}."
        )
        assert abs(float(item["max_price"]) - exp["max_price"]) <= 1e-6, (
            f"{ROLLUP_QUERY}: max_price for {path!r} should be {exp['max_price']}, got "
            f"{item['max_price']!r}."
        )
        assert abs(float(item["avg_price"]) - exp["avg_price"]) <= 0.006, (
            f"{ROLLUP_QUERY}: avg_price for {path!r} should be about "
            f"{round(exp['avg_price'], 2)}, got {item['avg_price']!r}."
        )
        assert is_two_decimals(item["avg_price"]), (
            f"{ROLLUP_QUERY}: avg_price for {path!r} must be rounded to 2 decimal places, got "
            f"{item['avg_price']!r}."
        )
    assert empty_subtrees >= 1, (
        "The catalog is expected to contain at least one category whose subtree holds no "
        "products; the recomputed expectation found none, so the null handling was not exercised."
    )


def check_root_totals_output():
    rows = fetch_categories()
    products = fetch_products()
    expected = expected_root_totals(build_expected(rows), products)
    result = gel_query_file(ROOT_TOTALS_QUERY)
    assert isinstance(result, list), f"{ROOT_TOTALS_QUERY} must return a JSON array."
    wanted_keys = {"root_slug", "product_count", "total_stock", "avg_price"}
    seen = {}
    for item in result:
        assert isinstance(item, dict), (
            f"{ROOT_TOTALS_QUERY} elements must be JSON objects: {item!r}"
        )
        assert set(item) == wanted_keys, (
            f"{ROOT_TOTALS_QUERY} element keys must be exactly {sorted(wanted_keys)}, got "
            f"{sorted(item)}."
        )
        seen[item["root_slug"]] = item
    slugs = [item["root_slug"] for item in result]
    assert slugs == sorted(slugs), (
        f"{ROOT_TOTALS_QUERY} must be ordered ascending by root_slug, got {slugs}."
    )
    assert set(seen) == set(expected), (
        f"{ROOT_TOTALS_QUERY} must contain exactly the root categories that have products in "
        f"their subtree: expected {sorted(expected)}, got {sorted(seen)}."
    )
    for root_slug, exp in expected.items():
        item = seen[root_slug]
        assert item["product_count"] == exp["product_count"], (
            f"{ROOT_TOTALS_QUERY}: product_count for root {root_slug!r} should be "
            f"{exp['product_count']}, got {item['product_count']!r}."
        )
        assert item["total_stock"] == exp["total_stock"], (
            f"{ROOT_TOTALS_QUERY}: total_stock for root {root_slug!r} should be "
            f"{exp['total_stock']}, got {item['total_stock']!r}."
        )
        assert is_number(item["avg_price"]), (
            f"{ROOT_TOTALS_QUERY}: avg_price for root {root_slug!r} must be a number, got "
            f"{item['avg_price']!r}."
        )
        assert abs(float(item["avg_price"]) - exp["avg_price"]) <= 0.006, (
            f"{ROOT_TOTALS_QUERY}: avg_price for root {root_slug!r} should be about "
            f"{round(exp['avg_price'], 2)}, got {item['avg_price']!r}."
        )
        assert is_two_decimals(item["avg_price"]), (
            f"{ROOT_TOTALS_QUERY}: avg_price for root {root_slug!r} must be rounded to 2 decimal "
            f"places, got {item['avg_price']!r}."
        )


def test_tree_query_contract(gel_server):
    check_tree_output()


def test_subtree_rollup_query_contract(gel_server):
    check_rollup_output()


def test_root_totals_query_contract(gel_server):
    check_root_totals_output()


# --------------------------------------------------------------------------
# 14-16. Automatic ancestry on insert, uniqueness
# --------------------------------------------------------------------------
def test_new_categories_get_ancestry_automatically(gel_server):
    rows = fetch_categories()
    expected = build_expected(rows)
    anchors = sorted(
        (info["path"], cid) for cid, info in expected.items() if info["depth"] == 2
    )
    assert anchors, "No category at depth 2 was found to attach the probe chain to."
    anchor_path, anchor_id = anchors[0]

    parent_id = anchor_id
    parent_path = anchor_path
    parent_depth = 2
    for suffix in ("a", "b", "c"):
        slug = f"probe-{RUN_TAG}-{suffix}"
        result = gel_json(
            "select (insert Category { slug := '"
            + slug
            + "', name := 'Probe "
            + suffix.upper()
            + "', parent := "
            + category_ref(parent_id)
            + " }) { id }"
        )
        assert result and "id" in result[0], f"Inserting probe category {slug!r} returned {result!r}"
        new_id = result[0]["id"]
        stored = gel_json(
            "select Category { path, depth, parent_id := .parent.id } filter .id = <uuid>'"
            + new_id
            + "'"
        )
        assert stored, f"Probe category {slug!r} was not stored."
        want_path = f"{parent_path}/{slug}"
        want_depth = parent_depth + 1
        assert stored[0]["path"] == want_path, (
            f"After inserting {slug!r} under {parent_path!r} the database must store path "
            f"{want_path!r}, got {stored[0]['path']!r}."
        )
        assert stored[0]["depth"] == want_depth, (
            f"After inserting {slug!r} under {parent_path!r} the database must store depth "
            f"{want_depth}, got {stored[0]['depth']!r}."
        )
        parent_id, parent_path, parent_depth = new_id, want_path, want_depth

    assert parent_depth == 5, (
        f"The probe chain should end at depth 5, ended at {parent_depth}."
    )
    # The whole tree must still be internally consistent.
    fresh = fetch_categories()
    fresh_expected = build_expected(fresh)
    for row in fresh:
        info = fresh_expected[row["id"]]
        assert (row["path"], row["depth"]) == (info["path"], info["depth"]), (
            f"After the inserts, {row['slug']!r} has stored (path={row['path']!r}, "
            f"depth={row['depth']!r}) but the tree implies (path={info['path']!r}, "
            f"depth={info['depth']!r})."
        )


def test_duplicate_sibling_slug_is_rejected(gel_server):
    rows = fetch_categories()
    expected = build_expected(rows)
    candidates = sorted(
        (info["path"], cid) for cid, info in expected.items() if info["parent"] is not None
    )
    assert candidates, "No non-root category available for the duplicate-sibling check."
    _, victim_id = candidates[0]
    victim = expected[victim_id]
    before = gel_json("select count(Category)")
    output = gel_expect_failure(
        "insert Category { slug := '"
        + victim["slug"]
        + "', name := 'Duplicate sibling', parent := "
        + category_ref(victim["parent"])
        + " }"
    )
    assert "ConstraintViolationError" in output, (
        "Inserting a second child with slug "
        f"{victim['slug']!r} under the same parent must raise a ConstraintViolationError, got: "
        f"{output}"
    )
    after = gel_json("select count(Category)")
    assert after == before, (
        f"The rejected insert must not change the category count ({before!r} -> {after!r})."
    )


def test_duplicate_root_slug_is_rejected(gel_server):
    rows = fetch_categories()
    expected = build_expected(rows)
    roots = sorted(info["slug"] for info in expected.values() if info["parent"] is None)
    assert roots, "No root category available for the duplicate-root check."
    before = gel_json("select count(Category)")
    output = gel_expect_failure(
        "insert Category { slug := '" + roots[0] + "', name := 'Duplicate root' }"
    )
    assert "ConstraintViolationError" in output, (
        f"Inserting a second root category with slug {roots[0]!r} must raise a "
        f"ConstraintViolationError, got: {output}"
    )
    after = gel_json("select count(Category)")
    assert after == before, (
        f"The rejected insert must not change the category count ({before!r} -> {after!r})."
    )


# --------------------------------------------------------------------------
# 17-19. Relocations
# --------------------------------------------------------------------------
def test_relocation_moves_the_whole_subtree(gel_server):
    rows = fetch_categories()
    expected = build_expected(rows)
    desc = descendant_map(expected)

    movable = []
    for cid, info in expected.items():
        deep = [d for d in desc.get(cid, []) if expected[d]["depth"] >= info["depth"] + 2]
        if deep:
            movable.append((info["path"], cid))
    assert movable, "No category with descendants two levels below it was found."
    _, moved_id = sorted(movable)[0]
    moved = expected[moved_id]
    subtree = [moved_id] + desc.get(moved_id, [])

    destinations = []
    for cid, info in expected.items():
        if cid in subtree or cid == moved["parent"]:
            continue
        if any(expected[child]["slug"] == moved["slug"] for child in info["children"]):
            continue
        destinations.append((info["path"], cid))
    assert destinations, f"No valid destination outside the subtree of {moved['path']!r}."
    _, dest_id = sorted(destinations)[0]
    dest = expected[dest_id]

    old_paths = {cid: expected[cid]["path"] for cid in subtree}
    old_depths = {cid: expected[cid]["depth"] for cid in subtree}
    new_prefix = f"{dest['path']}/{moved['slug']}"
    delta = dest["depth"] + 1 - moved["depth"]

    result = gel_json(relocation_stmt(moved_id, dest_id))
    assert result and "id" in result[0], (
        f"Inserting a Relocation for {moved['path']!r} -> {dest['path']!r} returned {result!r}"
    )
    relocation_id = result[0]["id"]

    after = {r["id"]: r for r in fetch_categories()}
    assert after[moved_id]["parent_id"] == dest_id, (
        f"After the relocation, {moved['slug']!r} must be a child of {dest['slug']!r}."
    )
    problems = []
    for cid in subtree:
        want_path = new_prefix + old_paths[cid][len(moved["path"]) :]
        want_depth = old_depths[cid] + delta
        got = after[cid]
        if got["path"] != want_path or got["depth"] != want_depth:
            problems.append(
                f"{got['slug']}: got (path={got['path']!r}, depth={got['depth']!r}), "
                f"expected (path={want_path!r}, depth={want_depth})"
            )
    assert not problems, (
        "A single Relocation insert must fix the whole moved subtree:\n" + "\n".join(problems)
    )

    audit = gel_json(
        "select Relocation { from_path, to_path } filter .id = <uuid>'" + relocation_id + "'"
    )
    assert audit, "The inserted Relocation row cannot be read back."
    assert audit[0]["from_path"] == moved["path"], (
        f"Relocation.from_path must be {moved['path']!r}, got {audit[0]['from_path']!r}."
    )
    assert audit[0]["to_path"] == new_prefix, (
        f"Relocation.to_path must be {new_prefix!r}, got {audit[0]['to_path']!r}."
    )

    fresh = fetch_categories()
    fresh_expected = build_expected(fresh)
    for row in fresh:
        info = fresh_expected[row["id"]]
        assert (row["path"], row["depth"]) == (info["path"], info["depth"]), (
            f"After the relocation, {row['slug']!r} stores (path={row['path']!r}, "
            f"depth={row['depth']!r}) but the tree implies (path={info['path']!r}, "
            f"depth={info['depth']!r})."
        )


def test_reports_follow_the_relocation(gel_server):
    check_tree_output()
    check_rollup_output()
    check_root_totals_output()


def test_relocation_to_root_level(gel_server):
    rows = fetch_categories()
    expected = build_expected(rows)
    root_slugs = {info["slug"] for info in expected.values() if info["parent"] is None}
    candidates = [
        (info["path"], cid)
        for cid, info in expected.items()
        if info["depth"] >= 2 and info["children"] and info["slug"] not in root_slugs
    ]
    assert candidates, "No nested category with children was available to promote to a root."
    _, moved_id = sorted(candidates)[0]
    moved = expected[moved_id]
    desc = descendant_map(expected)
    subtree = [moved_id] + desc.get(moved_id, [])
    old_paths = {cid: expected[cid]["path"] for cid in subtree}
    old_depths = {cid: expected[cid]["depth"] for cid in subtree}
    new_prefix = "/" + moved["slug"]
    delta = -moved["depth"]

    result = gel_json(relocation_stmt(moved_id))
    assert result and "id" in result[0], (
        f"Inserting a Relocation without new_parent for {moved['path']!r} returned {result!r}"
    )

    after = {r["id"]: r for r in fetch_categories()}
    assert after[moved_id]["parent_id"] is None, (
        f"{moved['slug']!r} must have no parent after being relocated to the root level."
    )
    assert after[moved_id]["path"] == new_prefix, (
        f"{moved['slug']!r} must have path {new_prefix!r} after becoming a root, got "
        f"{after[moved_id]['path']!r}."
    )
    assert after[moved_id]["depth"] == 0, (
        f"{moved['slug']!r} must have depth 0 after becoming a root, got "
        f"{after[moved_id]['depth']!r}."
    )
    problems = []
    for cid in subtree:
        want_path = new_prefix + old_paths[cid][len(moved["path"]) :]
        want_depth = old_depths[cid] + delta
        got = after[cid]
        if got["path"] != want_path or got["depth"] != want_depth:
            problems.append(
                f"{got['slug']}: got (path={got['path']!r}, depth={got['depth']!r}), "
                f"expected (path={want_path!r}, depth={want_depth})"
            )
    assert not problems, (
        "Promoting a category to the root level must fix its whole subtree:\n"
        + "\n".join(problems)
    )


# --------------------------------------------------------------------------
# 20-21. Rejected relocations
# --------------------------------------------------------------------------
def test_cycle_creating_relocations_are_rejected(gel_server):
    rows = fetch_categories()
    expected = build_expected(rows)
    candidates = sorted(
        (info["path"], cid) for cid, info in expected.items() if info["children"]
    )
    assert candidates, "No category with children was available for the cycle check."
    _, parent_id = candidates[0]
    child_id = sorted(expected[parent_id]["children"], key=lambda cid: expected[cid]["path"])[0]

    before_snapshot = category_snapshot()
    before_relocations = gel_json("select count(Relocation)")

    for label, target, new_parent in (
        ("under its own descendant", parent_id, child_id),
        ("under itself", parent_id, parent_id),
    ):
        output = gel_expect_failure(relocation_stmt(target, new_parent))
        assert "CATEGORY_CYCLE" in output, (
            f"Relocating {expected[target]['slug']!r} {label} must fail with an error containing "
            f"'CATEGORY_CYCLE', got: {output}"
        )

    after_relocations = gel_json("select count(Relocation)")
    assert after_relocations == before_relocations, (
        f"Rejected relocations must not store a Relocation row "
        f"({before_relocations!r} -> {after_relocations!r})."
    )
    assert category_snapshot() == before_snapshot, (
        "Rejected relocations must leave every category's path, depth and parent untouched."
    )


def test_destination_slug_collision_is_rejected(gel_server):
    rows = fetch_categories()
    expected = build_expected(rows)

    desc = descendant_map(expected)
    pair = None
    for cid, info in sorted(expected.items(), key=lambda kv: kv[1]["path"]):
        if info["parent"] is None:
            continue
        subtree = set([cid] + desc.get(cid, []))
        for did, dinfo in sorted(expected.items(), key=lambda kv: kv[1]["path"]):
            if did in subtree or did == info["parent"]:
                continue
            if any(expected[ch]["slug"] == info["slug"] for ch in dinfo["children"]):
                continue
            pair = (cid, did)
            break
        if pair:
            break
    assert pair is not None, "Could not find a category/destination pair for the collision check."
    moved_id, dest_id = pair
    moved = expected[moved_id]
    dest = expected[dest_id]

    blocker = gel_json(
        "select (insert Category { slug := '"
        + moved["slug"]
        + "', name := 'Collision blocker', parent := "
        + category_ref(dest_id)
        + " }) { id, path }"
    )
    assert blocker, "Failed to create the blocking category for the collision check."
    assert blocker[0]["path"] == f"{dest['path']}/{moved['slug']}", (
        f"The blocking category should be stored at {dest['path']}/{moved['slug']!r}, got "
        f"{blocker[0]['path']!r}."
    )

    before_snapshot = category_snapshot()
    before_relocations = gel_json("select count(Relocation)")
    output = gel_expect_failure(relocation_stmt(moved_id, dest_id))
    assert "ConstraintViolationError" in output, (
        f"Relocating {moved['path']!r} under {dest['path']!r} collides with an existing path and "
        f"must fail with a ConstraintViolationError, got: {output}"
    )
    after_relocations = gel_json("select count(Relocation)")
    assert after_relocations == before_relocations, (
        f"The rejected relocation must not store a Relocation row "
        f"({before_relocations!r} -> {after_relocations!r})."
    )
    assert category_snapshot() == before_snapshot, (
        "The rejected relocation must leave every category's path, depth and parent untouched."
    )
