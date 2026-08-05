import asyncio
import glob
import os
import subprocess
import sys
import time

import gel
import gel.errors
import pytest

PROJECT_DIR = "/home/user/catalog"
INSTANCE_NAME = "geltask"

os.environ["HOME"] = "/root"
os.environ["GEL_INSTANCE"] = INSTANCE_NAME

SUPPLIERS = (
    ("ACME", "Acme Tools"),
    ("GLOBEX", "Globex Supply"),
    ("INITECH", "Initech Parts"),
)

RAW_INSERT = """
insert Product {
    source_system := <str>$source_system,
    external_id := <str>$external_id,
    name := <str>$name,
    price_cents := <int64>$price_cents,
    revision := <int64>$revision,
    updated_at := datetime_of_statement(),
    supplier := assert_exists((select Supplier filter .code = <str>$supplier_code)),
}
"""

PRODUCT_SHAPE = """
select Product {
    source_system,
    external_id,
    name,
    price_cents,
    revision,
    updated_at,
    supplier_code := .supplier.code,
}
"""


def _run(args, cwd=None, timeout=300):
    return subprocess.run(
        args,
        cwd=cwd,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# --------------------------------------------------------------------------- #
# Counting proxy used to observe the per-batch round-trip budget.
# --------------------------------------------------------------------------- #

_COUNTED_METHODS = (
    "query",
    "query_single",
    "query_required_single",
    "query_json",
    "query_single_json",
    "query_required_single_json",
    "execute",
)


class Counter:
    def __init__(self):
        self.count = 0

    def bump(self):
        self.count += 1


class _CountingProxy:
    def __init__(self, inner, counter):
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_counter", counter)

    def __getattr__(self, name):
        attr = getattr(object.__getattribute__(self, "_inner"), name)
        counter = object.__getattribute__(self, "_counter")
        if name in _COUNTED_METHODS and callable(attr):

            async def _counted(*args, **kwargs):
                counter.bump()
                return await attr(*args, **kwargs)

            return _counted
        return attr


class CountingTransaction(_CountingProxy):
    async def __aenter__(self):
        await object.__getattribute__(self, "_inner").__aenter__()
        return self

    async def __aexit__(self, *exc_info):
        return await object.__getattribute__(self, "_inner").__aexit__(*exc_info)


class CountingRetry:
    def __init__(self, inner, counter):
        self._inner = inner
        self._counter = counter
        self._iter = None

    def __aiter__(self):
        self._iter = self._inner.__aiter__()
        return self

    async def __anext__(self):
        tx = await self._iter.__anext__()
        return CountingTransaction(tx, self._counter)


class CountingClient(_CountingProxy):
    def transaction(self):
        inner = object.__getattribute__(self, "_inner")
        counter = object.__getattribute__(self, "_counter")
        return CountingRetry(inner.transaction(), counter)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="session")
def client():
    """Start the local Gel instance (idempotent) and hand back a blocking client."""
    os.chdir(PROJECT_DIR)
    if PROJECT_DIR not in sys.path:
        sys.path.insert(0, PROJECT_DIR)

    started = _run(["gel-start-instance"])
    assert started.returncode == 0, (
        "`gel-start-instance` failed to bring the local Gel instance up: "
        f"stdout={started.stdout!r} stderr={started.stderr!r}"
    )

    last_error = None
    for _ in range(20):
        try:
            c = gel.create_client(timeout=15)
            c.query_single("select 1")
            yield c
            c.close()
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(2)
    raise AssertionError(
        f"Could not connect to the local Gel instance '{INSTANCE_NAME}': {last_error}"
    )


@pytest.fixture()
def db(client):
    """Reset the data (never the schema) before every test."""
    try:
        client.execute("delete Product;")
    except Exception as exc:  # noqa: BLE001
        pytest.fail(
            "Could not delete all `default::Product` objects; the `Product` object type "
            f"is missing or unusable: {exc}"
        )
    client.execute("delete Supplier;")
    for code, name in SUPPLIERS:
        client.query(
            "insert Supplier { code := <str>$code, name := <str>$name }",
            code=code,
            name=name,
        )
    return client


