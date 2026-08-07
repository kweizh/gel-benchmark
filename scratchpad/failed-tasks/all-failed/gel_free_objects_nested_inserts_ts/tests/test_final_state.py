"""Final-state verification for the gel_free_objects_nested_inserts_ts task.

Every check drives the real system: the executor's CLI is invoked with real
payload files against the real local Gel server, and the resulting database
state is read back with the `gel` CLI.
"""

import concurrent.futures
import copy
import glob
import json
import os
import shutil
import subprocess

import pytest

PROJECT_DIR = "/home/user/catalog-ingest"
INGEST_ARGS = ["npx", "tsx", "src/ingest.ts"]
START_SERVER = "/usr/local/bin/start-gel.sh"
WORK_DIR = "/tmp/verify"

SUMMARY_KEYS = {"ok", "source", "revision", "counts", "products", "totals"}
COUNTS_KEYS = {
    "products_created",
    "products_updated",
    "vendors_created",
    "vendors_updated",
    "tags_created",
    "variants_created",
    "variants_updated",
    "variants_removed",
}
TOTALS_KEYS = {
    "products_in_db",
    "variants_in_db",
    "tags_in_db",
    "vendors_in_db",
    "stock_total",
}
PRODUCT_KEYS = {
    "sku",
    "title",
    "price_cents",
    "vendor_code",
    "tags",
    "variants",
    "created",
}

PAYLOAD_A = {
    "source": "vendor_a",
    "revision": 1,
    "products": [
        {
            "sku": "SKU0001",
            "title": "Caf\u00e9 Press",
            "price_cents": 1999,
            "vendor": {"code": "acme", "name": "Acme Inc."},
            "tags": ["kitchen", "caf\u00e9", "kitchen"],
            "variants": [
                {"code": "S", "label": "Small", "stock": 3},
                {"code": "M", "label": "Medium", "stock": 5},
            ],
        },
        {
            "sku": "SKU0002",
            "title": "\u65e5\u672c\u8a9e Kettle",
            "price_cents": 4500,
            "vendor": {"code": "acme", "name": "Acme Inc."},
            "tags": [],
            "variants": [],
        },
    ],
}

PAYLOAD_B = {
    "source": "vendor_a",
    "revision": 2,
    "products": [
        {
            "sku": "SKU0001",
            "title": "Caf\u00e9 Press II",
            "price_cents": 2099,
            "vendor": {"code": "acme", "name": "Acme Industries"},
            "tags": ["caf\u00e9", "\u00e9lite"],
            "variants": [
                {"code": "S", "label": "Small", "stock": 7},
                {"code": "L", "label": "Large", "stock": 1},
            ],
        },
        {
            "sku": "SKU0003",
            "title": "Whisk",
            "price_cents": 250,
            "vendor": {"code": "globex", "name": "Globex"},
            "tags": ["kitchen"],
            "variants": [],
        },
    ],
}

PAYLOAD_D = {
    "source": "vendor_b",
    "revision": 1,
    "products": [
        {
            "sku": "SKU0002",
            "title": "Kettle Redux",
            "price_cents": 4600,
            "vendor": {"code": "globex", "name": "Globex"},
            "tags": ["kitchen", "tea"],
            "variants": [{"code": "XL", "label": "Extra", "stock": 2}],
        }
    ],
}

PURGE_QUERIES = [
    "delete Variant",
    "delete Product",
    "delete Tag",
    "delete Vendor",
    "delete SyncSource",
]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def run_gel(args, check=True, timeout=180):
    proc = subprocess.run(
        ["gel"] + args,
        capture_output=True,
        text=True,
        cwd=PROJECT_DIR,
        timeout=timeout,
    )
    if check:
        assert proc.returncode == 0, (
            f"`gel {' '.join(args)}` failed with code {proc.returncode}:\n"
            f"{proc.stdout}\n{proc.stderr}"
        )
    return proc


