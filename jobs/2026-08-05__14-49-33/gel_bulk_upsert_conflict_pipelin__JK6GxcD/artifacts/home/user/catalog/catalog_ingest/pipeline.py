import json
from datetime import datetime, timezone

def is_well_formed(record):
    if not isinstance(record, dict):
        return False
    
    # Check source_system
    if "source_system" not in record:
        return False
    ss = record["source_system"]
    if not isinstance(ss, str) or ss == "":
        return False
        
    # Check external_id
    if "external_id" not in record:
        return False
    ext_id = record["external_id"]
    if not isinstance(ext_id, str) or ext_id == "":
        return False
        
    # Check name
    if "name" not in record:
        return False
    name = record["name"]
    if not isinstance(name, str) or name == "":
        return False
        
    # Check price_cents
    if "price_cents" not in record:
        return False
    pc = record["price_cents"]
    if isinstance(pc, bool) or not isinstance(pc, int) or pc < 0:
        return False
        
    # Check supplier_code
    if "supplier_code" not in record:
        return False
    sc = record["supplier_code"]
    if not isinstance(sc, str) or sc == "":
        return False
        
    return True

async def ingest_batch(client, records):
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    
    supplier_codes_to_query = set()
    product_keys_to_query = []
    
    for record in records:
        if is_well_formed(record):
            supplier_codes_to_query.add(record["supplier_code"])
            product_keys_to_query.append((record["source_system"], record["external_id"]))
            
    rejects = []
    inserted_count = 0
    updated_count = 0
    unchanged_count = 0
    
    # Run everything in a single transaction to guarantee atomicity
    async for tx in client.transaction():
        async with tx:
            existing_suppliers = set()
            existing_products_map = {}
            
            if supplier_codes_to_query:
                res = await tx.query_single("""
                    select {
                      suppliers := (select (select Supplier filter .code in array_unpack(<array<str>>$supplier_codes)).code),
                      products := (
                        select Product {
                          source_system,
                          external_id,
                          name,
                          price_cents,
                          supplier_code := .supplier.code,
                          revision
                        } filter (.source_system, .external_id) in array_unpack(<array<tuple<str, str>>>$product_keys)
                      )
                    }
                """, supplier_codes=list(supplier_codes_to_query), product_keys=product_keys_to_query)
                
                existing_suppliers = set(res.suppliers)
                existing_products_map = {
                    (p.source_system, p.external_id): p
                    for p in res.products
                }
            
            local_rejects = []
            seen_keys = set()
            accepted_records = []
            
            for idx, record in enumerate(records):
                if not is_well_formed(record):
                    local_rejects.append({"index": idx, "reason": "invalid_record"})
                    continue
                
                sc = record["supplier_code"]
                if sc not in existing_suppliers:
                    local_rejects.append({"index": idx, "reason": "unknown_supplier"})
                    continue
                
                key = (record["source_system"], record["external_id"])
                if key in seen_keys:
                    local_rejects.append({"index": idx, "reason": "duplicate_key"})
                    continue
                
                seen_keys.add(key)
                accepted_records.append(record)
                
            inserts = []
            updates = []
            local_inserted_count = 0
            local_updated_count = 0
            local_unchanged_count = 0
            
            for record in accepted_records:
                key = (record["source_system"], record["external_id"])
                if key not in existing_products_map:
                    local_inserted_count += 1
                    inserts.append({
                        "source_system": record["source_system"],
                        "external_id": record["external_id"],
                        "name": record["name"],
                        "price_cents": record["price_cents"],
                        "revision": 1,
                        "updated_at": now_iso,
                        "supplier_code": record["supplier_code"]
                    })
                else:
                    db_prod = existing_products_map[key]
                    if (record["name"] == db_prod.name and
                        record["price_cents"] == db_prod.price_cents and
                        record["supplier_code"] == db_prod.supplier_code):
                        local_unchanged_count += 1
                    else:
                        local_updated_count += 1
                        updates.append({
                            "source_system": record["source_system"],
                            "external_id": record["external_id"],
                            "name": record["name"],
                            "price_cents": record["price_cents"],
                            "revision": db_prod.revision + 1,
                            "updated_at": now_iso,
                            "supplier_code": record["supplier_code"]
                        })
                        
            if inserts or updates:
                await tx.query_single("""
                    with
                      ins_data := json_array_unpack(to_json(<str>$inserts)),
                      upd_data := json_array_unpack(to_json(<str>$updates)),
                      
                      inserted := (
                        for item in ins_data union (
                          insert Product {
                            source_system := <str>item["source_system"],
                            external_id := <str>item["external_id"],
                            name := <str>item["name"],
                            price_cents := <int64>item["price_cents"],
                            revision := <int64>item["revision"],
                            updated_at := <datetime><str>item["updated_at"],
                            supplier := (select Supplier filter .code = <str>item["supplier_code"])
                          }
                        )
                      ),
                      
                      updated := (
                        for item in upd_data union (
                          update Product
                          filter .source_system = <str>item["source_system"] and .external_id = <str>item["external_id"]
                          set {
                            name := <str>item["name"],
                            price_cents := <int64>item["price_cents"],
                            revision := <int64>item["revision"],
                            updated_at := <datetime><str>item["updated_at"],
                            supplier := (select Supplier filter .code = <str>item["supplier_code"])
                          }
                        )
                      )
                    select {
                      inserted_count := count(inserted),
                      updated_count := count(updated)
                    }
                """, inserts=json.dumps(inserts), updates=json.dumps(updates))
                
            rejects = local_rejects
            inserted_count = local_inserted_count
            updated_count = local_updated_count
            unchanged_count = local_unchanged_count
            
    return {
        "inserted": inserted_count,
        "updated": updated_count,
        "unchanged": unchanged_count,
        "rejected": len(rejects),
        "rejects": rejects
    }
