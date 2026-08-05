import json
import os
import subprocess
import time

import pytest

PROJECT_DIR = "/home/user/catalog"
SCHEMA_DIR = os.path.join(PROJECT_DIR, "dbschema")
MIGRATIONS_DIR = os.path.join(SCHEMA_DIR, "migrations")
REPORT_SCRIPT = os.path.join(PROJECT_DIR, "report.py")

REPORT_KEYS = {
    "slug",
    "path",
    "depth",
    "parent",
    "children",
    "audit_checked_by",
    "rollup",
    "deepest_branch",
}
ROLLUP_KEYS = {
    "category_count",
    "product_count",
    "bundle_count",
    "listing_count",
    "price_cents_total",
    "in_stock_product_count",
}

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

# ---------------------------------------------------------------------------
# process helpers
# ---------------------------------------------------------------------------


def _run(args, timeout=300, cwd=PROJECT_DIR):
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _server_responds():
    try:
        proc = _run(["gel", "query", "select 1"], timeout=60)
    except subprocess.TimeoutExpired:
        return False
    return proc.returncode == 0


@pytest.fixture(scope="session")
def gel_server():
    """Bring up the bundled Gel server (idempotent) and wait until it answers queries."""
    try:
        start = _run(["gel-start"], timeout=600, cwd="/")
        detail = "gel-start rc=%s stdout=%s stderr=%s" % (
            start.returncode,
            start.stdout[-2000:],
            start.stderr[-2000:],
        )
    except subprocess.TimeoutExpired:
        detail = "gel-start timed out after 600s"
    deadline = time.time() + 300
    while time.time() < deadline:
        if _server_responds():
            return True
        time.sleep(3)
    pytest.fail("Gel server never became ready. " + detail)


def gel_json(query, timeout=300):
    proc = _run(["gel", "query", "-F", "json-lines", query], timeout=timeout)
    assert proc.returncode == 0, "EdgeQL query failed (rc=%s): %s\nstderr: %s" % (
        proc.returncode,
        query,
        proc.stderr,
    )
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def gel_one(query, timeout=300):
    rows = gel_json(query, timeout=timeout)
    assert len(rows) == 1, "Expected exactly one row from %s, got %d." % (query, len(rows))
    return rows[0]


def gel_exec_expect_failure(query):
    proc = _run(["gel", "query", query], timeout=300)
    return proc


def gel_exec(query):
    proc = _run(["gel", "query", query], timeout=300)
    assert proc.returncode == 0, "EdgeQL statement failed (rc=%s): %s\nstderr: %s" % (
        proc.returncode,
        query,
        proc.stderr,
    )
    return proc


def run_report(args, timeout=180):
    started = time.time()
    proc = _run(["python3", "report.py"] + list(args), timeout=timeout)
    elapsed = time.time() - started
    return proc, elapsed


def report_ok(args, max_seconds=60.0):
    proc, elapsed = run_report(args)
    assert proc.returncode == 0, (
        "`python3 report.py %s` exited with %s (expected 0).\nstdout: %s\nstderr: %s"
        % (" ".join(args), proc.returncode, proc.stdout[-4000:], proc.stderr[-4000:])
    )
    assert elapsed <= max_seconds, (
        "`python3 report.py %s` took %.1fs, which exceeds the %.0fs budget."
        % (" ".join(args), elapsed, max_seconds)
    )
    try:
        doc = json.loads(proc.stdout)
    except ValueError as exc:
        raise AssertionError(
            "stdout of `python3 report.py %s` is not a single JSON document (%s): %r"
            % (" ".join(args), exc, proc.stdout[:4000])
        )
    assert isinstance(doc, dict), "Expected a JSON object on stdout, got %r." % (doc,)
    assert set(doc) == REPORT_KEYS, (
        "Report document keys must be exactly %s, got %s."
        % (sorted(REPORT_KEYS), sorted(doc))
    )
    assert isinstance(doc["rollup"], dict) and set(doc["rollup"]) == ROLLUP_KEYS, (
        "`rollup` keys must be exactly %s, got %r." % (sorted(ROLLUP_KEYS), doc["rollup"])
    )
    return doc, proc.stdout