def gel_query(query: str):
    proc = run_gel(["query", "-F", "json", query])
    return json.loads(proc.stdout)


def gel_query_one(query: str):
    rows = gel_query(query)
    assert len(rows) == 1, f"Expected exactly one row from {query!r}, got {rows!r}"
    return rows[0]


def purge_catalog():
    for query in PURGE_QUERIES:
        run_gel(["query", query])


def write_payload(name: str, payload) -> str:
    os.makedirs(WORK_DIR, exist_ok=True)
    path = os.path.join(WORK_DIR, name)
    with open(path, "w", encoding="utf-8") as handle:
        if isinstance(payload, str):
            handle.write(payload)
        else:
            json.dump(payload, handle, ensure_ascii=False)
    return path


def run_ingest(input_path, timeout=300):
    args = list(INGEST_ARGS)
    if input_path is not None:
        args += ["--input", input_path]
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        cwd=PROJECT_DIR,
        timeout=timeout,
        env=os.environ.copy(),
    )


def parse_stdout(proc, context: str):
    assert proc.stdout.strip(), (
        f"{context}: the command printed nothing on stdout. stderr:\n{proc.stderr}"
    )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"{context}: stdout must be exactly one JSON object, got:\n"
            f"{proc.stdout!r}\n({exc})"
        )


def ingest_ok(input_path, context: str):
    proc = run_ingest(input_path)
    body = parse_stdout(proc, context)
    assert proc.returncode == 0, (
        f"{context}: expected exit code 0, got {proc.returncode}. "
        f"stdout={proc.stdout!r} stderr={proc.stderr[-2000:]!r}"
    )
    assert isinstance(body, dict), f"{context}: stdout must be a JSON object."
    assert body.get("ok") is True, f"{context}: expected \"ok\": true, got {body!r}"
    assert set(body.keys()) == SUMMARY_KEYS, (
        f"{context}: top-level keys must be exactly {sorted(SUMMARY_KEYS)}, "
        f"got {sorted(body.keys())}"
    )
    assert set(body["counts"].keys()) == COUNTS_KEYS, (
        f"{context}: `counts` keys must be exactly {sorted(COUNTS_KEYS)}, "
        f"got {sorted(body['counts'].keys())}"
    )
    assert set(body["totals"].keys()) == TOTALS_KEYS, (
        f"{context}: `totals` keys must be exactly {sorted(TOTALS_KEYS)}, "
        f"got {sorted(body['totals'].keys())}"
    )
    for entry in body["products"]:
        assert set(entry.keys()) == PRODUCT_KEYS, (
            f"{context}: every product entry must have exactly "
            f"{sorted(PRODUCT_KEYS)}, got {sorted(entry.keys())}"
        )
    return body


def ingest_error(input_path, expected_code: str, context: str):
    proc = run_ingest(input_path)
    body = parse_stdout(proc, context)
    assert proc.returncode == 1, (
        f"{context}: expected exit code 1, got {proc.returncode}. "
        f"stdout={proc.stdout!r} stderr={proc.stderr[-2000:]!r}"
    )
    assert isinstance(body, dict), f"{context}: stdout must be a JSON object."
    assert body.get("ok") is False, f"{context}: expected \"ok\": false, got {body!r}"
    assert body.get("error_code") == expected_code, (
        f"{context}: expected error_code {expected_code!r}, got {body!r}"
    )
    assert isinstance(body.get("message"), str) and body["message"], (
        f"{context}: `message` must be a non-empty string, got {body!r}"
    )
    return body


def db_snapshot():
    products = gel_query(
        "select Product { sku, title, price_cents, revision, "
        "vendor_code := .vendor.code, tag_labels := array_agg(.tags.label), "
        "variants := (select .<product[is Variant] { code, label, stock }) } "
        "order by .sku"
    )
    for product in products:
        product["tag_labels"] = sorted(product["tag_labels"])
        product["variants"] = sorted(product["variants"], key=lambda v: v["code"])
    return {
        "products": products,
        "vendors": gel_query("select Vendor { code, name } order by .code"),
        "tags": sorted(
            entry["label"] for entry in gel_query("select Tag { label }")
        ),
        "sources": gel_query("select SyncSource { code, revision } order by .code"),
    }


