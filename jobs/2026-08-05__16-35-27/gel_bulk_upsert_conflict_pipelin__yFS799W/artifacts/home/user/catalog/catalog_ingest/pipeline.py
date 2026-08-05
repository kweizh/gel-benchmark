"""Idempotent bulk catalog ingestion pipeline.

The public entry point is :func:`ingest_batch`, an ``async`` coroutine that
upserts a batch of product-catalog records against a Gel database through a
caller-supplied asynchronous client.  Replay safety, per-record acceptance
rules, atomic writes and a strict EdgeQL round-trip budget are all enforced
here.
"""

from __future__ import annotations

import datetime
import json
import typing

__all__ = ("ingest_batch",)

# ---------------------------------------------------------------------------
# EdgeQL statements
# ---------------------------------------------------------------------------

# Statement 1 – which of the requested supplier codes actually exist.
_SUPPLIER_CODES_QUERY = r"""
SELECT Supplier.code
FILTER .code IN array_unpack(<array<str>>$codes)
"""

# Statement 2 – current state of every product whose natural key is among the
# accepted records.  Only the three fields that participate in the
# insert/update/unchanged decision are fetched.
_EXISTING_PRODUCTS_QUERY = r"""
SELECT Product {
    source_system,
    external_id,
    name,
    price_cents,
    supplier_code := .supplier.code,
}
FILTER (.source_system, .external_id)
       IN array_unpack(<array<tuple<str, str>>$keys)
"""

# Statement 3 – the actual write.  New products are inserted (revision 1) and
# changed products are updated (revision + 1) in a single atomic statement.
# The two FOR-loops are each referenced exactly once (inside ``count``) so the
# INSERT/UPDATE side effects fire exactly once per record.
_UPSERT_QUERY = r"""
WITH
    to_insert := array_unpack(
        <array<tuple<str, str, str, int64, str, datetime>>$to_insert
    ),
    to_update := array_unpack(
        <array<tuple<str, str, str, int64, str, datetime>>$to_update
    ),
    new_products := (
        FOR rec IN to_insert UNION (
            INSERT Product {
                source_system := rec.0,
                external_id := rec.1,
                name := rec.2,
                price_cents := rec.3,
                revision := 1,
                updated_at := rec.5,
                supplier := (SELECT Supplier FILTER .code = rec.4 LIMIT 1),
            }
        )
    ),
    updated_products := (
        FOR rec IN to_update UNION (
            UPDATE Product
            FILTER .source_system = rec.0 AND .external_id = rec.1
            SET {
                name := rec.2,
                price_cents := rec.3,
                supplier := (SELECT Supplier FILTER .code = rec.4 LIMIT 1),
                revision := .revision + 1,
                updated_at := rec.5,
            }
        )
    )
SELECT {
    inserted := count(new_products),
    updated := count(updated_products),
};
"""

# Keys that make up a well-formed record.
_REQUIRED_STR_KEYS: typing.Final[tuple[str, ...]] = (
    "source_system",
    "external_id",
    "name",
    "supplier_code",
)


def _is_well_formed(record: typing.Any) -> bool:
    """Return ``True`` when *record* satisfies the well-formedness rules.

    A well-formed record is a ``dict`` carrying the five required fields with
    the expected types and value ranges.  ``bool`` is explicitly rejected as a
    substitute for ``int`` on ``price_cents``.  Extra keys are ignored.
    """
    if not isinstance(record, dict):
        return False
    for key in _REQUIRED_STR_KEYS:
        val = record.get(key)
        if not isinstance(val, str) or val == "":
            return False
    price = record.get("price_cents")
    # bool is a subclass of int in Python, so it must be excluded explicitly.
    if not isinstance(price, int) or isinstance(price, bool) or price < 0:
        return False
    return True


