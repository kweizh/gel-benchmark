"""Final-state verification for the gel_polymorphic_catalog_constraints_cli task.

Everything is verified against the *live* local Gel instance through the `gel`
CLI (highest fidelity available here) plus the executor's own report command.
"""

import glob
import json
import os
import random
import string
import subprocess
from decimal import Decimal, ROUND_HALF_UP

import pytest

PROJECT_DIR = "/home/user/catalog"
SCHEMA_DIR = os.path.join(PROJECT_DIR, "dbschema")
MIGRATIONS_DIR = os.path.join(SCHEMA_DIR, "migrations")
REPORT_CMD = os.path.join(PROJECT_DIR, "bin", "catalog-report")
SNAPSHOT_PATH = "/opt/task/initial_state.json"

TOTAL_PRODUCTS = 14
EXPECTED_RESTOCK_ALERTS = ["APP-0001", "APP-0004", "APP-0006", "BOK-0003"]
PRODUCT_KEYS = {
    "sku",
    "brand",
    "name",
    "title",
    "kind",
    "listing_status",
    "price_cents",
    "discount_cents",
    "final_price_cents",
    "units_in_stock",
    "accessories",
    "detail",
}
TOTALS_KEYS = {
    "product_count",
    "book_count",
    "apparel_count",
    "digital_count",
    "active_inventory_value_cents",
    "average_active_final_price",
}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _run(args, cwd=PROJECT_DIR, timeout=180):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)