def product_by_sku(snapshot, sku):
    for product in snapshot["products"]:
        if product["sku"] == sku:
            return product
    return None


def summary_product(body, sku):
    for entry in body["products"]:
        if entry["sku"] == sku:
            return entry
    raise AssertionError(f"No entry for {sku} in summary products: {body['products']!r}")


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def gel_server():
    proc = subprocess.run(
        ["bash", START_SERVER], capture_output=True, text=True, timeout=300
    )
    assert proc.returncode == 0, (
        f"Failed to start the local Gel server: {proc.stdout}\n{proc.stderr}"
    )
    return True


@pytest.fixture(scope="session")
def clean_catalog(gel_server):
    if os.path.isdir(WORK_DIR):
        shutil.rmtree(WORK_DIR)
    os.makedirs(WORK_DIR, exist_ok=True)
    purge_catalog()
    return True


@pytest.fixture(scope="session")
def batch_a(clean_catalog):
    path = write_payload("payload_a.json", PAYLOAD_A)
    return ingest_ok(path, "batch A")


@pytest.fixture(scope="session")
def batch_b(batch_a):
    path = write_payload("payload_b.json", PAYLOAD_B)
    return ingest_ok(path, "batch B")


@pytest.fixture(scope="session")
def batch_d(batch_b):
    path = write_payload("payload_d.json", PAYLOAD_D)
    return ingest_ok(path, "batch D")


# --------------------------------------------------------------------------
# 1. schema shape
# --------------------------------------------------------------------------


EXPECTED_POINTERS = {
    "default::Vendor": {"code", "name"},
    "default::Tag": {"label"},
    "default::Product": {
        "sku",
        "title",
        "price_cents",
        "revision",
        "vendor",
        "tags",
    },
    "default::Variant": {"product", "code", "label", "stock"},
    "default::SyncSource": {"code", "revision"},
}


def test_schema_object_types_exist(gel_server):
    rows = gel_query(
        "select schema::ObjectType { name } filter .name in {"
        "'default::Vendor', 'default::Tag', 'default::Product', "
        "'default::Variant', 'default::SyncSource'}"
    )
    found = sorted(row["name"] for row in rows)
    assert found == sorted(EXPECTED_POINTERS.keys()), (
        f"Expected object types {sorted(EXPECTED_POINTERS.keys())}, found {found}"
    )


def test_schema_pointers_are_declared(gel_server):
    rows = gel_query(
        "select schema::ObjectType { name, pointers: { name, "
        "card := <str>.cardinality, req := .required } } "
        "filter .name in {'default::Vendor', 'default::Tag', 'default::Product', "
        "'default::Variant', 'default::SyncSource'}"
    )
    by_type = {row["name"]: row["pointers"] for row in rows}
    for type_name, expected in EXPECTED_POINTERS.items():
        pointers = {p["name"]: p for p in by_type.get(type_name, [])}
        missing = expected - set(pointers)
        assert not missing, f"{type_name} is missing pointer(s): {sorted(missing)}"
        for name in expected:
            if type_name == "default::Product" and name == "tags":
                continue
            assert pointers[name]["req"] is True, (
                f"{type_name}.{name} must be required."
            )
            assert pointers[name]["card"] == "One", (
                f"{type_name}.{name} must be single-valued, got "
                f"{pointers[name]['card']}"
            )
    product_tags = {p["name"]: p for p in by_type["default::Product"]}["tags"]
    assert product_tags["card"] == "Many", (
        f"Product.tags must be a multi link, got cardinality {product_tags['card']}"
    )


# --------------------------------------------------------------------------
# 2. uniqueness rules (behavioural, on an empty catalog)
# --------------------------------------------------------------------------