def report_fails(args, expected_code, expected_error, max_seconds=60.0):
    proc, elapsed = run_report(args)
    assert proc.returncode == expected_code, (
        "`python3 report.py %s` exited with %s (expected %s).\nstdout: %s\nstderr: %s"
        % (
            " ".join(args),
            proc.returncode,
            expected_code,
            proc.stdout[-4000:],
            proc.stderr[-4000:],
        )
    )
    assert elapsed <= max_seconds, (
        "`python3 report.py %s` took %.1fs, which exceeds the %.0fs budget."
        % (" ".join(args), elapsed, max_seconds)
    )
    assert proc.stdout.strip() == "", (
        "Failed invocations must print nothing on stdout, got %r." % (proc.stdout[:2000],)
    )
    try:
        payload = json.loads(proc.stderr)
    except ValueError as exc:
        raise AssertionError(
            "stderr of `python3 report.py %s` is not a single JSON document (%s): %r"
            % (" ".join(args), exc, proc.stderr[:2000])
        )
    assert payload == expected_error, (
        "Expected stderr JSON %r for `python3 report.py %s`, got %r."
        % (expected_error, " ".join(args), payload)
    )


def rollup(doc):
    r = doc["rollup"]
    return (
        r["category_count"],
        r["product_count"],
        r["bundle_count"],
        r["listing_count"],
        r["price_cents_total"],
        r["in_stock_product_count"],
    )


# ---------------------------------------------------------------------------
# independent oracle, computed from raw rows read straight out of the database
# ---------------------------------------------------------------------------


def load_snapshot():
    parents = {}
    for row in gel_json("select Category { slug, parent_slug := .parent.slug }"):
        parents[row["slug"]] = row.get("parent_slug")

    products = {}
    for row in gel_json(
        "select Product { slug, price_cents, in_stock, cat := .category.slug }"
    ):
        products.setdefault(row["cat"], []).append(
            (row["slug"], row["price_cents"], row["in_stock"])
        )

    bundles = {}
    for row in gel_json("select Bundle { slug, price_cents, cat := .category.slug }"):
        bundles.setdefault(row["cat"], []).append((row["slug"], row["price_cents"]))

    audits = {}
    for row in gel_json("select CategoryAudit { checked_by, cat := .category.slug }"):
        audits[row["cat"]] = row["checked_by"]

    children = dict((slug, []) for slug in parents)
    for slug, parent in parents.items():
        if parent is not None:
            children[parent].append(slug)
    for slug in children:
        children[slug].sort()

    return {
        "parents": parents,
        "children": children,
        "products": products,
        "bundles": bundles,
        "audits": audits,
    }


def oracle_report(snap, slug):
    parents = snap["parents"]
    children = snap["children"]

    path = []
    seen = set()
    cur = slug
    while cur is not None:
        assert cur not in seen, "Oracle found a cycle above %s." % slug
        seen.add(cur)
        path.append(cur)
        cur = parents[cur]
    path.reverse()

    subtree = []
    stack = [slug]
    while stack:
        cur = stack.pop()
        subtree.append(cur)
        stack.extend(children[cur])

    product_count = bundle_count = total = in_stock = 0
    for cat in subtree:
        for _slug, price, stock in snap["products"].get(cat, []):
            product_count += 1
            total += price
            if stock:
                in_stock += 1
        for _slug, price in snap["bundles"].get(cat, []):
            bundle_count += 1
            total += price

    def deepest(node):
        kids = children[node]
        if not kids:
            return [node]
        best = None
        for kid in kids:
            cand = [node] + deepest(kid)
            if (
                best is None
                or len(cand) > len(best)
                or (len(cand) == len(best) and cand < best)
            ):
                best = cand
        return best

    return {
        "slug": slug,
        "path": path,
        "depth": len(path) - 1,
        "parent": parents[slug],
        "children": children[slug],
        "audit_checked_by": snap["audits"].get(slug),
        "rollup": {
            "category_count": len(subtree),
            "product_count": product_count,
            "bundle_count": bundle_count,
            "listing_count": product_count + bundle_count,
            "price_cents_total": total,
            "in_stock_product_count": in_stock,
        },
        "deepest_branch": deepest(slug),
    }