@pytest.fixture(scope="session", autouse=True)
def gel_server():
    """Bring up the local Gel server; every DB-touching test depends on this."""
    proc = _run(["gel-start"], cwd="/")
    assert proc.returncode == 0, (
        "gel-start failed to bring up the local Gel server.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    return True


def _parse_json_output(raw):
    raw = raw.strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = [json.loads(line) for line in raw.splitlines() if line.strip()]
    return data if isinstance(data, list) else [data]


def query(edgeql):
    """Run an EdgeQL query through the CLI and return the result set as a list."""
    proc = _run(["gel", "query", "-F", "json", edgeql])
    assert proc.returncode == 0, (
        f"gel query failed.\nquery: {edgeql}\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    return _parse_json_output(proc.stdout)


def query_single(edgeql):
    rows = query(edgeql)
    assert len(rows) == 1, f"Expected exactly one result for {edgeql!r}, got {rows!r}"
    return rows[0]


def expect_query_error(edgeql, expected_error):
    proc = _run(["gel", "query", edgeql])
    blob = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode != 0, (
        f"The following write was accepted but must have been rejected with {expected_error}:\n"
        f"{edgeql}\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert expected_error in blob, (
        f"Expected {expected_error} when running:\n{edgeql}\nGot instead: {blob}"
    )


def esc(value):
    """Escape a Python string as an EdgeQL single-quoted literal."""
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def snapshot():
    assert os.path.isfile(SNAPSHOT_PATH), (
        f"The reference snapshot {SNAPSHOT_PATH} is missing; it is baked into the image."
    )
    with open(SNAPSHOT_PATH) as handle:
        return json.load(handle)


def normalize(expr):
    return "".join((expr or "").split())


def constraint_blob(constraint):
    """All textual bits of a constraint, whatever key the CLI used for @value."""
    parts = [
        str(constraint.get(key) or "") for key in ("expr", "subjectexpr", "finalexpr")
    ]
    for param in constraint.get("params") or []:
        parts.extend(str(value) for value in param.values() if value is not None)
    return " ".join(parts)


PRODUCT_QUERY = """
select Product {
    id,
    sku,
    brand,
    name,
    title,
    price_cents,
    discount_cents,
    final_price_cents,
    units_in_stock,
    listing_status,
    type_name := .__type__.name,
    [is Book].author,
    [is Book].pages,
    [is Apparel].size_label,
    [is DigitalDownload].file_size_kb,
    accessories: { sku, @rank } order by @rank
} order by .sku
"""


def _rank_of(entry):
    if "rank" in entry:
        return entry["rank"]
    return entry["@rank"]


def live_products():
    rows = query(PRODUCT_QUERY)
    for row in rows:
        row["kind"] = row["type_name"].split("::")[-1]
        row["accessory_pairs"] = [(a["sku"], _rank_of(a)) for a in row.get("accessories") or []]
    return rows


def live_products_by_sku():
    return {row["sku"]: row for row in live_products()}


def run_report():
    assert os.path.isfile(REPORT_CMD), f"Report command {REPORT_CMD} does not exist."
    assert os.access(REPORT_CMD, os.X_OK), f"Report command {REPORT_CMD} is not executable."
    proc = _run([REPORT_CMD])
    assert proc.returncode == 0, (
        f"{REPORT_CMD} exited with {proc.returncode}.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"{REPORT_CMD} did not print a single parseable JSON document: {exc}\n"
            f"stdout was: {proc.stdout!r}"
        )
    assert isinstance(payload, dict), (
        f"{REPORT_CMD} must print a JSON object, got {type(payload).__name__}."
    )
    return proc.stdout, payload


def format_average(values):
    if not values:
        return "0.00"
    mean = Decimal(sum(values)) / Decimal(len(values))
    return str(mean.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def random_sku(existing):
    while True:
        candidate = "".join(random.choice(string.ascii_uppercase) for _ in range(3))
        candidate += "".join(random.choice(string.digits) for _ in range(4))
        if candidate not in existing:
            return candidate


# --------------------------------------------------------------------------- #
# migration history
# --------------------------------------------------------------------------- #
def test_migration_status_is_in_sync(gel_server):
    proc = _run(["gel", "migration", "status", "--quiet"])
    assert proc.returncode == 0, (
        "`gel migration status` reports that the branch is not in sync with "
        f"{SCHEMA_DIR}.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


def test_migration_history_extended_not_rewritten(gel_server):
    recorded = [
        row["name"]
        for row in query("select schema::Migration { name } filter not .builtin")
    ]
    initial = snapshot()["migrations"]
    for name in initial:
        assert name in recorded, (
            f"Pre-existing migration {name} is no longer part of the recorded history "
            f"(found {recorded}); the original migrations must not be squashed or rewritten."
        )
    assert len(recorded) >= 3, (
        f"Expected at least 3 recorded migrations (2 original + at least 1 new), found {len(recorded)}."
    )
    files = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql")))
    assert len(files) == len(recorded), (
        f"{len(files)} migration files on disk ({[os.path.basename(f) for f in files]}) "
        f"but {len(recorded)} migrations recorded in the database."
    )


# --------------------------------------------------------------------------- #
# schema: scalars and hierarchy
# --------------------------------------------------------------------------- #
def test_custom_scalar_types(gel_server):
    rows = query(
        "select schema::ScalarType { name, enum_values, bases: { name }, "
        "constraints: { name, expr, subjectexpr, finalexpr, params: { name, @value } } } "
        "filter .name in {'default::Sku', 'default::ListingStatus'}"
    )
    by_name = {row["name"]: row for row in rows}

    assert "default::Sku" in by_name, "Custom scalar type default::Sku is missing."
    sku = by_name["default::Sku"]
    bases = [b["name"] for b in sku.get("bases") or []]
    assert "std::str" in bases, f"default::Sku must extend std::str, bases were {bases}."
    blob = " ".join(constraint_blob(c) for c in sku.get("constraints") or [])
    assert "^[A-Z]{3}-[0-9]{4}$" in blob, (
        "default::Sku must be constrained to the regular expression ^[A-Z]{3}-[0-9]{4}$; "
        f"its constraints were: {sku.get('constraints')}"
    )

    assert "default::ListingStatus" in by_name, (
        "Custom scalar type default::ListingStatus is missing."
    )
    assert by_name["default::ListingStatus"]["enum_values"] == ["draft", "active", "archived"], (
        "default::ListingStatus must be an enum with values draft, active, archived in that order; "
        f"found {by_name['default::ListingStatus']['enum_values']}"
    )


def test_type_hierarchy(gel_server):
    rows = query(
        "select schema::ObjectType { name, abstract, ancestors: { name } } "
        "filter .name in {'default::Product', 'default::Book', 'default::Apparel', "
        "'default::DigitalDownload'}"
    )
    by_name = {row["name"]: row for row in rows}
    assert "default::Product" in by_name, "default::Product is missing."
    assert by_name["default::Product"]["abstract"] is True, (
        "default::Product must be an abstract type."
    )
    for name in ("default::Book", "default::Apparel", "default::DigitalDownload"):
        assert name in by_name, f"Concrete object type {name} is missing."
        assert not by_name[name]["abstract"], f"{name} must be a concrete (non-abstract) type."
        ancestors = [a["name"] for a in by_name[name].get("ancestors") or []]
        assert "default::Product" in ancestors, (
            f"{name} must extend default::Product; ancestors were {ancestors}."
        )


# --------------------------------------------------------------------------- #
# schema: pointers, constraints, indexes
# --------------------------------------------------------------------------- #
PRODUCT_INTROSPECTION = """
select schema::ObjectType {
    name,
    pointers: {
        name,
        required,
        cardinality,
        expr,
        default,
        target: { name },
        constraints: { name, expr, subjectexpr, finalexpr, params: { name, @value } },
        [is schema::Link].pointers: {
            name,
            required,
            target: { name },
        },
    },
    constraints: { name, expr, subjectexpr, finalexpr },
    indexes: { expr },
}
filter .name = 'default::Product'
"""


@pytest.fixture(scope="session")
def product_type(gel_server):
    rows = query(PRODUCT_INTROSPECTION)
    assert rows, "Could not introspect default::Product."
    return rows[0]


def pointer_map(type_row):
    return {p["name"]: p for p in type_row.get("pointers") or []}


def test_product_scalar_properties(product_type):
    pointers = pointer_map(product_type)

    expected_required = {
        "sku": "default::Sku",
        "brand": "std::str",
        "name": "std::str",
        "price_cents": "std::int64",
        "discount_cents": "std::int64",
        "units_in_stock": "std::int64",
        "listing_status": "default::ListingStatus",
    }
    for name, target in expected_required.items():
        assert name in pointers, (
            f"default::Product is missing the property {name!r}; found {sorted(pointers)}."
        )
        pointer = pointers[name]
        assert pointer["required"] is True, f"Property {name!r} of Product must be required."
        assert pointer["target"]["name"] == target, (
            f"Property {name!r} must target {target}, found {pointer['target']['name']}."
        )
        assert not pointer.get("expr"), (
            f"Property {name!r} must be a stored property, but it is computed "
            f"({pointer.get('expr')!r})."
        )

    assert not pointers["listing_status"].get("default"), (
        "listing_status must not declare a default value, found "
        f"{pointers['listing_status'].get('default')!r}."
    )
    assert pointers["discount_cents"].get("default"), (
        "discount_cents must declare a default value for newly inserted products."
    )


def test_product_computed_properties(product_type):
    pointers = pointer_map(product_type)
    for name in ("title", "final_price_cents"):
        assert name in pointers, f"default::Product is missing {name!r}."
        assert pointers[name].get("expr"), (
            f"{name!r} must be a computed property (non-empty expression)."
        )
        assert pointers[name]["cardinality"] == "One", (
            f"{name!r} must be a single (cardinality One) computed property, found "
            f"{pointers[name]['cardinality']}."
        )
    assert pointers["final_price_cents"]["target"]["name"] == "std::int64", (
        "final_price_cents must be an int64."
    )
    assert pointers["title"]["target"]["name"] == "std::str", "title must be a str."


def test_old_stock_property_is_gone(product_type):
    pointers = pointer_map(product_type)
    assert "stock" not in pointers, (
        "The old `stock` property must no longer exist on default::Product; it had to be "
        "carried over into units_in_stock."
    )


def test_subtype_properties(gel_server):
    rows = query(
        "select schema::ObjectType { name, pointers: { name, required, target: { name } } } "
        "filter .name in {'default::Book', 'default::Apparel', 'default::DigitalDownload'}"
    )
    by_name = {row["name"]: {p["name"]: p for p in row["pointers"]} for row in rows}

    book = by_name.get("default::Book", {})
    assert "author" in book and book["author"]["required"] is True, (
        "Book.author must exist and be required."
    )
    assert book["author"]["target"]["name"] == "std::str", "Book.author must be a str."
    assert "pages" in book, "Book.pages must exist."
    assert book["pages"]["required"] is False, "Book.pages must be optional."
    assert book["pages"]["target"]["name"] == "std::int64", "Book.pages must be an int64."

    apparel = by_name.get("default::Apparel", {})
    assert "size_label" in apparel and apparel["size_label"]["required"] is True, (
        "Apparel.size_label must exist and be required."
    )
    assert apparel["size_label"]["target"]["name"] == "std::str", (
        "Apparel.size_label must be a str."
    )

    digital = by_name.get("default::DigitalDownload", {})
    assert "file_size_kb" in digital and digital["file_size_kb"]["required"] is True, (
        "DigitalDownload.file_size_kb must exist and be required."
    )
    assert digital["file_size_kb"]["target"]["name"] == "std::int64", (
        "DigitalDownload.file_size_kb must be an int64."
    )


def test_product_structural_constraints(product_type):
    type_constraints = product_type.get("constraints") or []
    exclusive = [
        c
        for c in type_constraints
        if c["name"] == "std::exclusive"
        and "brand" in normalize(c.get("subjectexpr"))
        and "name" in normalize(c.get("subjectexpr"))
    ]
    assert exclusive, (
        "default::Product must carry a type-level exclusive constraint over the (brand, name) "
        f"pair; its type-level constraints were {type_constraints}."
    )

    expression = [
        c
        for c in type_constraints
        if c["name"] == "std::expression"
        and "discount_cents" in constraint_blob(c)
        and "price_cents" in constraint_blob(c)
    ]
    assert expression, (
        "default::Product must carry a type-level expression constraint relating discount_cents "
        f"and price_cents; its type-level constraints were {type_constraints}."
    )

    pointers = pointer_map(product_type)
    sku_constraints = [c["name"] for c in pointers["sku"].get("constraints") or []]
    assert "std::exclusive" in sku_constraints, (
        f"sku must be exclusive across the catalog; found constraints {sku_constraints}."
    )


def test_product_indexes(product_type):
    exprs = [normalize(index.get("expr")) for index in product_type.get("indexes") or []]
    assert any(expr in (".brand", "(.brand)") for expr in exprs), (
        f"default::Product must declare an index on the brand property; found indexes {exprs}."
    )
    composite = [
        expr
        for expr in exprs
        if expr.startswith("(")
        and ".listing_status" in expr
        and ".price_cents" in expr
        and expr.index(".listing_status") < expr.index(".price_cents")
    ]
    assert composite, (
        "default::Product must declare a composite index on (listing_status, price_cents) in that "
        f"order; found indexes {exprs}."
    )
    assert not any("title" in expr for expr in exprs), (
        f"No index over the old title expression may survive; found indexes {exprs}."
    )


def test_accessories_link_and_link_property(product_type):
    pointers = pointer_map(product_type)
    assert "accessories" in pointers, "default::Product must declare an `accessories` link."
    accessories = pointers["accessories"]
    assert accessories["cardinality"] == "Many", (
        f"accessories must be a multi link, found cardinality {accessories['cardinality']}."
    )
    assert accessories["target"]["name"] == "default::Product", (
        f"accessories must target default::Product, found {accessories['target']['name']}."
    )
    link_pointers = {p["name"]: p for p in accessories.get("pointers") or []}
    assert "rank" in link_pointers, (
        "The accessories link must carry a link property named `rank`; found "
        f"{sorted(link_pointers)}."
    )
    assert link_pointers["rank"]["required"] is True, "The `rank` link property must be required."
    assert link_pointers["rank"]["target"]["name"] == "std::int16", (
        f"The `rank` link property must be an int16, found {link_pointers['rank']['target']['name']}."
    )
    rank_guard = [
        c
        for c in accessories.get("constraints") or []
        if c["name"] == "std::exclusive" and "rank" in constraint_blob(c)
    ]
    assert rank_guard, (
        "The accessories link must carry an exclusive constraint involving the rank link property "
        f"so a product cannot have two accessories at the same rank; found "
        f"{accessories.get('constraints')}."
    )


def test_accessory_of_computed_backlink(product_type):
    pointers = pointer_map(product_type)
    assert "accessory_of" in pointers, (
        "default::Product must declare a computed `accessory_of` backlink."
    )
    accessory_of = pointers["accessory_of"]
    assert accessory_of.get("expr"), "accessory_of must be a computed link."
    assert accessory_of["cardinality"] == "Many", (
        f"accessory_of must be a multi link, found cardinality {accessory_of['cardinality']}."
    )


# --------------------------------------------------------------------------- #
# data preservation
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def live_by_sku(gel_server):
    return live_products_by_sku()


def test_total_product_count(live_by_sku):
    assert len(live_by_sku) == TOTAL_PRODUCTS, (
        f"Expected exactly {TOTAL_PRODUCTS} products (12 seeded + 2 digital downloads), "
        f"found {len(live_by_sku)}: {sorted(live_by_sku)}"
    )


def test_seeded_rows_preserved(live_by_sku):
    by_id = {row["id"].lower(): row for row in live_by_sku.values()}
    for expected in snapshot()["products"]:
        key = expected["id"].lower()
        assert key in by_id, (
            f"Seeded product {expected['sku']} (id {expected['id']}) no longer exists; existing "
            "rows had to be migrated in place, not recreated."
        )
        row = by_id[key]
        assert row["sku"] == expected["sku"], (
            f"Product {expected['id']} changed sku from {expected['sku']} to {row['sku']}."
        )
        assert row["kind"] == expected["kind"], (
            f"Product {expected['sku']} changed concrete type from {expected['kind']} to {row['kind']}."
        )
        assert row["price_cents"] == expected["price_cents"], (
            f"Product {expected['sku']} changed price_cents from {expected['price_cents']} to "
            f"{row['price_cents']}."
        )
        assert row["units_in_stock"] == expected["stock"], (
            f"Product {expected['sku']} must have units_in_stock {expected['stock']} (its former "
            f"stock), found {row['units_in_stock']}."
        )
        for field in ("author", "pages", "size_label"):
            if field in expected:
                assert row.get(field) == expected[field], (
                    f"Product {expected['sku']} changed {field} from {expected[field]!r} to "
                    f"{row.get(field)!r}."
                )


def test_title_split_preserved(live_by_sku):
    for expected in snapshot()["products"]:
        row = live_by_sku[expected["sku"]]
        assert row["title"] == expected["title"], (
            f"The computed title of {expected['sku']} must still be {expected['title']!r}, found "
            f"{row['title']!r}."
        )
        brand, _, name = expected["title"].partition(" | ")
        assert row["brand"] == brand, (
            f"{expected['sku']}.brand must be {brand!r} (the part before ' | '), found "
            f"{row['brand']!r}."
        )
        assert row["name"] == name, (
            f"{expected['sku']}.name must be {name!r} (the part after ' | '), found {row['name']!r}."
        )


def test_listing_status_backfill(live_by_sku):
    for expected in snapshot()["products"]:
        row = live_by_sku[expected["sku"]]
        wanted = "active" if expected["stock"] > 0 else "archived"
        assert row["listing_status"] == wanted, (
            f"{expected['sku']} had stock {expected['stock']} so its listing_status must be "
            f"{wanted}, found {row['listing_status']!r}."
        )
        assert row["listing_status"] != "draft", (
            f"No seeded product may end up as draft, but {expected['sku']} did."
        )
        live_wanted = "active" if row["units_in_stock"] > 0 else "archived"
        assert row["listing_status"] == live_wanted, (
            f"{expected['sku']} has units_in_stock {row['units_in_stock']} which is inconsistent "
            f"with listing_status {row['listing_status']!r}."
        )


def test_discounts_backfilled_to_zero(live_by_sku):
    for expected in snapshot()["products"]:
        row = live_by_sku[expected["sku"]]
        assert row["discount_cents"] == 0, (
            f"{expected['sku']} must have discount_cents 0, found {row['discount_cents']}."
        )
        assert row["final_price_cents"] == row["price_cents"], (
            f"{expected['sku']} has no discount so final_price_cents must equal price_cents "
            f"({row['price_cents']}), found {row['final_price_cents']}."
        )


def test_new_digital_downloads(live_by_sku):
    digital = {sku: row for sku, row in live_by_sku.items() if row["kind"] == "DigitalDownload"}
    assert sorted(digital) == ["DLD-0001", "DLD-0002"], (
        f"Exactly two DigitalDownload rows with skus DLD-0001 and DLD-0002 are required, found "
        f"{sorted(digital)}."
    )
    expected_rows = {
        "DLD-0001": {
            "brand": "Northwind",
            "name": "Trail Guide PDF",
            "title": "Northwind | Trail Guide PDF",
            "price_cents": 1200,
            "discount_cents": 200,
            "final_price_cents": 1000,
            "units_in_stock": 999,
            "listing_status": "active",
            "file_size_kb": 8200,
        },
        "DLD-0002": {
            "brand": "Cobalt",
            "name": "Layering Course",
            "title": "Cobalt | Layering Course",
            "price_cents": 4900,
            "discount_cents": 0,
            "final_price_cents": 4900,
            "units_in_stock": 500,
            "listing_status": "draft",
            "file_size_kb": 152000,
        },
    }
    for sku, expected in expected_rows.items():
        row = digital[sku]
        for field, value in expected.items():
            assert row.get(field) == value, (
                f"{sku}.{field} must be {value!r}, found {row.get(field)!r}."
            )


def test_accessory_wiring(live_by_sku):
    assert live_by_sku["BOK-0001"]["accessory_pairs"] == [("DLD-0001", 1), ("APP-0005", 2)], (
        "BOK-0001 must link DLD-0001 at rank 1 and APP-0005 at rank 2, found "
        f"{live_by_sku['BOK-0001']['accessory_pairs']}."
    )
    assert live_by_sku["APP-0001"]["accessory_pairs"] == [("APP-0005", 1)], (
        "APP-0001 must link APP-0005 at rank 1, found "
        f"{live_by_sku['APP-0001']['accessory_pairs']}."
    )
    total_links = sum(len(row["accessory_pairs"]) for row in live_by_sku.values())
    assert total_links == 3, (
        f"The catalog must contain exactly 3 accessory links in total, found {total_links}."
    )
    backlinks = query(
        "select Product { sources := (select .accessory_of { sku }) } "
        "filter .sku = 'APP-0005'"
    )
    assert backlinks, "APP-0005 not found while checking the accessory_of backlink."
    sources = sorted(item["sku"] for item in backlinks[0]["sources"])
    assert sources == ["APP-0001", "BOK-0001"], (
        "APP-0005 must report APP-0001 and BOK-0001 through the computed accessory_of backlink, "
        f"found {sources}."
    )


# --------------------------------------------------------------------------- #
# report command
# --------------------------------------------------------------------------- #
def test_report_runs_and_is_deterministic(gel_server):
    before = query_single("select count(Product)")
    first_raw, first = run_report()
    second_raw, second = run_report()
    assert first_raw == second_raw, (
        "Two consecutive runs of the report command must produce byte-identical stdout."
    )
    assert set(first) == {"products", "restock_alerts", "totals"}, (
        f"The report must have exactly the keys products, restock_alerts, totals; found "
        f"{sorted(first)}."
    )
    assert isinstance(first["products"], list), "`products` must be a JSON array."
    assert isinstance(first["restock_alerts"], list), "`restock_alerts` must be a JSON array."
    assert isinstance(first["totals"], dict), "`totals` must be a JSON object."
    assert second == first, "The report content changed between two consecutive runs."
    after = query_single("select count(Product)")
    assert after == before, (
        f"Running the report must not modify the database (product count went from {before} to {after})."
    )


def test_report_products_match_live_database(gel_server):
    _, report = run_report()
    live = live_products()
    expected_skus = [row["sku"] for row in live]
    reported_skus = [item["sku"] for item in report["products"]]
    assert reported_skus == sorted(expected_skus), (
        "`products` must list every product sorted by sku ascending; expected "
        f"{sorted(expected_skus)}, found {reported_skus}."
    )

    live_by_sku_map = {row["sku"]: row for row in live}
    for item in report["products"]:
        sku = item["sku"]
        row = live_by_sku_map[sku]
        assert set(item) == PRODUCT_KEYS, (
            f"Product entry {sku} must have exactly the keys {sorted(PRODUCT_KEYS)}, found "
            f"{sorted(item)}."
        )
        for field in (
            "brand",
            "name",
            "title",
            "listing_status",
            "price_cents",
            "discount_cents",
            "final_price_cents",
            "units_in_stock",
        ):
            assert item[field] == row[field], (
                f"{sku}.{field} in the report is {item[field]!r} but the database says {row[field]!r}."
            )
        for field in (
            "price_cents",
            "discount_cents",
            "final_price_cents",
            "units_in_stock",
        ):
            assert isinstance(item[field], int) and not isinstance(item[field], bool), (
                f"{sku}.{field} must be a JSON number, found {item[field]!r}."
            )
        assert item["kind"] == row["kind"], (
            f"{sku}.kind must be {row['kind']!r} (concrete type without module prefix), found "
            f"{item['kind']!r}."
        )
        assert item["accessories"] == [
            {"sku": pair[0], "rank": pair[1]} for pair in row["accessory_pairs"]
        ], (
            f"{sku}.accessories must be {row['accessory_pairs']!r} ordered by rank, found "
            f"{item['accessories']!r}."
        )
        detail = item["detail"]
        assert isinstance(detail, dict), f"{sku}.detail must be a JSON object."
        if row["kind"] == "Book":
            assert set(detail) == {"author", "pages"}, (
                f"{sku}.detail must have exactly the keys author and pages, found {sorted(detail)}."
            )
            assert detail["author"] == row["author"], (
                f"{sku}.detail.author must be {row['author']!r}, found {detail['author']!r}."
            )
            assert detail["pages"] == row["pages"], (
                f"{sku}.detail.pages must be {row['pages']!r} (JSON null when unset), found "
                f"{detail['pages']!r}."
            )
        elif row["kind"] == "Apparel":
            assert set(detail) == {"size_label"}, (
                f"{sku}.detail must have exactly the key size_label, found {sorted(detail)}."
            )
            assert detail["size_label"] == row["size_label"], (
                f"{sku}.detail.size_label must be {row['size_label']!r}, found "
                f"{detail['size_label']!r}."
            )
        else:
            assert set(detail) == {"file_size_kb"}, (
                f"{sku}.detail must have exactly the key file_size_kb, found {sorted(detail)}."
            )
            assert detail["file_size_kb"] == row["file_size_kb"], (
                f"{sku}.detail.file_size_kb must be {row['file_size_kb']!r}, found "
                f"{detail['file_size_kb']!r}."
            )


def test_report_totals_match_live_database(gel_server):
    _, report = run_report()
    live = live_products()
    totals = report["totals"]
    assert set(totals) == TOTALS_KEYS, (
        f"`totals` must have exactly the keys {sorted(TOTALS_KEYS)}, found {sorted(totals)}."
    )
    expected_counts = {
        "product_count": len(live),
        "book_count": sum(1 for row in live if row["kind"] == "Book"),
        "apparel_count": sum(1 for row in live if row["kind"] == "Apparel"),
        "digital_count": sum(1 for row in live if row["kind"] == "DigitalDownload"),
    }
    for key, value in expected_counts.items():
        assert totals[key] == value, f"totals.{key} must be {value}, found {totals[key]!r}."
        assert isinstance(totals[key], int) and not isinstance(totals[key], bool), (
            f"totals.{key} must be a JSON number, found {totals[key]!r}."
        )

    active = [row for row in live if row["listing_status"] == "active"]
    expected_value = sum(row["final_price_cents"] * row["units_in_stock"] for row in active)
    assert totals["active_inventory_value_cents"] == expected_value, (
        "totals.active_inventory_value_cents must be the sum of final_price_cents * units_in_stock "
        f"over active products ({expected_value}), found {totals['active_inventory_value_cents']!r}."
    )
    expected_average = format_average([row["final_price_cents"] for row in active])
    assert totals["average_active_final_price"] == expected_average, (
        "totals.average_active_final_price must be the half-up two-decimal mean of the active "
        f"final_price_cents values ({expected_average!r}), found "
        f"{totals['average_active_final_price']!r}."
    )
    assert isinstance(totals["average_active_final_price"], str), (
        "totals.average_active_final_price must be a JSON string."
    )


def test_report_restock_alerts_match_live_database(gel_server):
    _, report = run_report()
    rows = query(
        "select Product { sku } filter not (Product is DigitalDownload) "
        "and .listing_status != ListingStatus.archived and .units_in_stock < 5"
    )
    expected = sorted(row["sku"] for row in rows)
    assert report["restock_alerts"] == expected, (
        f"restock_alerts must be {expected}, found {report['restock_alerts']}."
    )
    assert expected == EXPECTED_RESTOCK_ALERTS, (
        f"With the required catalog data the restock alerts must be {EXPECTED_RESTOCK_ALERTS}, but "
        f"the database yields {expected}."
    )


def test_report_reflects_live_data_changes(gel_server):
    _, before = run_report()
    existing = set(live_products_by_sku())
    new_sku = random_sku(existing)
    token = "".join(random.choice(string.ascii_lowercase) for _ in range(8))
    brand = f"Probe {token}"
    name = f"Fixture {token}"
    insert = (
        "insert Book { "
        f"sku := <Sku>{esc(new_sku)}, brand := {esc(brand)}, name := {esc(name)}, "
        "price_cents := 5000, units_in_stock := 2, "
        "listing_status := ListingStatus.active, author := 'Probe Author' }"
    )
    proc = _run(["gel", "query", insert])
    assert proc.returncode == 0, (
        f"A valid Book insert was rejected.\n{insert}\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    try:
        stored = query_single(
            "select Book { discount_cents, final_price_cents, title, pages } "
            f"filter .sku = <Sku>{esc(new_sku)}"
        )
        assert stored["discount_cents"] == 0, (
            "discount_cents must default to 0 when omitted, found "
            f"{stored['discount_cents']!r}."
        )
        assert stored["final_price_cents"] == 5000, (
            f"final_price_cents must be 5000, found {stored['final_price_cents']!r}."
        )
        assert stored["title"] == f"{brand} | {name}", (
            f"The computed title must be {brand + ' | ' + name!r}, found {stored['title']!r}."
        )
        assert stored["pages"] is None, "pages must be unset for the probe Book."

        _, after = run_report()
        reported = {item["sku"]: item for item in after["products"]}
        assert new_sku in reported, (
            f"The report must include the newly inserted product {new_sku}; it looks like the "
            "output is not derived from the live database."
        )
        assert reported[new_sku]["kind"] == "Book", (
            f"{new_sku} must be reported with kind Book, found {reported[new_sku]['kind']!r}."
        )
        assert reported[new_sku]["detail"]["pages"] is None, (
            f"{new_sku}.detail.pages must be JSON null, found "
            f"{reported[new_sku]['detail']['pages']!r}."
        )
        assert after["totals"]["product_count"] == before["totals"]["product_count"] + 1, (
            "totals.product_count must grow by one after inserting a product."
        )
        assert after["totals"]["book_count"] == before["totals"]["book_count"] + 1, (
            "totals.book_count must grow by one after inserting a Book."
        )
        assert new_sku in after["restock_alerts"], (
            f"{new_sku} has 2 units in stock and is active, so it must appear in restock_alerts: "
            f"{after['restock_alerts']}"
        )
    finally:
        cleanup = _run(["gel", "query", f"delete Product filter .sku = <Sku>{esc(new_sku)}"])
        assert cleanup.returncode == 0, (
            f"Failed to clean up the probe product {new_sku}: {cleanup.stderr}"
        )

    _, restored = run_report()
    assert all(item["sku"] != new_sku for item in restored["products"]), (
        f"The report still lists {new_sku} after it was deleted."
    )
    assert restored["totals"]["product_count"] == TOTAL_PRODUCTS, (
        f"totals.product_count must be back to {TOTAL_PRODUCTS} after cleanup, found "
        f"{restored['totals']['product_count']}."
    )


# --------------------------------------------------------------------------- #
# negative checks
# --------------------------------------------------------------------------- #
def _probe_book(sku, brand=None, name=None, extra=""):
    token = "".join(random.choice(string.ascii_lowercase) for _ in range(8))
    brand = brand if brand is not None else f"Neg {token}"
    name = name if name is not None else f"Case {token}"
    fields = [
        f"sku := <Sku>{esc(sku)}",
        f"brand := {esc(brand)}",
        f"name := {esc(name)}",
        "price_cents := 1000",
        "units_in_stock := 1",
        "listing_status := ListingStatus.active",
        "author := 'Neg Author'",
    ]
    if extra:
        fields.append(extra)
    return "insert Book { " + ", ".join(fields) + " }"


def test_reject_duplicate_sku(gel_server):
    expect_query_error(_probe_book("BOK-0001"), "ConstraintViolationError")


def test_reject_malformed_sku(gel_server):
    expect_query_error(_probe_book("bad-sku"), "ConstraintViolationError")


def test_reject_duplicate_brand_and_name(gel_server):
    row = query_single("select Book { brand, name } filter .sku = 'BOK-0003'")
    existing = set(live_products_by_sku())
    insert = (
        "insert Apparel { "
        f"sku := <Sku>{esc(random_sku(existing))}, brand := {esc(row['brand'])}, "
        f"name := {esc(row['name'])}, price_cents := 1000, units_in_stock := 1, "
        "listing_status := ListingStatus.active, size_label := 'M' }"
    )
    expect_query_error(insert, "ConstraintViolationError")


def test_reject_discount_above_price(gel_server):
    existing = set(live_products_by_sku())
    expect_query_error(
        _probe_book(random_sku(existing), extra="discount_cents := 1001"),
        "ConstraintViolationError",
    )


def test_reject_non_positive_price(gel_server):
    existing = set(live_products_by_sku())
    insert = _probe_book(random_sku(existing)).replace("price_cents := 1000", "price_cents := 0")
    expect_query_error(insert, "ConstraintViolationError")


def test_reject_negative_stock(gel_server):
    existing = set(live_products_by_sku())
    insert = _probe_book(random_sku(existing)).replace(
        "units_in_stock := 1", "units_in_stock := -1"
    )
    expect_query_error(insert, "ConstraintViolationError")


def test_reject_missing_listing_status(gel_server):
    existing = set(live_products_by_sku())
    token = "".join(random.choice(string.ascii_lowercase) for _ in range(8))
    insert = (
        "insert Book { "
        f"sku := <Sku>{esc(random_sku(existing))}, brand := {esc('Neg ' + token)}, "
        f"name := {esc('Case ' + token)}, price_cents := 1000, units_in_stock := 1, "
        "author := 'Neg Author' }"
    )
    expect_query_error(insert, "MissingRequiredError")


def test_reject_undersized_digital_download(gel_server):
    existing = set(live_products_by_sku())
    token = "".join(random.choice(string.ascii_lowercase) for _ in range(8))
    insert = (
        "insert DigitalDownload { "
        f"sku := <Sku>{esc(random_sku(existing))}, brand := {esc('Neg ' + token)}, "
        f"name := {esc('Case ' + token)}, price_cents := 1000, units_in_stock := 1, "
        "listing_status := ListingStatus.active, file_size_kb := 0 }"
    )
    expect_query_error(insert, "ConstraintViolationError")


def test_reject_duplicate_accessory_rank(gel_server):
    update = (
        "update Product filter .sku = 'BOK-0001' set { "
        "accessories += (select detached Product { @rank := <int16>1 } filter .sku = 'APP-0006') }"
    )
    expect_query_error(update, "ConstraintViolationError")


def test_no_stray_rows_after_negative_checks(gel_server):
    count = query_single("select count(Product)")
    assert count == TOTAL_PRODUCTS, (
        f"Rejected writes must leave no side effects: expected {TOTAL_PRODUCTS} products, found "
        f"{count}."
    )
    skus = sorted(row["sku"] for row in query("select Product { sku }"))
    expected = sorted([p["sku"] for p in snapshot()["products"]] + ["DLD-0001", "DLD-0002"])
    assert skus == expected, f"Unexpected catalog contents: {skus} (expected {expected})."