def test_uniqueness_constraints_are_enforced(gel_server):
    purge_catalog()
    try:
        run_gel(["query", "insert Vendor { code := 'uniq_v', name := 'A' }"])
        dup = run_gel(
            ["query", "insert Vendor { code := 'uniq_v', name := 'B' }"], check=False
        )
        assert dup.returncode != 0, "Vendor.code must be exclusive."

        run_gel(["query", "insert Tag { label := 'uniq_t' }"])
        dup = run_gel(["query", "insert Tag { label := 'uniq_t' }"], check=False)
        assert dup.returncode != 0, "Tag.label must be exclusive."

        run_gel(["query", "insert SyncSource { code := 'uniq_s', revision := 1 }"])
        dup = run_gel(
            ["query", "insert SyncSource { code := 'uniq_s', revision := 2 }"],
            check=False,
        )
        assert dup.returncode != 0, "SyncSource.code must be exclusive."

        insert_product = (
            "insert Product {{ sku := '{sku}', title := 't', price_cents := 1, "
            "revision := 1, vendor := assert_exists((select Vendor "
            "filter .code = 'uniq_v' limit 1)) }}"
        )
        run_gel(["query", insert_product.format(sku="uniq_p1")])
        dup = run_gel(["query", insert_product.format(sku="uniq_p1")], check=False)
        assert dup.returncode != 0, "Product.sku must be exclusive."
        run_gel(["query", insert_product.format(sku="uniq_p2")])

        insert_variant = (
            "insert Variant {{ product := assert_exists((select Product "
            "filter .sku = '{sku}' limit 1)), code := 'S', label := 'l', "
            "stock := 1 }}"
        )
        run_gel(["query", insert_variant.format(sku="uniq_p1")])
        dup = run_gel(["query", insert_variant.format(sku="uniq_p1")], check=False)
        assert dup.returncode != 0, (
            "Variant.code must be exclusive per product (same product, same code)."
        )
        same_code_other_product = run_gel(
            ["query", insert_variant.format(sku="uniq_p2")], check=False
        )
        assert same_code_other_product.returncode == 0, (
            "Two different products must be allowed to share a variant code: "
            f"{same_code_other_product.stdout}\n{same_code_other_product.stderr}"
        )
    finally:
        purge_catalog()


# --------------------------------------------------------------------------
# 3. migrations
# --------------------------------------------------------------------------


def test_migration_files_exist():
    files = glob.glob(os.path.join(PROJECT_DIR, "dbschema", "migrations", "*.edgeql"))
    assert files, (
        "No migration file found in "
        f"{os.path.join(PROJECT_DIR, 'dbschema', 'migrations')}; the schema must be "
        "applied through Gel's migration system."
    )


def test_migration_status_is_up_to_date(gel_server):
    proc = run_gel(["migration", "status"], check=False)
    assert proc.returncode == 0, (
        f"`gel migration status` reported a problem: {proc.stdout}\n{proc.stderr}"
    )
    output = f"{proc.stdout}\n{proc.stderr}".lower()
    assert "up to date" in output, (
        "Database is not up to date with the migrations: "
        f"{proc.stdout}\n{proc.stderr}"
    )


def test_migration_history_recorded_in_db(gel_server):
    proc = run_gel(["migration", "log", "--from-db"])
    history = f"{proc.stdout}\n{proc.stderr}".strip()
    assert history and "<no migrations>" not in history, (
        "The database has no migration history; the schema was not applied "
        f"through a migration: {proc.stdout}\n{proc.stderr}"
    )


# --------------------------------------------------------------------------
# 4. first batch
# --------------------------------------------------------------------------


def test_batch_a_counts(batch_a):
    assert batch_a["source"] == "vendor_a", batch_a
    assert batch_a["revision"] == 1, batch_a
    assert batch_a["counts"] == {
        "products_created": 2,
        "products_updated": 0,
        "vendors_created": 1,
        "vendors_updated": 0,
        "tags_created": 2,
        "variants_created": 2,
        "variants_updated": 0,
        "variants_removed": 0,
    }, f"Unexpected counts for batch A: {batch_a['counts']}"