def parent_of(slug):
    row = gel_one(
        "select Category { parent_slug := .parent.slug } filter .slug = '%s'" % slug
    )
    return row.get("parent_slug")


# ===========================================================================
# A. schema deliverable, inspected directly in the database
# ===========================================================================


def _category_pointers():
    row = gel_one(
        "select schema::ObjectType { "
        "  pointers: { name, cardinality, target_name := .target.name } "
        "} filter .name = 'default::Category'"
    )
    return dict((p["name"], p) for p in row["pointers"])


def test_report_script_exists():
    assert os.path.isfile(REPORT_SCRIPT), "Expected the CLI at %s." % REPORT_SCRIPT


def test_category_children_pointer_declared(gel_server):
    pointers = _category_pointers()
    assert "children" in pointers, (
        "default::Category does not declare a `children` pointer: %s" % sorted(pointers)
    )
    ptr = pointers["children"]
    assert ptr["target_name"] == "default::Category", (
        "`Category.children` must target default::Category, found %r." % (ptr,)
    )
    assert ptr["cardinality"] == "Many", (
        "`Category.children` must allow many objects, found cardinality %r." % (ptr,)
    )


def test_category_products_pointer_declared(gel_server):
    pointers = _category_pointers()
    assert "products" in pointers, (
        "default::Category does not declare a `products` pointer: %s" % sorted(pointers)
    )
    ptr = pointers["products"]
    assert ptr["target_name"] == "default::Product", (
        "`Category.products` must target default::Product only, found %r." % (ptr,)
    )
    assert ptr["cardinality"] == "Many", (
        "`Category.products` must allow many objects, found cardinality %r." % (ptr,)
    )


def test_category_audit_pointer_is_single_valued(gel_server):
    pointers = _category_pointers()
    assert "audit" in pointers, (
        "default::Category does not declare an `audit` pointer: %s" % sorted(pointers)
    )
    ptr = pointers["audit"]
    assert ptr["target_name"] == "default::CategoryAudit", (
        "`Category.audit` must target default::CategoryAudit, found %r." % (ptr,)
    )
    assert ptr["cardinality"] == "One", (
        "`Category.audit` must resolve to at most one object (cardinality One), found %r."
        % (ptr,)
    )


def test_category_parent_link_unchanged(gel_server):
    pointers = _category_pointers()
    assert "parent" in pointers, "The seeded `Category.parent` link is gone."
    ptr = pointers["parent"]
    assert ptr["target_name"] == "default::Category" and ptr["cardinality"] == "One", (
        "`Category.parent` must still be a single link to default::Category, found %r."
        % (ptr,)
    )


def test_no_extra_pointers_were_added(gel_server):
    expected = {
        "default::Category": {
            "slug",
            "label",
            "parent",
            "rank",
            "children",
            "products",
            "audit",
        },
        "default::Product": {"slug", "label", "category", "price_cents", "in_stock"},
        "default::Bundle": {"slug", "label", "category", "price_cents", "item_count"},
        "default::CategoryAudit": {"category", "checked_by"},
    }
    for type_name, wanted in expected.items():
        row = gel_one(
            "select schema::ObjectType { pointers: { name } } filter .name = '%s'"
            % type_name
        )
        actual = set(
            p["name"] for p in row["pointers"] if p["name"] not in ("id", "__type__")
        )
        assert actual == wanted, (
            "%s must declare exactly the pointers %s, found %s."
            % (type_name, sorted(wanted), sorted(actual))
        )


def test_instance_schema_is_in_sync_with_schema_dir(gel_server):
    proc = _run(["gel", "migration", "status", "--quiet"], timeout=300)
    assert proc.returncode == 0, (
        "`gel migration status --quiet` returned %s: the instance schema is not in sync "
        "with %s.\nstdout: %s\nstderr: %s"
        % (proc.returncode, SCHEMA_DIR, proc.stdout, proc.stderr)
    )


def test_migration_history_grew(gel_server):
    migrations = sorted(
        name for name in os.listdir(MIGRATIONS_DIR) if name.endswith(".edgeql")
    )
    assert len(migrations) >= 2, (
        "Expected the schema change to be recorded as a new migration in %s, found %r."
        % (MIGRATIONS_DIR, migrations)
    )