def _load_ingest_batch():
    if PROJECT_DIR not in sys.path:
        sys.path.insert(0, PROJECT_DIR)
    try:
        from catalog_ingest.pipeline import ingest_batch
    except Exception as exc:  # noqa: BLE001
        pytest.fail(
            "Could not import `ingest_batch` from `catalog_ingest.pipeline` with "
            f"{PROJECT_DIR} on sys.path: {exc}"
        )
    assert asyncio.iscoroutinefunction(ingest_batch), (
        "`catalog_ingest.pipeline.ingest_batch` must be an async (coroutine) function."
    )
    return ingest_batch


def ingest(records, counter=None):
    """Call the executor's ingest_batch with a freshly connected async client."""
    ingest_batch = _load_ingest_batch()

    async def _main():
        real = gel.create_async_client()
        try:
            await real.ensure_connected()
            supplied = CountingClient(real, counter) if counter is not None else real
            return await ingest_batch(supplied, records)
        finally:
            await real.aclose()

    return asyncio.run(_main())


def products_by_key(client):
    rows = client.query(PRODUCT_SHAPE)
    return {(r.source_system, r.external_id): r for r in rows}


def check_shape(stats, n_records):
    assert isinstance(stats, dict), f"ingest_batch must return a dict, got {type(stats)!r}"
    assert set(stats.keys()) == {
        "inserted",
        "updated",
        "unchanged",
        "rejected",
        "rejects",
    }, f"Unexpected key set in the returned statistics: {sorted(stats.keys())}"
    for key in ("inserted", "updated", "unchanged", "rejected"):
        assert isinstance(stats[key], int) and not isinstance(stats[key], bool), (
            f"Statistics key {key!r} must be an int, got {stats[key]!r}"
        )
    assert isinstance(stats["rejects"], list), (
        f"`rejects` must be a list, got {type(stats['rejects'])!r}"
    )
    for entry in stats["rejects"]:
        assert isinstance(entry, dict), f"Each reject must be a dict, got {entry!r}"
        assert set(entry.keys()) == {"index", "reason"}, (
            f"Each reject must have exactly the keys index/reason, got {sorted(entry.keys())}"
        )
        assert isinstance(entry["index"], int) and not isinstance(entry["index"], bool), (
            f"Reject `index` must be an int, got {entry['index']!r}"
        )
        assert entry["reason"] in {
            "invalid_record",
            "unknown_supplier",
            "duplicate_key",
        }, f"Unexpected reject reason {entry['reason']!r}"
    indices = [e["index"] for e in stats["rejects"]]
    assert indices == sorted(indices), f"`rejects` must be ordered by index, got {indices}"
    assert stats["rejected"] == len(stats["rejects"]), (
        f"`rejected` ({stats['rejected']}) must equal len(rejects) ({len(stats['rejects'])})"
    )
    total = stats["inserted"] + stats["updated"] + stats["unchanged"] + stats["rejected"]
    assert total == n_records, (
        f"inserted+updated+unchanged+rejected must equal len(records)={n_records}, got {total}"
    )


def rec(source_system, external_id, name, price_cents, supplier_code):
    return {
        "source_system": source_system,
        "external_id": external_id,
        "name": name,
        "price_cents": price_cents,
        "supplier_code": supplier_code,
    }


BASE_BATCH = [
    rec("erp", "P-1", "Hex Bolt", 120, "ACME"),
    rec("erp", "P-2", "Torque Wrench", 4500, "GLOBEX"),
    rec("wms", "P-1", "Pallet Jack", 89000, "INITECH"),
]


# --------------------------------------------------------------------------- #
# 1. Migration history
# --------------------------------------------------------------------------- #