def test_batch_a_products_payload(batch_a):
    skus = [entry["sku"] for entry in batch_a["products"]]
    assert skus == ["SKU0001", "SKU0002"], (
        f"`products` must be sorted by sku ascending, got {skus}"
    )
    first = summary_product(batch_a, "SKU0001")
    assert first["title"] == "Caf\u00e9 Press", first
    assert first["price_cents"] == 1999, first
    assert first["vendor_code"] == "acme", first
    assert first["created"] is True, first
    assert first["tags"] == ["caf\u00e9", "kitchen"], (
        f"Tags must be deduplicated and sorted by code point, got {first['tags']}"
    )
    assert first["variants"] == [
        {"code": "M", "label": "Medium", "stock": 5},
        {"code": "S", "label": "Small", "stock": 3},
    ], f"Variants must be sorted by code ascending, got {first['variants']}"
    second = summary_product(batch_a, "SKU0002")
    assert second["title"] == "\u65e5\u672c\u8a9e Kettle", second
    assert second["tags"] == [], second
    assert second["variants"] == [], second
    assert second["created"] is True, second


def test_batch_a_totals(batch_a):
    assert batch_a["totals"] == {
        "products_in_db": 2,
        "variants_in_db": 2,
        "tags_in_db": 2,
        "vendors_in_db": 1,
        "stock_total": 8,
    }, f"Unexpected totals for batch A: {batch_a['totals']}"


def test_batch_a_persisted_state(batch_a):
    snapshot = db_snapshot()
    assert [p["sku"] for p in snapshot["products"]] == ["SKU0001", "SKU0002"], snapshot
    assert [v["code"] for v in snapshot["vendors"]] == ["acme"], snapshot
    assert snapshot["tags"] == sorted(["kitchen", "caf\u00e9"]), snapshot
    assert snapshot["sources"] == [{"code": "vendor_a", "revision": 1}], snapshot

    first = product_by_sku(snapshot, "SKU0001")
    assert first is not None
    assert first["title"] == "Caf\u00e9 Press", first
    assert first["price_cents"] == 1999, first
    assert first["revision"] == 1, first
    assert first["vendor_code"] == "acme", first
    assert first["tag_labels"] == sorted(["kitchen", "caf\u00e9"]), first
    assert first["variants"] == [
        {"code": "M", "label": "Medium", "stock": 5},
        {"code": "S", "label": "Small", "stock": 3},
    ], first

    second = product_by_sku(snapshot, "SKU0002")
    assert second is not None
    assert second["title"] == "\u65e5\u672c\u8a9e Kettle", second
    assert second["variants"] == [], second


# --------------------------------------------------------------------------
# 5. replay / stale revision
# --------------------------------------------------------------------------


def test_replay_is_rejected_as_stale(batch_a):
    before = db_snapshot()
    path = write_payload("payload_a_replay.json", PAYLOAD_A)
    ingest_error(path, "STALE_REVISION", "replay of batch A")
    assert db_snapshot() == before, "A stale batch must not modify the database."


def test_revision_zero_is_invalid_payload(batch_a):
    before = db_snapshot()
    payload = copy.deepcopy(PAYLOAD_A)
    payload["revision"] = 0
    path = write_payload("payload_a_rev0.json", payload)
    ingest_error(path, "INVALID_PAYLOAD", "batch A with revision 0")
    assert db_snapshot() == before, "A rejected batch must not modify the database."


# --------------------------------------------------------------------------
# 6. second batch
# --------------------------------------------------------------------------


def test_batch_b_counts(batch_b):
    assert batch_b["counts"] == {
        "products_created": 1,
        "products_updated": 1,
        "vendors_created": 1,
        "vendors_updated": 1,
        "tags_created": 1,
        "variants_created": 1,
        "variants_updated": 1,
        "variants_removed": 1,
    }, f"Unexpected counts for batch B: {batch_b['counts']}"


