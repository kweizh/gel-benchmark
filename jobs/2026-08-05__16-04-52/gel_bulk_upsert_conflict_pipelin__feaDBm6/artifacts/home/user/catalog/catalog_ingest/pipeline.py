from datetime import datetime, timezone
import json


def _is_valid_non_empty_str(value):
    """Return True if value is a non-empty str."""
    return isinstance(value, str) and len(value) > 0


def _is_valid_int(value):
    """Return True if value is an int but not a bool."""
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_record(record):
    """Check if a record is well-formed.

    Returns True if valid, False otherwise.
    """
    if not isinstance(record, dict):
        return False

    if not _is_valid_non_empty_str(record.get("source_system")):
        return False

    if not _is_valid_non_empty_str(record.get("external_id")):
        return False

    if not _is_valid_non_empty_str(record.get("name")):
        return False

    price = record.get("price_cents")
    if not _is_valid_int(price) or price < 0:
        return False

    if not _is_valid_non_empty_str(record.get("supplier_code")):
        return False

    return True


def _natural_key(record):
    """Return the natural key tuple for a record."""
    return (record["source_system"], record["external_id"])


async def ingest_batch(client, records):
    """Ingest a batch of product catalog records idempotently.

    At most 3 EdgeQL statements are executed through *client*.
    """
    now = datetime.now(timezone.utc)

    # ── Statement 1: fetch existing suppliers and products ──────────────
    # Collect all supplier codes and natural keys from records that have
    # at least those fields in the right shape, so we can look them up.
    supplier_codes = set()
    natural_keys_ss = set()
    natural_keys_ei = set()
    natural_key_pairs = []

    for rec in records:
        if isinstance(rec, dict):
            sc = rec.get("supplier_code")
            if _is_valid_non_empty_str(sc):
                supplier_codes.add(sc)
            ss = rec.get("source_system")
            ei = rec.get("external_id")
            if _is_valid_non_empty_str(ss) and _is_valid_non_empty_str(ei):
                natural_key_pairs.append((ss, ei))

    supplier_codes_list = list(supplier_codes)

    # Build a JSON array of natural key pairs for the query
    nk_json = json.dumps(
        [{"ss": nk[0], "ei": nk[1]} for nk in natural_key_pairs]
    )

    result1_json = await client.query_json(
        """\
        with
          codes := <array<str>>$supplier_codes,
          keys := <array<tuple<ss: str, ei: str>>>json_array_unpack(<json>$natural_keys),
          existing_suppliers := (
            select Supplier { code }
            filter .code in array_unpack(codes)
          ),
          existing_products := (
            select Product {
              source_system,
              external_id,
              name,
              price_cents,
              revision,
              supplier: { code }
            }
            filter (
              (.source_system, .external_id) in array_unpack(keys)
            )
          ),
        select {
          supplier_codes := array_agg(existing_suppliers.code),
          products := array_agg(existing_products {
            source_system,
            external_id,
            name,
            price_cents,
            revision,
            supplier_code := .supplier.code,
          }),
        }
        """,
        supplier_codes=supplier_codes_list,
        natural_keys=nk_json,
    )

    result1 = json.loads(result1_json)
    existing_supplier_codes = set(result1["supplier_codes"])
    existing_products = {}
    for p in result1["products"]:
        nk = (p["source_system"], p["external_id"])
        existing_products[nk] = p

    # ── Classify records ────────────────────────────────────────────────
    rejects = []
    to_insert = []
    to_update = []
    unchanged_count = 0
    seen_natural_keys = {}  # nk -> index of first non-rejected record

    for idx, rec in enumerate(records):
        # Rule 1: not well-formed
        if not _validate_record(rec):
            rejects.append({"index": idx, "reason": "invalid_record"})
            continue

        # Rule 2: unknown supplier
        if rec["supplier_code"] not in existing_supplier_codes:
            rejects.append({"index": idx, "reason": "unknown_supplier"})
            continue

        nk = _natural_key(rec)

        # Rule 3: duplicate natural key within batch (earlier non-rejected)
        if nk in seen_natural_keys:
            rejects.append({"index": idx, "reason": "duplicate_key"})
            continue

        seen_natural_keys[nk] = idx

        # Accepted — determine outcome
        if nk in existing_products:
            existing = existing_products[nk]
            same_name = rec["name"] == existing["name"]
            same_price = rec["price_cents"] == existing["price_cents"]
            same_supplier = rec["supplier_code"] == existing["supplier_code"]

            if same_name and same_price and same_supplier:
                unchanged_count += 1
            else:
                to_update.append(rec)
        else:
            to_insert.append(rec)

    # ── Statement 2: write to database ──────────────────────────────────
    inserted = 0
    updated = 0

    if to_insert or to_update:
        inserts_json = json.dumps(to_insert)
        updates_json = json.dumps(to_update)
        now_iso = now.isoformat()

        result2_json = await client.query_json(
            """\
            with
              inserts := <json>$inserts,
              updates := <json>$updates,
              now := <datetime>$now,
              ins := (
                for item in json_array_unpack(inserts) union (
                  with
                    sup := (select Supplier filter .code = <str>item['supplier_code']),
                  insert Product {
                    source_system := <str>item['source_system'],
                    external_id := <str>item['external_id'],
                    name := <str>item['name'],
                    price_cents := <int64>item['price_cents'],
                    revision := 1,
                    updated_at := now,
                    supplier := assert_single(sup),
                  }
                )
              ),
              upd := (
                for item in json_array_unpack(updates) union (
                  with
                    src := <str>item['source_system'],
                    ext := <str>item['external_id'],
                    new_name := <str>item['name'],
                    new_price := <int64>item['price_cents'],
                    new_sup_code := <str>item['supplier_code'],
                    new_sup := (select Supplier filter .code = new_sup_code),
                  update Product
                  filter .source_system = src and .external_id = ext
                  set {
                    name := new_name,
                    price_cents := new_price,
                    supplier := assert_single(new_sup),
                    revision := .revision + 1,
                    updated_at := now,
                  }
                )
              ),
            select {
              inserted := count(ins),
              updated := count(upd),
            }
            """,
            inserts=inserts_json,
            updates=updates_json,
            now=now_iso,
        )

        result2 = json.loads(result2_json)
        inserted = result2["inserted"]
        updated = result2["updated"]

    return {
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged_count,
        "rejected": len(rejects),
        "rejects": rejects,
    }
