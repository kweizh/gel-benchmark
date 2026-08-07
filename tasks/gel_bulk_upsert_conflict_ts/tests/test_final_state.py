import glob
import json
import os
import shutil
import subprocess
import time

import pytest

PROJECT_DIR = "/home/user/catalog-sync"
DIST_ENTRY = os.path.join(PROJECT_DIR, "dist", "sync.js")
GEL_START = "/usr/local/bin/gel-start.sh"
BATCH_DIR = "/tmp/verify-batches"

BASE_BATCH = [
    {
        "sku": "P-001",
        "name": "Hammer",
        "price_cents": 1999,
        "stock": 10,
        "category": "tools",
        "tags": ["metal", "sale"],
    },
    {
        "sku": "P-002",
        "name": "Wrench",
        "price_cents": 2599,
        "stock": 5,
        "category": "tools",
        "tags": ["metal"],
    },
    {
        "sku": "P-003",
        "name": "Screwdriver",
        "price_cents": 899,
        "stock": 40,
        "category": "tools",
        "tags": ["metal", "metal", "clearance"],
    },
    {
        "sku": "P-004",
        "name": "Notebook",
        "price_cents": 450,
        "stock": 120,
        "category": "office",
        "tags": ["paper"],
    },
    {
        "sku": "P-005",
        "name": "Pen",
        "price_cents": 150,
        "stock": 300,
        "category": "office",
        "tags": [],
    },
    {
        "sku": "P-006",
        "name": "Stapler",
        "price_cents": 1200,
        "stock": 25,
        "category": "office",
    },
    {
        "sku": "P-007",
        "name": "Mug",
        "price_cents": 999,
        "stock": 60,
        "category": "kitchen",
        "tags": ["sale", "ceramic"],
    },
    {
        "sku": "P-008",
        "name": "Kettle",
        "price_cents": 4599,
        "stock": 12,
        "category": "kitchen",
        "tags": ["sale"],
    },
]

DELTA_BATCH = [
    {
        "sku": "P-001",
        "name": "Hammer",
        "price_cents": 1999,
        "stock": 10,
        "category": "tools",
        "tags": ["metal", "sale"],
    },
    {
        "sku": "P-002",
        "name": "Wrench",
        "price_cents": 2799,
        "stock": 4,
        "category": "tools",
        "tags": ["metal"],
    },
    {
        "sku": "P-007",
        "name": "Large Mug",
        "price_cents": 999,
        "stock": 60,
        "category": "kitchenware",
        "tags": ["ceramic"],
    },
    {
        "sku": "P-009",
        "name": "Ladle",
        "price_cents": 700,
        "stock": 9,
        "category": "kitchenware",
        "tags": ["metal", "new"],
    },
]


def _perf_batch():
    batch = []
    for n in range(1, 201):
        batch.append(
            {
                "sku": f"PERF-{n:04d}",
                "name": f"Item {n}",
                "price_cents": 100 + n,
                "stock": n,
                "category": f"bulk-{n % 5}",
                "tags": ["bulk", f"grp-{n % 7}"],
            }
        )
    return batch