def test_batch_b_products_payload(batch_b):
    skus = [entry["sku"] for entry in batch_b["products"]]
    assert skus == ["SKU0001", "SKU0003"], skus
    first = summary_product(batch_b, "SKU0001")
    assert first["created"] is False, first
    assert first["title"] == "Caf\u00e9 Press II", first
    assert first["price_cents"] == 2099, first
    assert first["tags"] == ["caf\u00e9", "\u00e9lite"], first
    assert first["variants"] == [
        {"code": "L", "label": "Large", "stock": 1},
        {"code": "S", "label": "Small", "stock": 7},
    ], first
    third = summary_product(batch_b, "SKU0003")
    assert third["created"] is True, third
    assert third["tags"] == ["kitchen"], third
    assert third["variants"] == [], third


def test_batch_b_totals(batch_b):
    assert batch_b["totals"] == {
        "products_in_db": 3,
        "variants_in_db": 2,
        "tags_in_db": 3,
        "vendors_in_db": 2,
        "stock_total": 8,
    }, f"Unexpected totals for batch B: {batch_b['totals']}"


def test_batch_b_persisted_state(batch_b):
    snapshot = db_snapshot()
    assert [p["sku"] for p in snapshot["products"]] == [
        "SKU0001",
        "SKU0002",
        "SKU0003",
    ], snapshot
    assert snapshot["sources"] == [{"code": "vendor_a", "revision": 2}], snapshot
    assert snapshot["vendors"] == [
        {"code": "acme", "name": "Acme Industries"},
        {"code": "globex", "name": "Globex"},
    ], f"Vendor acme must have been renamed: {snapshot['vendors']}"
    assert snapshot["tags"] == sorted(["kitchen", "caf\u00e9", "\u00e9lite"]), (
        f"The `kitchen` Tag object must survive being unlinked: {snapshot['tags']}"
    )

    first = product_by_sku(snapshot, "SKU0001")
    assert first is not None
    assert first["revision"] == 2, first
    assert first["tag_labels"] == sorted(["caf\u00e9", "\u00e9lite"]), first
    assert first["variants"] == [
        {"code": "L", "label": "Large", "stock": 1},
        {"code": "S", "label": "Small", "stock": 7},
    ], f"Variant M must be deleted and S updated: {first['variants']}"

    untouched = product_by_sku(snapshot, "SKU0002")
    assert untouched is not None
    assert untouched["title"] == "\u65e5\u672c\u8a9e Kettle", untouched
    assert untouched["price_cents"] == 4500, untouched
    assert untouched["revision"] == 1, (
        f"A product absent from batch B must stay untouched: {untouched}"
    )

    third = product_by_sku(snapshot, "SKU0003")
    assert third is not None
    assert third["tag_labels"] == ["kitchen"], third
    assert third["vendor_code"] == "globex", third


# --------------------------------------------------------------------------
# 7. rollback on invalid payloads
# --------------------------------------------------------------------------


def test_invalid_payload_rolls_everything_back(batch_b):
    before = db_snapshot()
    payload = {
        "source": "vendor_a",
        "revision": 3,
        "products": [
            {
                "sku": "SKU0004",
                "title": "Valid product",
                "price_cents": 500,
                "vendor": {"code": "acme", "name": "Acme Industries"},
                "tags": ["kitchen"],
                "variants": [{"code": "S", "label": "Small", "stock": 1}],
            },
            {
                "sku": "SKU0005",
                "title": "Broken product",
                "price_cents": -1,
                "vendor": {"code": "acme", "name": "Acme Industries"},
                "tags": [],
                "variants": [],
            },
        ],
    }
    path = write_payload("payload_c.json", payload)
    ingest_error(path, "INVALID_PAYLOAD", "batch C (negative price)")

    after = db_snapshot()
    assert after == before, (
        "A failed batch must leave the database byte-for-byte unchanged."
    )
    assert product_by_sku(after, "SKU0004") is None, (
        "SKU0004 was written even though the batch failed."
    )
    assert product_by_sku(after, "SKU0005") is None, (
        "SKU0005 was written even though the batch failed."
    )
    assert len(after["products"]) == 3, after
    assert after["sources"] == [{"code": "vendor_a", "revision": 2}], after