def test_children_pointer_resolves_from_database(gel_server):
    row = gel_one(
        "select Category { children: { slug } order by .slug } filter .slug = 'headphones'"
    )
    slugs = [child["slug"] for child in row["children"]]
    assert slugs == ["wired", "wireless"], (
        "`headphones.children` queried directly must be {wired, wireless}, got %r." % (slugs,)
    )


def test_products_pointer_excludes_bundles(gel_server):
    row = gel_one(
        "select Category { products: { slug } order by .slug } filter .slug = 'wireless'"
    )
    slugs = [product["slug"] for product in row["products"]]
    assert slugs == ["hp-wl-1"], (
        "`wireless.products` must contain only the product hp-wl-1 (never the bundle "
        "wl-bundle), got %r." % (slugs,)
    )


def test_audit_pointer_returns_single_object_or_nothing(gel_server):
    row = gel_one("select Category { audit: { checked_by } } filter .slug = 'audio'")
    audit = row["audit"]
    assert isinstance(audit, dict), (
        "`audio.audit` must serialise as a single object, got %r." % (audit,)
    )
    assert audit["checked_by"] == "qa-anna", (
        "`audio.audit.checked_by` must be 'qa-anna', got %r." % (audit,)
    )
    row = gel_one("select Category { audit: { checked_by } } filter .slug = 'storage'")
    assert not row["audit"], (
        "`storage.audit` must be empty because no audit row exists, got %r." % (row,)
    )


def test_audit_exclusive_constraint_still_enforced(gel_server):
    proc = gel_exec_expect_failure(
        "insert CategoryAudit { "
        "category := assert_single((select Category filter .slug = 'audio')), "
        "checked_by := 'dup' }"
    )
    assert proc.returncode != 0, (
        "Inserting a second CategoryAudit for `audio` must fail; the exclusive constraint "
        "on CategoryAudit.category was removed."
    )
    count = gel_one("select count(CategoryAudit)")
    assert count == 2, "Expected 2 CategoryAudit rows after the rejected insert, got %s." % count


# ===========================================================================
# B. regression: the seeded dataset survived unchanged
# ===========================================================================


def test_seeded_counts_unchanged(gel_server):
    for type_name, expected in (
        ("Category", 854),
        ("Product", 1609),
        ("Bundle", 2),
        ("CategoryAudit", 2),
    ):
        actual = gel_one("select count(%s)" % type_name)
        assert actual == expected, (
            "Seeded data changed: expected %d %s objects, found %s."
            % (expected, type_name, actual)
        )


def test_seeded_tree_structure_unchanged(gel_server):
    snap = load_snapshot()
    parents = snap["parents"]
    for slug, parent in EXPLICIT_TREE.items():
        assert slug in parents, "Seeded category %s is missing." % slug
        assert parents[slug] == parent, (
            "Seeded category %s must still have parent %r, found %r."
            % (slug, parent, parents[slug])
        )
    for i in range(40):
        slug = "spine-%d" % i
        expected = ("spine-%d" % (i - 1)) if i else None
        assert parents.get(slug) == expected, (
            "Generated category %s must still have parent %r, found %r."
            % (slug, expected, parents.get(slug))
        )
        for j in range(20):
            bin_slug = "bin-%d-%d" % (i, j)
            assert parents.get(bin_slug) == slug, (
                "Generated category %s must still have parent %s, found %r."
                % (bin_slug, slug, parents.get(bin_slug))
            )


# ===========================================================================
# C. report mode, happy paths
# ===========================================================================


def test_report_audio(gel_server):
    doc, _ = report_ok(["--slug", "audio"])
    assert doc["slug"] == "audio", doc
    assert doc["path"] == ["electronics", "audio"], "Wrong path for audio: %r" % (doc["path"],)
    assert doc["depth"] == 1, "Wrong depth for audio: %r" % (doc["depth"],)
    assert doc["parent"] == "electronics", "Wrong parent for audio: %r" % (doc["parent"],)
    assert doc["children"] == ["headphones", "speakers"], (
        "Wrong children for audio: %r" % (doc["children"],)
    )
    assert doc["audit_checked_by"] == "qa-anna", (
        "Wrong audit_checked_by for audio: %r" % (doc["audit_checked_by"],)
    )
    assert rollup(doc) == (5, 5, 2, 7, 80900, 3), "Wrong rollup for audio: %r" % (doc["rollup"],)
    assert doc["deepest_branch"] == ["audio", "headphones", "wired"], (
        "Wrong deepest_branch for audio: %r" % (doc["deepest_branch"],)
    )


