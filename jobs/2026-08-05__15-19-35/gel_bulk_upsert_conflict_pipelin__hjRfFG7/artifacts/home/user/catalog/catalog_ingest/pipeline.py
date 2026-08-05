"""Idempotent bulk catalog ingestion for the Gel-backed product catalog.

`ingest_batch` accepts a batch of loosely-structured ERP records and applies
the well-formed, non-duplicate, known-supplier ones to the database, using at
most two round-trips (well under the three-statement budget):

1. One query to fetch the set of known supplier codes.
2. One atomic upsert query (skipped entirely if nothing was accepted) that
   inserts new products, updates changed ones, and leaves unchanged ones
   untouched -- all within a single EdgeQL statement, which Gel executes
   atomically.
"""

from __future__ import annotations

import json
from typing import Any

INVALID_RECORD = "invalid_record"
UNKNOWN_SUPPLIER = "unknown_supplier"
DUPLICATE_KEY = "duplicate_key"

_SUPPLIER_CODES_QUERY = "select Supplier.code;"

_UPSERT_QUERY = """
with items := <json>$items,
select (
  for item in json_array_unpack(items) union (
    (insert Product {
        source_system := <str>item['source_system'],
        external_id := <str>item['external_id'],
        name := <str>item['name'],
        price_cents := <int64>item['price_cents'],
        revision := 1,
        updated_at := datetime_current(),
        supplier := (select Supplier filter .code = <str>item['supplier_code']),
    }
    unless conflict on ((.source_system, .external_id))
    else (
      update Product
      filter (
        .name != <str>item['name']
        or .price_cents != <int64>item['price_cents']
        or .supplier.code != <str>item['supplier_code']
      )
      set {
        name := <str>item['name'],
        price_cents := <int64>item['price_cents'],
        supplier := (select Supplier filter .code = <str>item['supplier_code']),
        revision := .revision + 1,
        updated_at := datetime_current(),
      }
    ))
  )
) { revision };
"""


def _is_well_formed(record: Any) -> bool:
    if not isinstance(record, dict):
        return False

    source_system = record.get("source_system")
    external_id = record.get("external_id")
    name = record.get("name")
    price_cents = record.get("price_cents")
    supplier_code = record.get("supplier_code")

    if not isinstance(source_system, str) or source_system == "":
        return False
    if not isinstance(external_id, str) or external_id == "":
        return False
    if not isinstance(name, str) or name == "":
        return False
    if not isinstance(supplier_code, str) or supplier_code == "":
        return False
    # bool is a subclass of int in Python; explicitly excluded per spec.
    if isinstance(price_cents, bool) or not isinstance(price_cents, int):
        return False
    if price_cents < 0:
        return False

    return True


async def ingest_batch(client: Any, records: list) -> dict:
    rejects: list[dict] = []

    # Pass 1 (pure Python): rule 1 -- well-formedness.
    candidates: list[tuple[int, dict]] = []
    for index, record in enumerate(records):
        if _is_well_formed(record):
            candidates.append((index, record))
        else:
            rejects.append({"index": index, "reason": INVALID_RECORD})

    # Round-trip 1/2: learn which supplier codes actually exist.
    known_supplier_codes = set(await client.query(_SUPPLIER_CODES_QUERY))

    # Pass 2 (pure Python): rule 2 -- unknown supplier, rule 3 -- duplicate
    # natural key among earlier, non-rejected records of this same call.
    seen_keys: set[tuple[str, str]] = set()
    accepted: list[dict] = []
    for index, record in candidates:
        supplier_code = record["supplier_code"]
        if supplier_code not in known_supplier_codes:
            rejects.append({"index": index, "reason": UNKNOWN_SUPPLIER})
            continue

        natural_key = (record["source_system"], record["external_id"])
        if natural_key in seen_keys:
            rejects.append({"index": index, "reason": DUPLICATE_KEY})
            continue
        seen_keys.add(natural_key)

        accepted.append(
            {
                "source_system": record["source_system"],
                "external_id": record["external_id"],
                "name": record["name"],
                "price_cents": record["price_cents"],
                "supplier_code": supplier_code,
            }
        )

    inserted = 0
    updated = 0
    unchanged = 0

    if accepted:
        # Round-trip 2/2: a single atomic insert-or-update statement for the
        # whole accepted set. Gel executes a single query atomically, so
        # either every write in this call commits or none do.
        results = await client.query(_UPSERT_QUERY, items=json.dumps(accepted))
        for obj in results:
            if obj.revision == 1:
                inserted += 1
            else:
                updated += 1
        unchanged = len(accepted) - inserted - updated

    rejects.sort(key=lambda r: r["index"])

    return {
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "rejected": len(rejects),
        "rejects": rejects,
    }