def _invalid_variants():
    duplicate_sku = copy.deepcopy(PAYLOAD_B)
    duplicate_sku["revision"] = 3
    duplicate_sku["products"][1]["sku"] = "SKU0001"

    duplicate_variant = copy.deepcopy(PAYLOAD_B)
    duplicate_variant["revision"] = 3
    duplicate_variant["products"][0]["variants"] = [
        {"code": "S", "label": "Small", "stock": 1},
        {"code": "S", "label": "Small again", "stock": 2},
    ]

    empty_products = {"source": "vendor_a", "revision": 3, "products": []}

    string_revision = copy.deepcopy(PAYLOAD_B)
    string_revision["revision"] = "3"

    empty_tag = copy.deepcopy(PAYLOAD_B)
    empty_tag["revision"] = 3
    empty_tag["products"][0]["tags"] = ["kitchen", ""]

    return [
        ("duplicate_sku", duplicate_sku),
        ("duplicate_variant_code", duplicate_variant),
        ("empty_products", empty_products),
        ("string_revision", string_revision),
        ("empty_tag_label", empty_tag),
    ]


@pytest.mark.parametrize("name,payload", _invalid_variants())
def test_malformed_payloads_are_rejected(batch_b, name, payload):
    before = db_snapshot()
    path = write_payload(f"payload_invalid_{name}.json", payload)
    ingest_error(path, "INVALID_PAYLOAD", f"invalid payload ({name})")
    assert db_snapshot() == before, (
        f"The invalid payload {name} modified the database."
    )


# --------------------------------------------------------------------------
# 8. IO errors
# --------------------------------------------------------------------------


def test_missing_input_file_is_io_error(batch_b):
    before = db_snapshot()
    ingest_error(
        os.path.join(WORK_DIR, "does-not-exist.json"), "IO_ERROR", "missing input file"
    )
    assert db_snapshot() == before, "A failed run must not modify the database."


def test_unparseable_input_is_io_error(batch_b):
    before = db_snapshot()
    path = write_payload("not-json.json", "not json at all")
    ingest_error(path, "IO_ERROR", "unparseable input file")
    assert db_snapshot() == before, "A failed run must not modify the database."


def test_missing_input_flag_is_io_error(batch_b):
    before = db_snapshot()
    proc = run_ingest(None)
    body = parse_stdout(proc, "missing --input flag")
    assert proc.returncode == 1, (
        f"Expected exit code 1 without --input, got {proc.returncode}: "
        f"{proc.stdout!r}"
    )
    assert body.get("ok") is False and body.get("error_code") == "IO_ERROR", (
        f"Expected an IO_ERROR object without --input, got {body!r}"
    )
    assert db_snapshot() == before, "A failed run must not modify the database."


# --------------------------------------------------------------------------
# 9. a brand new source
# --------------------------------------------------------------------------


def test_batch_d_counts_and_totals(batch_d):
    assert batch_d["source"] == "vendor_b", batch_d
    assert batch_d["counts"] == {
        "products_created": 0,
        "products_updated": 1,
        "vendors_created": 0,
        "vendors_updated": 0,
        "tags_created": 1,
        "variants_created": 1,
        "variants_updated": 0,
        "variants_removed": 0,
    }, f"Unexpected counts for batch D: {batch_d['counts']}"
    assert batch_d["totals"] == {
        "products_in_db": 3,
        "variants_in_db": 3,
        "tags_in_db": 4,
        "vendors_in_db": 2,
        "stock_total": 10,
    }, f"Unexpected totals for batch D: {batch_d['totals']}"
    entry = summary_product(batch_d, "SKU0002")
    assert entry["created"] is False, entry
    assert entry["tags"] == ["kitchen", "tea"], entry
    assert entry["variants"] == [{"code": "XL", "label": "Extra", "stock": 2}], entry