async def ingest_batch(
    client: typing.Any,
    records: list[typing.Any],
) -> dict[str, typing.Any]:
    """Ingest *records* idempotently through *client*.

    Parameters
    ----------
    client:
        An already-connected asynchronous Gel client.  The function works
        exclusively through this object and never opens a connection of its
        own.
    records:
        A list of JSON-ish dicts describing product-catalog rows.

    Returns
    -------
    dict
        A dictionary with exactly the keys ``inserted``, ``updated``,
        ``unchanged``, ``rejected`` and ``rejects``.
    """
    now = datetime.datetime.now(datetime.timezone.utc)

    # -- Phase 1: classify invalid_record (pure Python, no DB) ---------------
    well_formed: list[tuple[int, dict]] = []
    invalid_rejects: list[dict] = []
    for index, record in enumerate(records):
        if _is_well_formed(record):
            well_formed.append((index, record))
        else:
            invalid_rejects.append({"index": index, "reason": "invalid_record"})

    # Nothing well-formed → no database access required at all.
    if not well_formed:
        rejects = sorted(invalid_rejects, key=lambda r: r["index"])
        return {
            "inserted": 0,
            "updated": 0,
            "unchanged": 0,
            "rejected": len(rejects),
            "rejects": rejects,
        }

    # -- Phase 2: query existing supplier codes (Statement 1) ----------------
    supplier_codes_needed = list({r["supplier_code"] for _, r in well_formed})
    existing_codes = set(
        await client.query(_SUPPLIER_CODES_QUERY, codes=supplier_codes_needed)
    )

    # -- Phase 3: classify unknown_supplier -----------------------------------
    known_supplier: list[tuple[int, dict]] = []
    unknown_rejects: list[dict] = []
    for index, record in well_formed:
        if record["supplier_code"] in existing_codes:
            known_supplier.append((index, record))
        else:
            unknown_rejects.append(
                {"index": index, "reason": "unknown_supplier"}
            )

    # -- Phase 4: classify duplicate_key (pure Python) ------------------------
    final_accepted: list[tuple[int, dict]] = []
    duplicate_rejects: list[dict] = []
    seen_keys: set[tuple[str, str]] = set()
    for index, record in known_supplier:
        key = (record["source_system"], record["external_id"])
        if key in seen_keys:
            duplicate_rejects.append(
                {"index": index, "reason": "duplicate_key"}
            )
        else:
            seen_keys.add(key)
            final_accepted.append((index, record))

    # -- Phase 5: query existing products (Statement 2) -----------------------
    keys = [
        (r["source_system"], r["external_id"]) for _, r in final_accepted
    ]
    existing_products: dict[tuple[str, str], dict] = {}
    if keys:
        raw = await client.query_json(_EXISTING_PRODUCTS_QUERY, keys=keys)
        for product in json.loads(raw):
            existing_products[
                (product["source_system"], product["external_id"])
            ] = product

    # -- Phase 6: classify insert / update / unchanged ------------------------
    to_insert: list[dict] = []
    to_update: list[dict] = []
    for _, record in final_accepted:
        key = (record["source_system"], record["external_id"])
        existing = existing_products.get(key)
        if existing is None:
            to_insert.append(record)
        elif (
            existing["name"] == record["name"]
            and existing["price_cents"] == record["price_cents"]
            and existing["supplier_code"] == record["supplier_code"]
        ):
            pass  # unchanged – no database operation
        else:
            to_update.append(record)

    # -- Phase 7: upsert (Statement 3) ----------------------------------------
    inserted = 0
    updated = 0
    if to_insert or to_update:
        insert_data = [
            (
                r["source_system"],
                r["external_id"],
                r["name"],
                r["price_cents"],
                r["supplier_code"],
                now,
            )
            for r in to_insert
        ]
        update_data = [
            (
                r["source_system"],
                r["external_id"],
                r["name"],
                r["price_cents"],
                r["supplier_code"],
                now,
            )
            for r in to_update
        ]
        raw = await client.query_required_single_json(
            _UPSERT_QUERY,
            to_insert=insert_data,
            to_update=update_data,
        )
        counts = json.loads(raw)
        inserted = counts["inserted"]
        updated = counts["updated"]

    unchanged = len(final_accepted) - inserted - updated

    # -- Assemble the return value --------------------------------------------
    rejects = invalid_rejects + unknown_rejects + duplicate_rejects
    rejects.sort(key=lambda r: r["index"])

    return {
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "rejected": len(rejects),
        "rejects": rejects,
    }