def test_migration_history_is_in_sync(client):
    proc = _run(["gel", "migration", "status"], cwd=PROJECT_DIR)
    combined = (proc.stdout + proc.stderr).lower()
    assert proc.returncode == 0, (
        "`gel migration status` must succeed in the project directory. "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert "up to date" in combined, (
        f"The project schema and the instance must be in sync, got: {combined!r}"
    )


def test_new_migration_file_was_created(client):
    migrations = sorted(
        glob.glob(os.path.join(PROJECT_DIR, "dbschema", "migrations", "*.edgeql"))
    )
    assert len(migrations) >= 2, (
        "The `Product` type must be introduced through a new migration file under "
        f"dbschema/migrations; found only {migrations}"
    )


# --------------------------------------------------------------------------- #
# 2. Schema-level guarantees enforced by the database itself
# --------------------------------------------------------------------------- #


def test_natural_key_is_exclusive_in_the_database(db):
    db.query(
        RAW_INSERT,
        source_system="sys",
        external_id="K1",
        name="A",
        price_cents=10,
        revision=1,
        supplier_code="ACME",
    )
    with pytest.raises(gel.errors.ConstraintViolationError):
        db.query(
            RAW_INSERT,
            source_system="sys",
            external_id="K1",
            name="A duplicate",
            price_cents=11,
            revision=1,
            supplier_code="ACME",
        )


def test_same_external_id_under_another_source_system_is_allowed(db):
    db.query(
        RAW_INSERT,
        source_system="sys",
        external_id="K1",
        name="A",
        price_cents=10,
        revision=1,
        supplier_code="ACME",
    )
    db.query(
        RAW_INSERT,
        source_system="other",
        external_id="K1",
        name="B",
        price_cents=10,
        revision=1,
        supplier_code="ACME",
    )
    assert len(products_by_key(db)) == 2, (
        "Only the pair (source_system, external_id) may be exclusive, not external_id alone."
    )


def test_database_rejects_overlong_name(db):
    with pytest.raises(gel.errors.ConstraintViolationError):
        db.query(
            RAW_INSERT,
            source_system="sys",
            external_id="K2",
            name="X" * 201,
            price_cents=10,
            revision=1,
            supplier_code="ACME",
        )


def test_database_rejects_negative_price(db):
    with pytest.raises(gel.errors.ConstraintViolationError):
        db.query(
            RAW_INSERT,
            source_system="sys",
            external_id="K3",
            name="Cheap",
            price_cents=-1,
            revision=1,
            supplier_code="ACME",
        )


# --------------------------------------------------------------------------- #
# 3-6. Ingestion behaviour
# --------------------------------------------------------------------------- #


def test_fresh_insert(db):
    stats = ingest(list(BASE_BATCH))
    check_shape(stats, len(BASE_BATCH))
    assert stats == {
        "inserted": 3,
        "updated": 0,
        "unchanged": 0,
        "rejected": 0,
        "rejects": [],
    }, f"Unexpected statistics for a batch of three brand-new products: {stats}"

    stored = products_by_key(db)
    assert set(stored) == {("erp", "P-1"), ("erp", "P-2"), ("wms", "P-1")}, (
        f"Unexpected products in the database: {sorted(stored)}"
    )
    assert stored[("erp", "P-1")].supplier_code == "ACME"
    assert stored[("erp", "P-2")].supplier_code == "GLOBEX"
    assert stored[("wms", "P-1")].supplier_code == "INITECH"
    assert stored[("erp", "P-2")].price_cents == 4500
    assert stored[("wms", "P-1")].name == "Pallet Jack"
    for key, row in stored.items():
        assert row.revision == 1, f"A freshly inserted product must have revision 1, {key} has {row.revision}"


def test_identical_replay_changes_nothing(db):
    ingest(list(BASE_BATCH))
    before = {k: (v.revision, v.updated_at) for k, v in products_by_key(db).items()}

    stats = ingest(list(BASE_BATCH))
    check_shape(stats, len(BASE_BATCH))
    assert stats == {
        "inserted": 0,
        "updated": 0,
        "unchanged": 3,
        "rejected": 0,
        "rejects": [],
    }, f"Replaying an identical batch must report three unchanged products, got {stats}"

    after = {k: (v.revision, v.updated_at) for k, v in products_by_key(db).items()}
    assert after == before, (
        "Replaying an identical batch must not touch `revision` or `updated_at`.\n"
        f"before={before}\nafter={after}"
    )


def test_mixed_insert_update_unchanged_batch(db):
    ingest(list(BASE_BATCH))
    before = {k: (v.revision, v.updated_at) for k, v in products_by_key(db).items()}

    mixed = [
        rec("erp", "P-1", "Hex Bolt", 120, "ACME"),
        rec("erp", "P-2", "Torque Wrench", 4600, "GLOBEX"),
        rec("wms", "P-1", "Pallet Jack", 89000, "ACME"),
        rec("erp", "P-9", "Caliper", 2599, "GLOBEX"),
    ]
    stats = ingest(mixed)
    check_shape(stats, len(mixed))
    assert stats == {
        "inserted": 1,
        "updated": 2,
        "unchanged": 1,
        "rejected": 0,
        "rejects": [],
    }, f"Unexpected statistics for the mixed batch: {stats}"

    stored = products_by_key(db)
    untouched = stored[("erp", "P-1")]
    assert (untouched.revision, untouched.updated_at) == before[("erp", "P-1")], (
        "The record that did not change must keep its revision and updated_at."
    )

    repriced = stored[("erp", "P-2")]
    assert repriced.price_cents == 4600, "The new price must be persisted."
    assert repriced.revision == before[("erp", "P-2")][0] + 1, (
        f"A changed product must bump revision by exactly 1, got {repriced.revision}"
    )
    assert repriced.updated_at > before[("erp", "P-2")][1], (
        "A changed product must get a newer updated_at."
    )

    relinked = stored[("wms", "P-1")]
    assert relinked.supplier_code == "ACME", "The supplier link must be re-pointed."
    assert relinked.revision == before[("wms", "P-1")][0] + 1
    assert relinked.updated_at > before[("wms", "P-1")][1]

    fresh = stored[("erp", "P-9")]
    assert fresh.revision == 1 and fresh.price_cents == 2599


def test_name_only_change_counts_as_update(db):
    ingest([rec("erp", "P-9", "Caliper", 2599, "GLOBEX")])
    stats = ingest([rec("erp", "P-9", "Digital Caliper", 2599, "GLOBEX")])
    check_shape(stats, 1)
    assert stats == {
        "inserted": 0,
        "updated": 1,
        "unchanged": 0,
        "rejected": 0,
        "rejects": [],
    }, f"A name-only change must be reported as one update, got {stats}"
    stored = products_by_key(db)[("erp", "P-9")]
    assert stored.name == "Digital Caliper"
    assert stored.revision == 2, f"Expected revision 2 after one change, got {stored.revision}"


# --------------------------------------------------------------------------- #
# 7-8. Dirty batches
# --------------------------------------------------------------------------- #


def test_dirty_batch_rejects(db):
    dirty = [
        rec("erp", "D-1", "Good One", 10, "ACME"),
        "not-a-dict",
        {"source_system": "erp", "external_id": "D-2", "name": "No price", "supplier_code": "ACME"},
        rec("erp", "D-3", "", 5, "ACME"),
        rec("erp", "D-4", "Negative", -5, "ACME"),
        rec("erp", "D-5", "Float", 3.5, "ACME"),
        rec("erp", "D-6", "Boolish", True, "ACME"),
        rec("", "D-7", "Blank source", 5, "ACME"),
        rec("erp", "D-8", "Ghost supplier", 5, "NOPE"),
        {
            "source_system": "erp",
            "external_id": "D-1",
            "name": "Shadow",
            "price_cents": 999,
            "supplier_code": "GLOBEX",
            "extra": "ignored",
        },
        rec("erp", "D-9", "Good Two", 20, "INITECH"),
    ]
    stats = ingest(dirty)
    check_shape(stats, len(dirty))

    assert stats["inserted"] == 2, f"Exactly two records are acceptable, got {stats}"
    assert stats["updated"] == 0, f"No record can be an update here, got {stats}"
    assert stats["unchanged"] == 0, f"No record can be unchanged here, got {stats}"
    assert stats["rejects"] == [
        {"index": 1, "reason": "invalid_record"},
        {"index": 2, "reason": "invalid_record"},
        {"index": 3, "reason": "invalid_record"},
        {"index": 4, "reason": "invalid_record"},
        {"index": 5, "reason": "invalid_record"},
        {"index": 6, "reason": "invalid_record"},
        {"index": 7, "reason": "invalid_record"},
        {"index": 8, "reason": "unknown_supplier"},
        {"index": 9, "reason": "duplicate_key"},
    ], f"Unexpected rejects: {stats['rejects']}"

    stored = products_by_key(db)
    assert set(stored) == {("erp", "D-1"), ("erp", "D-9")}, (
        f"Only the two acceptable records may reach the database, found {sorted(stored)}"
    )
    first = stored[("erp", "D-1")]
    assert first.name == "Good One", "The first occurrence of a duplicated key wins."
    assert first.price_cents == 10, "The shadow duplicate must not overwrite the first record."
    assert first.supplier_code == "ACME"
    assert first.revision == 1, "The rejected duplicate must not bump the revision."
    second = stored[("erp", "D-9")]
    assert second.price_cents == 20 and second.supplier_code == "INITECH"


def test_duplicate_of_a_rejected_record_is_not_a_duplicate(db):
    batch = [
        rec("erp", "R-1", "Ghost", 100, "NOPE"),
        rec("erp", "R-1", "Real", 100, "ACME"),
    ]
    stats = ingest(batch)
    check_shape(stats, len(batch))
    assert stats["rejects"] == [{"index": 0, "reason": "unknown_supplier"}], (
        f"Only the record with the unknown supplier may be rejected, got {stats['rejects']}"
    )
    assert stats["inserted"] == 1, (
        f"The second record must be applied, not treated as a duplicate: {stats}"
    )
    stored = products_by_key(db)
    assert set(stored) == {("erp", "R-1")}
    assert stored[("erp", "R-1")].name == "Real"


# --------------------------------------------------------------------------- #
# 9. Atomicity
# --------------------------------------------------------------------------- #


def test_failed_batch_leaves_no_partial_state(db):
    ingest(
        [
            rec("erp", "A-1", "Alpha", 100, "ACME"),
            rec("erp", "A-2", "Beta", 200, "GLOBEX"),
        ]
    )
    before = {
        k: (v.name, v.price_cents, v.revision, v.updated_at)
        for k, v in products_by_key(db).items()
    }

    poisoned = [
        rec("erp", "A-1", "Alpha", 175, "ACME"),
        rec("erp", "A-3", "Gamma", 300, "INITECH"),
        rec("erp", "A-4", "Y" * 250, 400, "ACME"),
    ]
    with pytest.raises(Exception):
        ingest(poisoned)

    after = {
        k: (v.name, v.price_cents, v.revision, v.updated_at)
        for k, v in products_by_key(db).items()
    }
    assert set(after) == {("erp", "A-1"), ("erp", "A-2")}, (
        "A batch refused by the database must not create any product; "
        f"found {sorted(after)}"
    )
    assert after == before, (
        "A batch refused by the database must not modify any product.\n"
        f"before={before}\nafter={after}"
    )


# --------------------------------------------------------------------------- #
# 10. Round-trip budget
# --------------------------------------------------------------------------- #


def _bulk_records(n):
    codes = [code for code, _ in SUPPLIERS]
    return [
        rec("bulk", f"B-{i:04d}", f"Bulk Item {i}", 100 + i, codes[i % len(codes)])
        for i in range(n)
    ]


def test_round_trip_budget_and_supplied_client_usage(db):
    records = _bulk_records(120)

    counter = Counter()
    stats = ingest(records, counter=counter)
    check_shape(stats, len(records))
    assert stats["inserted"] == 120, f"All 120 records must be inserted, got {stats}"
    assert counter.count >= 1, (
        "`ingest_batch` must run its statements through the client object it was given."
    )
    assert counter.count <= 3, (
        "A batch may cost at most 3 EdgeQL statement executions, "
        f"but {counter.count} were observed for 120 records."
    )
    assert len(products_by_key(db)) == 120

    replay_counter = Counter()
    replay = ingest(records, counter=replay_counter)
    check_shape(replay, len(records))
    assert replay["unchanged"] == 120, (
        f"Replaying the same 120 records must report 120 unchanged, got {replay}"
    )
    assert 1 <= replay_counter.count <= 3, (
        "The replay must also stay within the 3-statement budget, "
        f"observed {replay_counter.count}."
    )
    revisions = {row.revision for row in products_by_key(db).values()}
    assert revisions == {1}, f"No revision may have been bumped by the replay, got {revisions}"


def test_small_batch_uses_the_same_budget(db):
    counter = Counter()
    stats = ingest(list(BASE_BATCH), counter=counter)
    check_shape(stats, len(BASE_BATCH))
    assert stats["inserted"] == 3
    assert 1 <= counter.count <= 3, (
        f"A three-record batch must stay within the 3-statement budget, observed {counter.count}."
    )