def test_batch_d_persisted_state(batch_d):
    snapshot = db_snapshot()
    assert snapshot["sources"] == [
        {"code": "vendor_a", "revision": 2},
        {"code": "vendor_b", "revision": 1},
    ], f"Each source must track its own revision: {snapshot['sources']}"
    product = product_by_sku(snapshot, "SKU0002")
    assert product is not None
    assert product["title"] == "Kettle Redux", product
    assert product["price_cents"] == 4600, product
    assert product["revision"] == 1, product
    assert product["vendor_code"] == "globex", product
    assert product["tag_labels"] == ["kitchen", "tea"], product
    assert product["variants"] == [{"code": "XL", "label": "Extra", "stock": 2}], product


# --------------------------------------------------------------------------
# 10. concurrency
# --------------------------------------------------------------------------


def test_concurrent_sources_do_not_collide(batch_d):
    payloads = []
    for source, sku, title in (
        ("conc_a", "SKU9001", "Concurrent A"),
        ("conc_b", "SKU9002", "Concurrent B"),
    ):
        payloads.append(
            write_payload(
                f"payload_{source}.json",
                {
                    "source": source,
                    "revision": 1,
                    "products": [
                        {
                            "sku": sku,
                            "title": title,
                            "price_cents": 100,
                            "vendor": {"code": "shared", "name": "Shared Co"},
                            "tags": ["shared_tag", "kitchen"],
                            "variants": [],
                        }
                    ],
                },
            )
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run_ingest, payloads))

    for path, proc in zip(payloads, results):
        body = parse_stdout(proc, f"concurrent run of {path}")
        assert proc.returncode == 0, (
            f"Concurrent run of {path} failed with exit code {proc.returncode}: "
            f"{proc.stdout!r}\n{proc.stderr[-2000:]!r}"
        )
        assert body.get("ok") is True, body

    assert (
        gel_query_one("select count((select Vendor filter .code = 'shared'))") == 1
    ), "Concurrent batches must end up sharing exactly one Vendor 'shared'."
    assert (
        gel_query_one("select count((select Tag filter .label = 'shared_tag'))") == 1
    ), "Concurrent batches must end up sharing exactly one Tag 'shared_tag'."
    assert (
        gel_query_one("select count((select Tag filter .label = 'kitchen'))") == 1
    ), "Concurrent batches must reuse the existing Tag 'kitchen'."
    snapshot = db_snapshot()
    assert product_by_sku(snapshot, "SKU9001") is not None, snapshot
    assert product_by_sku(snapshot, "SKU9002") is not None, snapshot
    sources = {row["code"]: row["revision"] for row in snapshot["sources"]}
    assert sources.get("conc_a") == 1, sources
    assert sources.get("conc_b") == 1, sources


# --------------------------------------------------------------------------
# 11. stdout hygiene
# --------------------------------------------------------------------------


def test_stdout_contains_only_json(batch_d):
    payload = copy.deepcopy(PAYLOAD_D)
    payload["revision"] = 2
    success_path = write_payload("payload_hygiene_ok.json", payload)
    proc = run_ingest(success_path)
    assert proc.returncode == 0, (
        f"Expected the follow-up batch to succeed: {proc.stdout!r}\n{proc.stderr!r}"
    )
    body = json.loads(proc.stdout)
    assert isinstance(body, dict) and body.get("ok") is True, body

    failure_path = write_payload("payload_hygiene_bad.json", "{ broken")
    proc = run_ingest(failure_path)
    assert proc.returncode == 1, proc.stdout
    body = json.loads(proc.stdout)
    assert isinstance(body, dict) and body.get("ok") is False, body