def test_report_electronics_root(gel_server):
    doc, _ = report_ok(["--slug", "electronics"])
    assert doc["path"] == ["electronics"], "Wrong path for electronics: %r" % (doc["path"],)
    assert doc["depth"] == 0, "Wrong depth for electronics: %r" % (doc["depth"],)
    assert doc["parent"] is None, "electronics has no parent, got %r." % (doc["parent"],)
    assert doc["children"] == ["audio", "cameras", "storage"], (
        "Wrong children for electronics: %r" % (doc["children"],)
    )
    assert doc["audit_checked_by"] == "qa-bob", (
        "Wrong audit_checked_by for electronics: %r" % (doc["audit_checked_by"],)
    )
    assert rollup(doc) == (10, 9, 2, 11, 190400, 6), (
        "Wrong rollup for electronics (subtree aggregates must include every depth): %r"
        % (doc["rollup"],)
    )
    assert doc["deepest_branch"] == ["electronics", "audio", "headphones", "wired"], (
        "Wrong deepest_branch for electronics: %r" % (doc["deepest_branch"],)
    )


def test_report_headphones_tie_break(gel_server):
    doc, _ = report_ok(["--slug", "headphones"])
    assert doc["depth"] == 2, "Wrong depth for headphones: %r" % (doc["depth"],)
    assert doc["children"] == ["wired", "wireless"], (
        "Wrong children for headphones: %r" % (doc["children"],)
    )
    assert rollup(doc) == (3, 4, 1, 5, 64000, 3), (
        "Wrong rollup for headphones: %r" % (doc["rollup"],)
    )
    assert doc["deepest_branch"] == ["headphones", "wired"], (
        "headphones has two equally deep branches; the tie must be broken by code point "
        "order, so ['headphones', 'wired'] is expected, got %r." % (doc["deepest_branch"],)
    )


def test_report_wireless_leaf(gel_server):
    doc, _ = report_ok(["--slug", "wireless"])
    assert doc["path"] == ["electronics", "audio", "headphones", "wireless"], (
        "Wrong path for wireless: %r" % (doc["path"],)
    )
    assert doc["depth"] == 3, "Wrong depth for wireless: %r" % (doc["depth"],)
    assert doc["children"] == [], "wireless has no children, got %r." % (doc["children"],)
    assert doc["audit_checked_by"] is None, (
        "wireless has no audit row, got %r." % (doc["audit_checked_by"],)
    )
    assert rollup(doc) == (1, 1, 1, 2, 40000, 1), (
        "Wrong rollup for wireless: %r" % (doc["rollup"],)
    )
    assert doc["deepest_branch"] == ["wireless"], (
        "Wrong deepest_branch for wireless: %r" % (doc["deepest_branch"],)
    )


def test_report_storage_empty_subtree(gel_server):
    doc, _ = report_ok(["--slug", "storage"])
    assert doc["children"] == ["movable"], "Wrong children for storage: %r" % (doc["children"],)
    assert rollup(doc) == (2, 0, 0, 0, 0, 0), (
        "storage's subtree holds no listings, so every count must be 0: %r" % (doc["rollup"],)
    )
    assert doc["deepest_branch"] == ["storage", "movable"], (
        "Wrong deepest_branch for storage: %r" % (doc["deepest_branch"],)
    )


def test_report_sandbox_isolated_root(gel_server):
    doc, _ = report_ok(["--slug", "sandbox"])
    assert doc["path"] == ["sandbox"], "Wrong path for sandbox: %r" % (doc["path"],)
    assert doc["depth"] == 0, "Wrong depth for sandbox: %r" % (doc["depth"],)
    assert doc["parent"] is None, "sandbox has no parent, got %r." % (doc["parent"],)
    assert doc["children"] == [], "sandbox has no children, got %r." % (doc["children"],)
    assert doc["audit_checked_by"] is None, (
        "sandbox has no audit row, got %r." % (doc["audit_checked_by"],)
    )
    assert rollup(doc) == (1, 0, 0, 0, 0, 0), "Wrong rollup for sandbox: %r" % (doc["rollup"],)
    assert doc["deepest_branch"] == ["sandbox"], (
        "Wrong deepest_branch for sandbox: %r" % (doc["deepest_branch"],)
    )