@pytest.fixture(scope="session")
def gel_instance():
    proc = subprocess.run(
        ["bash", GEL_START], capture_output=True, text=True, timeout=600
    )
    print("gel-start.sh stdout:\n" + proc.stdout)
    print("gel-start.sh stderr:\n" + proc.stderr)
    assert proc.returncode == 0, (
        f"Failed to start the local Gel instance via {GEL_START}: "
        f"rc={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    return True


@pytest.fixture(scope="session")
def batch_dir(gel_instance):
    if os.path.isdir(BATCH_DIR):
        shutil.rmtree(BATCH_DIR)
    os.makedirs(BATCH_DIR, exist_ok=True)
    return BATCH_DIR


def gel_query(query, fmt="json"):
    proc = subprocess.run(
        ["gel", "query", "-F", fmt, query],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, (
        f"'gel query' failed for {query!r}: rc={proc.returncode} "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    return json.loads(proc.stdout or "null")


def gel_scalar(query):
    result = gel_query(query)
    assert isinstance(result, list) and len(result) == 1, (
        f"Expected a single value from {query!r}, got {result!r}"
    )
    return result[0]


def gel_query_raw(query):
    return subprocess.run(
        ["gel", "query", query],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=180,
    )


def reset_catalog():
    proc = subprocess.run(
        ["gel", "query", "delete Product", "delete Tag", "delete Category"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, (
        "Failed to reset catalog data before a scenario: "
        f"rc={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )


@pytest.fixture()
def clean_db(batch_dir):
    reset_catalog()
    return True


def write_batch(name, payload_text):
    path = os.path.join(BATCH_DIR, name)
    with open(path, "w") as handle:
        handle.write(payload_text)
    return path


def write_json_batch(name, payload):
    return write_batch(name, json.dumps(payload))


def run_sync(args, timeout=300):
    started = time.monotonic()
    proc = subprocess.run(
        ["node", DIST_ENTRY] + list(args),
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    elapsed = time.monotonic() - started
    return proc, elapsed


def parse_stdout_json(proc):
    text = proc.stdout.strip()
    assert text, (
        "The sync CLI printed nothing on stdout; "
        f"rc={proc.returncode} stderr={proc.stderr!r}"
    )
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            "stdout must contain exactly one JSON document, got "
            f"{proc.stdout!r} (parse error: {exc}); stderr={proc.stderr!r}"
        )
    assert isinstance(payload, dict), (
        f"stdout JSON document must be an object, got {payload!r}"
    )
    return payload


def run_ok(batch_name, payload, timeout=300):
    path = write_json_batch(batch_name, payload)
    proc, elapsed = run_sync(["--input", path], timeout=timeout)
    assert proc.returncode == 0, (
        f"Expected exit code 0 for batch {batch_name}, got {proc.returncode}; "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    result = parse_stdout_json(proc)
    assert result.get("ok") is True, f"Expected \"ok\": true, got {result!r}"
    return result, elapsed


def counts():
    return {
        "products": gel_scalar("select count(Product)"),
        "categories": gel_scalar("select count(Category)"),
        "tags": gel_scalar("select count(Tag)"),
    }


def product(sku):
    rows = gel_query(
        "select Product { sku, name, price_cents, stock, "
        "category: { name }, tags: { label } } "
        f"filter .sku = '{sku}'"
    )
    assert len(rows) <= 1, f"More than one Product row for sku {sku}: {rows}"
    return rows[0] if rows else None


def tag_labels():
    return sorted(gel_query("select Tag.label"))


def category_names():
    return sorted(gel_query("select Category.name"))


# ---------------------------------------------------------------------------
# 1. Project artifacts and migration state
# ---------------------------------------------------------------------------


def test_project_artifacts_exist():
    assert os.path.isfile(os.path.join(PROJECT_DIR, "gel.toml")), (
        f"{PROJECT_DIR}/gel.toml is missing."
    )
    assert os.path.isfile(os.path.join(PROJECT_DIR, "dbschema", "default.gel")), (
        f"{PROJECT_DIR}/dbschema/default.gel is missing."
    )
    migrations = sorted(
        glob.glob(os.path.join(PROJECT_DIR, "dbschema", "migrations", "*.edgeql"))
    )
    assert migrations, (
        "No migration file matching dbschema/migrations/*.edgeql was found; "
        "the schema must be applied through the migration engine."
    )
    assert os.path.isfile(DIST_ENTRY), (
        f"The compiled CLI entrypoint {DIST_ENTRY} does not exist."
    )


def test_migration_state_is_in_sync(gel_instance):
    proc = subprocess.run(
        ["gel", "migration", "status"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=180,
    )
    combined = (proc.stdout + proc.stderr).lower()
    assert proc.returncode == 0, (
        "'gel migration status' reports the database is not in sync with "
        f"dbschema/migrations: rc={proc.returncode} stdout={proc.stdout!r} "
        f"stderr={proc.stderr!r}"
    )
    assert "up to date" in combined or "up-to-date" in combined, (
        f"Unexpected 'gel migration status' output: {proc.stdout!r} {proc.stderr!r}"
    )
    files = sorted(
        glob.glob(os.path.join(PROJECT_DIR, "dbschema", "migrations", "*.edgeql"))
    )
    applied = gel_scalar("select count(schema::Migration)")
    assert applied >= 1, (
        "No migration has been applied to the database; the catalog schema must "
        "reach the instance through the migration engine."
    )
    assert applied == len(files), (
        f"The database reports {applied} applied migration(s) but "
        f"dbschema/migrations holds {len(files)} migration file(s)."
    )


# ---------------------------------------------------------------------------
# 2. Schema shape
# ---------------------------------------------------------------------------


def _schema_pointers(type_name):
    rows = gel_query(
        "select schema::ObjectType { "
        "  name, "
        "  props := (select .pointers[is schema::Property] "
        "            { name, required, cardinality, target: { name } }), "
        "  links := (select .pointers[is schema::Link] "
        "            { name, required, cardinality, target: { name } }) "
        "} "
        f"filter .name = '{type_name}'"
    )
    assert rows, f"Object type {type_name} does not exist in the database schema."
    row = rows[0]
    props = {p["name"]: p for p in row["props"]}
    links = {l["name"]: l for l in row["links"]}
    return props, links


def test_category_and_tag_schema(gel_instance):
    props, _ = _schema_pointers("default::Category")
    assert "name" in props, "default::Category has no property 'name'."
    assert props["name"]["required"] is True, "Category.name must be required."
    assert props["name"]["cardinality"].lower() == "one", (
        f"Category.name must be single, got cardinality {props['name']['cardinality']!r}."
    )
    assert props["name"]["target"]["name"] == "std::str", (
        f"Category.name must be std::str, got {props['name']['target']['name']!r}."
    )

    props, _ = _schema_pointers("default::Tag")
    assert "label" in props, "default::Tag has no property 'label'."
    assert props["label"]["required"] is True, "Tag.label must be required."
    assert props["label"]["cardinality"].lower() == "one", (
        f"Tag.label must be single, got cardinality {props['label']['cardinality']!r}."
    )
    assert props["label"]["target"]["name"] == "std::str", (
        f"Tag.label must be std::str, got {props['label']['target']['name']!r}."
    )


def test_product_schema_properties_and_links(gel_instance):
    props, links = _schema_pointers("default::Product")

    for name, target in (
        ("sku", "std::str"),
        ("name", "std::str"),
        ("price_cents", "std::int64"),
        ("stock", "std::int64"),
    ):
        assert name in props, f"default::Product has no property {name!r}."
        assert props[name]["required"] is True, f"Product.{name} must be required."
        assert props[name]["cardinality"].lower() == "one", (
            f"Product.{name} must be single, got {props[name]['cardinality']!r}."
        )
        assert props[name]["target"]["name"] == target, (
            f"Product.{name} must target {target}, got {props[name]['target']['name']!r}."
        )

    assert "category" in links, "default::Product has no link 'category'."
    assert links["category"]["required"] is True, "Product.category must be required."
    assert links["category"]["cardinality"].lower() == "one", (
        f"Product.category must be a single link, got {links['category']['cardinality']!r}."
    )
    assert links["category"]["target"]["name"] == "default::Category", (
        f"Product.category must target default::Category, got "
        f"{links['category']['target']['name']!r}."
    )

    assert "tags" in links, "default::Product has no link 'tags'."
    assert links["tags"]["cardinality"].lower() == "many", (
        f"Product.tags must be a multi link, got {links['tags']['cardinality']!r}."
    )
    assert links["tags"]["target"]["name"] == "default::Tag", (
        f"Product.tags must target default::Tag, got {links['tags']['target']['name']!r}."
    )


def _has_exclusive(type_name, pointer_name):
    rows = gel_query(
        "select schema::ObjectType { "
        "  own_constraints := (select .constraints { name, subjectexpr }), "
        "  pointers: { name, constraints: { name } } "
        "} "
        f"filter .name = '{type_name}'"
    )
    assert rows, f"Object type {type_name} does not exist in the database schema."
    row = rows[0]
    for pointer in row["pointers"]:
        if pointer["name"] != pointer_name:
            continue
        if any(c["name"] == "std::exclusive" for c in pointer["constraints"]):
            return True
    for constraint in row["own_constraints"]:
        if constraint["name"] != "std::exclusive":
            continue
        expr = (constraint.get("subjectexpr") or "").replace(" ", "")
        if expr in (f".{pointer_name}", f"(.{pointer_name})"):
            return True
    return False


def test_exclusive_constraints_declared(gel_instance):
    assert _has_exclusive("default::Product", "sku"), (
        "Product.sku does not carry a std::exclusive constraint."
    )
    assert _has_exclusive("default::Category", "name"), (
        "Category.name does not carry a std::exclusive constraint."
    )
    assert _has_exclusive("default::Tag", "label"), (
        "Tag.label does not carry a std::exclusive constraint."
    )


def test_exclusive_constraints_enforced(clean_db):
    first = gel_query_raw("insert Category { name := 'uniq-check' }")
    assert first.returncode == 0, (
        f"Inserting a Category failed: {first.stdout!r} {first.stderr!r}"
    )
    second = gel_query_raw("insert Category { name := 'uniq-check' }")
    assert second.returncode != 0, (
        "Inserting a duplicate Category name succeeded; Category.name is not unique."
    )
    reset_catalog()


# ---------------------------------------------------------------------------
# 3. Happy path: first run
# ---------------------------------------------------------------------------


def test_first_run_inserts_all_records(clean_db):
    result, _ = run_ok("base.json", BASE_BATCH)
    assert result["total"] == 8, f"Expected total 8, got {result.get('total')!r}"
    assert result["inserted"] == 8, f"Expected inserted 8, got {result.get('inserted')!r}"
    assert result["updated"] == 0, f"Expected updated 0, got {result.get('updated')!r}"
    assert result["unchanged"] == 0, (
        f"Expected unchanged 0, got {result.get('unchanged')!r}"
    )

    results = result["results"]
    assert len(results) == 8, f"Expected 8 result entries, got {len(results)}"
    assert [r["sku"] for r in results] == [
        "P-001",
        "P-002",
        "P-003",
        "P-004",
        "P-005",
        "P-006",
        "P-007",
        "P-008",
    ], f"results must be sorted ascending by sku, got {[r['sku'] for r in results]}"
    assert all(r["outcome"] == "inserted" for r in results), (
        f"Every outcome must be 'inserted', got {[r['outcome'] for r in results]}"
    )

    by_sku = {r["sku"]: r for r in results}
    assert by_sku["P-003"]["tags"] == ["clearance", "metal"], (
        "P-003 tags must be de-duplicated and sorted as ['clearance', 'metal'], got "
        f"{by_sku['P-003']['tags']!r}"
    )
    assert by_sku["P-005"]["tags"] == [], (
        f"P-005 must report no tags, got {by_sku['P-005']['tags']!r}"
    )
    assert by_sku["P-006"]["tags"] == [], (
        f"P-006 must report no tags, got {by_sku['P-006']['tags']!r}"
    )
    assert by_sku["P-001"]["category"] == "tools", (
        f"P-001 category must be reported as 'tools', got {by_sku['P-001']['category']!r}"
    )


def test_first_run_database_state(clean_db):
    run_ok("base.json", BASE_BATCH)

    state = counts()
    assert state["products"] == 8, f"Expected 8 Product rows, got {state['products']}"
    assert state["categories"] == 3, (
        f"Expected 3 Category rows (deduplicated by name), got {state['categories']}"
    )
    assert category_names() == ["kitchen", "office", "tools"], (
        f"Unexpected Category names: {category_names()}"
    )
    assert state["tags"] == 5, f"Expected 5 Tag rows, got {state['tags']}"
    assert tag_labels() == ["ceramic", "clearance", "metal", "paper", "sale"], (
        f"Unexpected Tag labels: {tag_labels()}"
    )

    row = product("P-001")
    assert row is not None, "Product P-001 was not created."
    assert sorted(t["label"] for t in row["tags"]) == ["metal", "sale"], (
        f"P-001 tag labels must be ['metal', 'sale'], got {row['tags']!r}"
    )
    assert row["category"]["name"] == "tools", (
        f"P-001 category must be 'tools', got {row['category']!r}"
    )


# ---------------------------------------------------------------------------
# 4. Idempotency
# ---------------------------------------------------------------------------


def test_replaying_batch_is_idempotent(clean_db):
    run_ok("base.json", BASE_BATCH)
    before = counts()

    result, _ = run_ok("base.json", BASE_BATCH)
    assert result["total"] == 8, f"Expected total 8, got {result.get('total')!r}"
    assert result["inserted"] == 0, (
        f"Replaying the batch must insert nothing, got inserted={result.get('inserted')!r}"
    )
    assert result["updated"] == 0, (
        f"Replaying the batch must update nothing, got updated={result.get('updated')!r}"
    )
    assert result["unchanged"] == 8, (
        f"Replaying the batch must report 8 unchanged, got {result.get('unchanged')!r}"
    )
    assert all(r["outcome"] == "unchanged" for r in result["results"]), (
        f"Every outcome must be 'unchanged', got {[r['outcome'] for r in result['results']]}"
    )

    after = counts()
    assert after == before, (
        f"Replaying the batch changed the database: before={before} after={after}"
    )
    assert tag_labels() == ["ceramic", "clearance", "metal", "paper", "sale"], (
        f"Tag labels changed after the replay: {tag_labels()}"
    )


# ---------------------------------------------------------------------------
# 5. Partial updates
# ---------------------------------------------------------------------------


def test_partial_update_outcomes(clean_db):
    run_ok("base.json", BASE_BATCH)

    result, _ = run_ok("delta.json", DELTA_BATCH)
    assert result["total"] == 4, f"Expected total 4, got {result.get('total')!r}"
    assert result["inserted"] == 1, f"Expected inserted 1, got {result.get('inserted')!r}"
    assert result["updated"] == 2, f"Expected updated 2, got {result.get('updated')!r}"
    assert result["unchanged"] == 1, (
        f"Expected unchanged 1, got {result.get('unchanged')!r}"
    )

    results = result["results"]
    assert [r["sku"] for r in results] == ["P-001", "P-002", "P-007", "P-009"], (
        f"results must be sorted ascending by sku, got {[r['sku'] for r in results]}"
    )
    outcomes = {r["sku"]: r["outcome"] for r in results}
    assert outcomes["P-001"] == "unchanged", (
        f"P-001 was not modified and must be 'unchanged', got {outcomes['P-001']!r}"
    )
    assert outcomes["P-002"] == "updated", (
        f"P-002 changed price/stock and must be 'updated', got {outcomes['P-002']!r}"
    )
    assert outcomes["P-007"] == "updated", (
        f"P-007 changed name/category/tags and must be 'updated', got {outcomes['P-007']!r}"
    )
    assert outcomes["P-009"] == "inserted", (
        f"P-009 is new and must be 'inserted', got {outcomes['P-009']!r}"
    )


def test_partial_update_database_state(clean_db):
    run_ok("base.json", BASE_BATCH)
    run_ok("delta.json", DELTA_BATCH)

    state = counts()
    assert state["products"] == 9, f"Expected 9 Product rows, got {state['products']}"
    assert state["categories"] == 4, (
        f"Expected 4 Category rows, got {state['categories']}"
    )
    assert category_names() == ["kitchen", "kitchenware", "office", "tools"], (
        f"Unexpected Category names: {category_names()}"
    )
    assert state["tags"] == 6, f"Expected 6 Tag rows, got {state['tags']}"
    assert tag_labels() == [
        "ceramic",
        "clearance",
        "metal",
        "new",
        "paper",
        "sale",
    ], f"Unexpected Tag labels: {tag_labels()}"

    p002 = product("P-002")
    assert p002 is not None, "Product P-002 disappeared."
    assert p002["price_cents"] == 2799, (
        f"P-002 price_cents must be 2799, got {p002['price_cents']!r}"
    )
    assert p002["stock"] == 4, f"P-002 stock must be 4, got {p002['stock']!r}"

    p007 = product("P-007")
    assert p007 is not None, "Product P-007 disappeared."
    assert p007["name"] == "Large Mug", (
        f"P-007 name must be 'Large Mug', got {p007['name']!r}"
    )
    assert p007["category"]["name"] == "kitchenware", (
        f"P-007 category must be 'kitchenware', got {p007['category']!r}"
    )
    assert sorted(t["label"] for t in p007["tags"]) == ["ceramic"], (
        "P-007 must be linked to exactly the tag 'ceramic' after the update, got "
        f"{p007['tags']!r}"
    )

    p001 = product("P-001")
    assert p001 is not None, "Product P-001 disappeared."
    assert sorted(t["label"] for t in p001["tags"]) == ["metal", "sale"], (
        f"P-001 must still be linked to 'metal' and 'sale', got {p001['tags']!r}"
    )
    p008 = product("P-008")
    assert p008 is not None, "Product P-008 disappeared."
    assert sorted(t["label"] for t in p008["tags"]) == ["sale"], (
        f"P-008 must still be linked to 'sale', got {p008['tags']!r}"
    )


# ---------------------------------------------------------------------------
# 6. Boundary case: empty batch
# ---------------------------------------------------------------------------


def test_empty_batch_is_a_noop(clean_db):
    run_ok("base.json", BASE_BATCH)
    before = counts()

    result, _ = run_ok("empty.json", [])
    assert result["total"] == 0, f"Expected total 0, got {result.get('total')!r}"
    assert result["inserted"] == 0, f"Expected inserted 0, got {result.get('inserted')!r}"
    assert result["updated"] == 0, f"Expected updated 0, got {result.get('updated')!r}"
    assert result["unchanged"] == 0, (
        f"Expected unchanged 0, got {result.get('unchanged')!r}"
    )
    assert result["results"] == [], (
        f"Expected an empty results array, got {result['results']!r}"
    )

    after = counts()
    assert after == before, (
        f"An empty batch changed the database: before={before} after={after}"
    )


# ---------------------------------------------------------------------------
# 7. Error contract
# ---------------------------------------------------------------------------


def _assert_error(proc, expected_code, expected_error):
    assert proc.returncode == expected_code, (
        f"Expected exit code {expected_code}, got {proc.returncode}; "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    payload = parse_stdout_json(proc)
    assert payload.get("ok") is False, f"Expected \"ok\": false, got {payload!r}"
    assert payload.get("error_code") == expected_error, (
        f"Expected error_code {expected_error!r}, got {payload.get('error_code')!r}"
    )
    message = payload.get("message")
    assert isinstance(message, str) and message.strip(), (
        f"Expected a non-empty \"message\" string, got {message!r}"
    )
    return payload


def test_missing_input_argument(clean_db):
    proc, _ = run_sync([])
    _assert_error(proc, 2, "input_unreadable")


def test_missing_input_file(clean_db):
    missing = os.path.join(BATCH_DIR, "missing.json")
    if os.path.exists(missing):
        os.remove(missing)
    proc, _ = run_sync(["--input", missing])
    _assert_error(proc, 2, "input_unreadable")


def test_invalid_json_input_file(clean_db):
    path = write_batch("broken.json", "{not json")
    proc, _ = run_sync(["--input", path])
    _assert_error(proc, 2, "input_unreadable")


def test_top_level_value_is_not_an_array(clean_db):
    path = write_batch("not-array.json", json.dumps({"sku": "P-100"}))
    proc, _ = run_sync(["--input", path])
    payload = _assert_error(proc, 3, "invalid_record")
    assert "index" in payload, (
        f"Expected an \"index\" key in the error document, got {payload!r}"
    )
    assert payload["index"] is None, (
        f"Expected \"index\": null when the top-level value is not an array, got "
        f"{payload['index']!r}"
    )


def test_invalid_record_negative_price(clean_db):
    run_ok("base.json", BASE_BATCH)
    before = counts()

    batch = [
        {
            "sku": "P-201",
            "name": "Valid A",
            "price_cents": 100,
            "stock": 1,
            "category": "misc",
            "tags": ["x"],
        },
        {
            "sku": "P-202",
            "name": "Negative Price",
            "price_cents": -5,
            "stock": 1,
            "category": "misc",
            "tags": [],
        },
        {
            "sku": "P-203",
            "name": "Valid B",
            "price_cents": 300,
            "stock": 2,
            "category": "misc",
            "tags": [],
        },
    ]
    path = write_json_batch("invalid-price.json", batch)
    proc, _ = run_sync(["--input", path])
    payload = _assert_error(proc, 3, "invalid_record")
    assert payload.get("index") == 1, (
        f"Expected \"index\": 1 for the offending record, got {payload.get('index')!r}"
    )

    assert product("P-201") is None, "P-201 must not be written when the batch is invalid."
    assert product("P-203") is None, "P-203 must not be written when the batch is invalid."
    after = counts()
    assert after == before, (
        f"An invalid batch changed the database: before={before} after={after}"
    )


def test_invalid_record_missing_category(clean_db):
    batch = [
        {
            "sku": "P-211",
            "name": "Valid A",
            "price_cents": 100,
            "stock": 1,
            "category": "misc",
            "tags": [],
        },
        {"sku": "P-212", "name": "No Category", "price_cents": 100, "stock": 1},
        {
            "sku": "P-213",
            "name": "Valid B",
            "price_cents": 100,
            "stock": 1,
            "category": "misc",
            "tags": [],
        },
    ]
    path = write_json_batch("invalid-category.json", batch)
    proc, _ = run_sync(["--input", path])
    payload = _assert_error(proc, 3, "invalid_record")
    assert payload.get("index") == 1, (
        f"Expected \"index\": 1 for the offending record, got {payload.get('index')!r}"
    )
    assert counts()["products"] == 0, (
        "No products may be written when the batch contains an invalid record."
    )


def test_duplicate_sku_is_rejected(clean_db):
    batch = [
        {
            "sku": "P-301",
            "name": "First",
            "price_cents": 100,
            "stock": 1,
            "category": "dup",
            "tags": [],
        },
        {
            "sku": "P-302",
            "name": "Second",
            "price_cents": 200,
            "stock": 2,
            "category": "dup",
            "tags": [],
        },
        {
            "sku": "P-303",
            "name": "Third",
            "price_cents": 300,
            "stock": 3,
            "category": "dup",
            "tags": [],
        },
        {
            "sku": "P-301",
            "name": "First Again",
            "price_cents": 400,
            "stock": 4,
            "category": "dup",
            "tags": [],
        },
    ]
    path = write_json_batch("duplicate-sku.json", batch)
    proc, _ = run_sync(["--input", path])
    payload = _assert_error(proc, 4, "duplicate_sku")
    assert payload.get("sku") == "P-301", (
        f"Expected \"sku\": \"P-301\" in the error document, got {payload.get('sku')!r}"
    )
    for sku in ("P-301", "P-302", "P-303"):
        assert product(sku) is None, (
            f"{sku} must not be written when the batch contains a duplicate sku."
        )


def test_database_rejection_is_atomic(clean_db):
    run_ok("base.json", BASE_BATCH)
    before = counts()

    batch = [
        {
            "sku": "P-001",
            "name": "Hammer",
            "price_cents": 1999,
            "stock": 11,
            "category": "tools",
            "tags": ["metal", "sale"],
        },
        {
            "sku": "P-401",
            "name": "Anvil",
            "price_cents": 8999,
            "stock": 3,
            "category": "tools",
            "tags": ["metal"],
        },
        {
            "sku": "P-402",
            "name": "Overstocked",
            "price_cents": 100,
            "stock": 100001,
            "category": "tools",
            "tags": ["metal"],
        },
    ]
    path = write_json_batch("db-reject.json", batch)
    proc, _ = run_sync(["--input", path])
    _assert_error(proc, 5, "db_error")

    assert product("P-401") is None, (
        "P-401 must not be persisted when the batch is rejected by the database."
    )
    assert product("P-402") is None, (
        "P-402 must not be persisted when the batch is rejected by the database."
    )
    after = counts()
    assert after["products"] == before["products"] == 8, (
        f"Product count changed after a rejected batch: before={before} after={after}"
    )
    p001 = product("P-001")
    assert p001 is not None, "Product P-001 disappeared."
    assert p001["stock"] == 10, (
        "The legal change to P-001 must be rolled back together with the batch; "
        f"stock is {p001['stock']!r} instead of 10."
    )


# ---------------------------------------------------------------------------
# 8. Scale and idempotency at size
# ---------------------------------------------------------------------------


def test_large_batch_performance_and_idempotency(clean_db):
    run_ok("base.json", BASE_BATCH)
    before = counts()

    perf = _perf_batch()
    result, elapsed = run_ok("perf.json", perf)
    assert result["total"] == 200, f"Expected total 200, got {result.get('total')!r}"
    assert result["inserted"] == 200, (
        f"Expected inserted 200, got {result.get('inserted')!r}"
    )
    assert elapsed < 60, (
        f"Processing a 200-record batch took {elapsed:.1f}s, which exceeds the 60s budget."
    )
    skus = [r["sku"] for r in result["results"]]
    assert skus == sorted(skus), "results must be sorted ascending by sku."

    replay, _ = run_ok("perf.json", perf)
    assert replay["unchanged"] == 200, (
        f"Replaying the 200-record batch must report 200 unchanged, got "
        f"{replay.get('unchanged')!r}"
    )
    assert replay["inserted"] == 0, (
        f"Replaying the 200-record batch must insert nothing, got {replay.get('inserted')!r}"
    )

    after = counts()
    assert after["products"] == before["products"] + 200, (
        f"Expected {before['products'] + 200} Product rows, got {after['products']}"
    )
    assert after["categories"] == before["categories"] + 5, (
        f"Expected exactly 5 new Category rows, got "
        f"{after['categories'] - before['categories']}"
    )
    grp_tags = gel_scalar("select count((select Tag filter .label like 'grp-%'))")
    assert grp_tags == 7, f"Expected exactly 7 'grp-' Tag rows, got {grp_tags}"