def test_report_deep_generated_leaf(gel_server):
    doc, _ = report_ok(["--slug", "bin-39-19"])
    assert doc["depth"] == 40, "bin-39-19 sits 40 levels deep, got %r." % (doc["depth"],)
    assert len(doc["path"]) == 41 and doc["path"][-1] == "bin-39-19", (
        "Wrong path for bin-39-19: %r" % (doc["path"],)
    )
    assert doc["path"][0] == "spine-0", "Wrong root for bin-39-19: %r" % (doc["path"][:3],)
    assert rollup(doc) == (1, 2, 0, 2, 8381, 2), (
        "Wrong rollup for bin-39-19: %r" % (doc["rollup"],)
    )
    assert doc["deepest_branch"] == ["bin-39-19"], (
        "Wrong deepest_branch for bin-39-19: %r" % (doc["deepest_branch"],)
    )


def test_report_largest_subtree_matches_independent_oracle(gel_server):
    snap = load_snapshot()
    expected = oracle_report(snap, "spine-0")
    doc, _ = report_ok(["--slug", "spine-0"])
    assert doc["path"] == expected["path"], "Wrong path for spine-0: %r" % (doc["path"],)
    assert doc["depth"] == expected["depth"], "Wrong depth for spine-0: %r" % (doc["depth"],)
    assert doc["parent"] == expected["parent"], "Wrong parent for spine-0: %r" % (doc["parent"],)
    assert sorted(doc["children"]) == sorted(expected["children"]), (
        "spine-0's direct children do not match the database: %r" % (doc["children"],)
    )
    assert doc["rollup"] == expected["rollup"], (
        "spine-0's subtree rollup must match the values recomputed from the raw rows %r, "
        "got %r." % (expected["rollup"], doc["rollup"])
    )
    assert doc["rollup"]["category_count"] == 840, (
        "spine-0's subtree holds 840 categories, got %r." % (doc["rollup"],)
    )
    assert doc["deepest_branch"] == expected["deepest_branch"], (
        "spine-0's deepest branch must be %r, got %r."
        % (expected["deepest_branch"], doc["deepest_branch"])
    )
    assert len(doc["deepest_branch"]) == 41, (
        "spine-0's deepest branch is 41 nodes long, got %d." % len(doc["deepest_branch"])
    )


def test_report_is_repeatable(gel_server):
    _, first = report_ok(["--slug", "audio"])
    _, second = report_ok(["--slug", "audio"])
    _, third = report_ok(["--slug", "audio"])
    assert first == second == third, (
        "Re-running the same report must produce identical stdout; got %r / %r / %r."
        % (first, second, third)
    )


# ===========================================================================
# D. failure paths
# ===========================================================================


def test_unknown_slug_is_rejected(gel_server):
    report_fails(
        ["--slug", "nope-nope"],
        4,
        {"error": "unknown_slug", "slug": "nope-nope"},
    )


def test_product_slug_is_not_a_category(gel_server):
    report_fails(
        ["--slug", "cam-1"],
        5,
        {"error": "not_a_category", "slug": "cam-1"},
    )


def test_bundle_slug_is_not_a_category(gel_server):
    report_fails(
        ["--slug", "audio-starter"],
        5,
        {"error": "not_a_category", "slug": "audio-starter"},
    )


def test_cycle_inside_loop_is_detected(gel_server):
    report_fails(
        ["--slug", "loop-a"],
        3,
        {"error": "cycle_detected", "slug": "loop-a"},
    )


def test_cycle_above_node_is_detected(gel_server):
    report_fails(
        ["--slug", "loop-c"],
        3,
        {"error": "cycle_detected", "slug": "loop-c"},
    )


# ===========================================================================
# E. move mode
# ===========================================================================


def test_reparent_moves_category_and_reports_new_position(gel_server):
    try:
        doc, _ = report_ok(["--slug", "movable", "--reparent", "cameras"])
        assert doc["path"] == ["electronics", "cameras", "movable"], (
            "After the move, movable's path must be electronics/cameras/movable, got %r."
            % (doc["path"],)
        )
        assert doc["depth"] == 2, "Wrong depth after the move: %r" % (doc["depth"],)
        assert doc["parent"] == "cameras", "Wrong parent after the move: %r" % (doc["parent"],)
        assert parent_of("movable") == "cameras", (
            "The database still does not link movable to cameras after the move."
        )
        row = gel_one(
            "select Category { children: { slug } order by .slug } filter .slug = 'cameras'"
        )
        slugs = [child["slug"] for child in row["children"]]
        assert slugs == ["lenses", "movable"], (
            "cameras' children must be {lenses, movable} after the move, got %r." % (slugs,)
        )
    finally:
        restored, _ = report_ok(["--slug", "movable", "--reparent", "storage"])
        assert restored["parent"] == "storage", (
            "Moving movable back under storage failed: %r" % (restored,)
        )
    assert parent_of("movable") == "storage", (
        "movable must be back under storage after the second move."
    )
    doc, _ = report_ok(["--slug", "storage"])
    assert doc["children"] == ["movable"] and rollup(doc) == (2, 0, 0, 0, 0, 0), (
        "The storage report must be identical to its pre-move state, got %r." % (doc,)
    )


def test_reparent_into_own_subtree_is_rejected(gel_server):
    report_fails(
        ["--slug", "electronics", "--reparent", "movable"],
        6,
        {"error": "would_create_cycle", "slug": "electronics", "reparent": "movable"},
    )
    assert parent_of("electronics") is None, (
        "The rejected move must not have given electronics a parent."
    )
    assert parent_of("movable") == "storage", (
        "The rejected move must not have touched movable's parent."
    )


def test_reparent_onto_itself_is_rejected(gel_server):
    report_fails(
        ["--slug", "audio", "--reparent", "audio"],
        6,
        {"error": "would_create_cycle", "slug": "audio", "reparent": "audio"},
    )
    assert parent_of("audio") == "electronics", (
        "The rejected self-move must not have changed audio's parent."
    )


def test_reparent_to_unknown_slug_is_rejected(gel_server):
    report_fails(
        ["--slug", "sandbox", "--reparent", "nope-nope"],
        4,
        {"error": "unknown_slug", "slug": "nope-nope"},
    )
    assert parent_of("sandbox") is None, (
        "The rejected move must not have given sandbox a parent."
    )


def test_reparent_to_non_category_is_rejected(gel_server):
    report_fails(
        ["--slug", "sandbox", "--reparent", "hp-alpha"],
        5,
        {"error": "not_a_category", "slug": "hp-alpha"},
    )
    assert parent_of("sandbox") is None, (
        "The rejected move must not have given sandbox a parent."
    )


def test_reparent_onto_looped_branch_is_rejected(gel_server):
    report_fails(
        ["--slug", "sandbox", "--reparent", "loop-a"],
        3,
        {"error": "cycle_detected", "slug": "loop-a"},
    )
    assert parent_of("sandbox") is None, (
        "The rejected move must not have given sandbox a parent."
    )
    assert parent_of("loop-a") == "loop-b" and parent_of("loop-b") == "loop-a", (
        "The rejected move must not have altered the looped legacy categories."
    )


def test_reparent_of_looped_category_is_rejected(gel_server):
    report_fails(
        ["--slug", "loop-a", "--reparent", "sandbox"],
        3,
        {"error": "cycle_detected", "slug": "loop-a"},
    )
    assert parent_of("loop-a") == "loop-b", (
        "The rejected move must not have changed loop-a's parent."
    )


# ===========================================================================
# F. anti-hardcoding: rows inserted directly into the database change the answers
# ===========================================================================


def _cleanup_probe():
    gel_exec("delete Listing filter .slug in {'zz-prod', 'zz-bundle'}")
    gel_exec("delete CategoryAudit filter .category.slug = 'sandbox'")
    gel_exec("delete Category filter .slug = 'zz-probe'")


def test_directly_inserted_rows_flow_through_computed_pointers(gel_server):
    try:
        gel_exec(
            "insert Category { slug := 'zz-probe', label := 'ZZ Probe', rank := 0, "
            "parent := assert_single((select Category filter .slug = 'sandbox')) }"
        )
        gel_exec(
            "insert Product { slug := 'zz-prod', label := 'ZZ Product', "
            "price_cents := 700, in_stock := true, "
            "category := assert_single((select Category filter .slug = 'zz-probe')) }"
        )
        gel_exec(
            "insert Bundle { slug := 'zz-bundle', label := 'ZZ Bundle', "
            "price_cents := 1300, item_count := 2, "
            "category := assert_single((select Category filter .slug = 'zz-probe')) }"
        )
        gel_exec(
            "insert CategoryAudit { "
            "category := assert_single((select Category filter .slug = 'sandbox')), "
            "checked_by := 'qa-zoe' }"
        )

        row = gel_one(
            "select Category { children: { slug } } filter .slug = 'sandbox'"
        )
        assert [child["slug"] for child in row["children"]] == ["zz-probe"], (
            "`sandbox.children` must pick up the directly inserted category, got %r." % (row,)
        )
        row = gel_one(
            "select Category { products: { slug } } filter .slug = 'zz-probe'"
        )
        assert [product["slug"] for product in row["products"]] == ["zz-prod"], (
            "`zz-probe.products` must contain only zz-prod, got %r." % (row,)
        )
        row = gel_one("select Category { audit: { checked_by } } filter .slug = 'sandbox'")
        assert row["audit"] and row["audit"]["checked_by"] == "qa-zoe", (
            "`sandbox.audit` must pick up the directly inserted audit row, got %r." % (row,)
        )

        doc, _ = report_ok(["--slug", "sandbox"])
        assert doc["children"] == ["zz-probe"], (
            "The report must reflect the directly inserted category, got %r." % (doc["children"],)
        )
        assert doc["audit_checked_by"] == "qa-zoe", (
            "The report must reflect the directly inserted audit row, got %r."
            % (doc["audit_checked_by"],)
        )
        assert rollup(doc) == (2, 1, 1, 2, 2000, 1), (
            "The sandbox rollup must account for the directly inserted rows, got %r."
            % (doc["rollup"],)
        )
        assert doc["deepest_branch"] == ["sandbox", "zz-probe"], (
            "Wrong deepest_branch for sandbox after the insert: %r" % (doc["deepest_branch"],)
        )

        snap = load_snapshot()
        expected = oracle_report(snap, "sandbox")
        assert doc["rollup"] == expected["rollup"], (
            "The sandbox rollup disagrees with the values recomputed from the raw rows %r: %r"
            % (expected["rollup"], doc["rollup"])
        )

        child, _ = report_ok(["--slug", "zz-probe"])
        assert child["path"] == ["sandbox", "zz-probe"], (
            "Wrong path for zz-probe: %r" % (child["path"],)
        )
        assert child["depth"] == 1, "Wrong depth for zz-probe: %r" % (child["depth"],)
        assert rollup(child) == (1, 1, 1, 2, 2000, 1), (
            "Wrong rollup for zz-probe: %r" % (child["rollup"],)
        )
    finally:
        _cleanup_probe()


def test_state_returns_to_baseline_after_probe_rows_are_removed(gel_server):
    count = gel_one("select count(Category)")
    assert count == 854, (
        "After removing the probe rows the database must hold 854 categories again, got %s."
        % count
    )
    doc, _ = report_ok(["--slug", "sandbox"])
    assert doc["children"] == [], (
        "sandbox must have no children again, got %r." % (doc["children"],)
    )
    assert doc["audit_checked_by"] is None, (
        "sandbox must have no audit row again, got %r." % (doc["audit_checked_by"],)
    )
    assert rollup(doc) == (1, 0, 0, 0, 0, 0), (
        "The sandbox rollup must be back to its baseline, got %r." % (doc["rollup"],)
    )
    assert doc["deepest_branch"] == ["sandbox"], (
        "Wrong deepest_branch for sandbox after cleanup: %r" % (doc["deepest_branch"],)
    )
