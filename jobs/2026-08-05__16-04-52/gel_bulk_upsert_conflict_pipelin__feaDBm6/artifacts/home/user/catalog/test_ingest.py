import asyncio
import json
import gel
from catalog_ingest.pipeline import ingest_batch


async def main():
    client = gel.create_async_client()

    # Test 1: Basic insert
    print("=== Test 1: Basic insert ===")
    records = [
        {
            "source_system": "ERP1",
            "external_id": "P001",
            "name": "Widget A",
            "price_cents": 1000,
            "supplier_code": "SUP1",
        },
        {
            "source_system": "ERP1",
            "external_id": "P002",
            "name": "Widget B",
            "price_cents": 2000,
            "supplier_code": "SUP1",
        },
    ]
    result = await ingest_batch(client, records)
    print(result)
    assert result["inserted"] == 2
    assert result["updated"] == 0
    assert result["unchanged"] == 0
    assert result["rejected"] == 0
    assert result["rejects"] == []

    # Test 2: Idempotent replay
    print("\n=== Test 2: Idempotent replay ===")
    result = await ingest_batch(client, records)
    print(result)
    assert result["inserted"] == 0
    assert result["updated"] == 0
    assert result["unchanged"] == 2
    assert result["rejected"] == 0

    # Test 3: Update
    print("\n=== Test 3: Update ===")
    records2 = [
        {
            "source_system": "ERP1",
            "external_id": "P001",
            "name": "Widget A v2",
            "price_cents": 1500,
            "supplier_code": "SUP1",
        },
    ]
    result = await ingest_batch(client, records2)
    print(result)
    assert result["inserted"] == 0
    assert result["updated"] == 1
    assert result["unchanged"] == 0
    assert result["rejected"] == 0

    # Test 4: Invalid records
    print("\n=== Test 4: Invalid records ===")
    records3 = [
        {"source_system": "", "external_id": "X", "name": "X", "price_cents": 0, "supplier_code": "SUP1"},
        {"source_system": "X", "external_id": "", "name": "X", "price_cents": 0, "supplier_code": "SUP1"},
        {"source_system": "X", "external_id": "X", "name": "", "price_cents": 0, "supplier_code": "SUP1"},
        {"source_system": "X", "external_id": "X", "name": "X", "price_cents": -1, "supplier_code": "SUP1"},
        {"source_system": "X", "external_id": "X", "name": "X", "price_cents": True, "supplier_code": "SUP1"},
        {"source_system": "X", "external_id": "X", "name": "X", "price_cents": 0, "supplier_code": ""},
        "not_a_dict",
    ]
    result = await ingest_batch(client, records3)
    print(result)
    assert result["rejected"] == 7
    assert all(r["reason"] == "invalid_record" for r in result["rejects"])

    # Test 5: Unknown supplier
    print("\n=== Test 5: Unknown supplier ===")
    records4 = [
        {
            "source_system": "ERP1",
            "external_id": "P999",
            "name": "Ghost Product",
            "price_cents": 500,
            "supplier_code": "NOEXIST",
        },
    ]
    result = await ingest_batch(client, records4)
    print(result)
    assert result["rejected"] == 1
    assert result["rejects"][0]["reason"] == "unknown_supplier"

    # Test 6: Duplicate key within batch
    print("\n=== Test 6: Duplicate key within batch ===")
    records5 = [
        {
            "source_system": "ERP2",
            "external_id": "DUP1",
            "name": "First",
            "price_cents": 100,
            "supplier_code": "SUP1",
        },
        {
            "source_system": "ERP2",
            "external_id": "DUP1",
            "name": "Second",
            "price_cents": 200,
            "supplier_code": "SUP1",
        },
    ]
    result = await ingest_batch(client, records5)
    print(result)
    assert result["inserted"] == 1
    assert result["rejected"] == 1
    assert result["rejects"][0]["reason"] == "duplicate_key"
    assert result["rejects"][0]["index"] == 1

    # Test 7: Mixed batch
    print("\n=== Test 7: Mixed batch ===")
    records6 = [
        {"source_system": "ERR", "external_id": "E1", "name": "Bad", "price_cents": -5, "supplier_code": "SUP1"},
        {
            "source_system": "ERP3",
            "external_id": "M1",
            "name": "Mixed 1",
            "price_cents": 300,
            "supplier_code": "SUP1",
        },
        {
            "source_system": "ERP3",
            "external_id": "M2",
            "name": "Mixed 2",
            "price_cents": 400,
            "supplier_code": "NOEXIST",
        },
        {
            "source_system": "ERP3",
            "external_id": "M1",
            "name": "Mixed 1 Dup",
            "price_cents": 500,
            "supplier_code": "SUP1",
        },
        {
            "source_system": "ERP1",
            "external_id": "P001",
            "name": "Widget A v2",
            "price_cents": 1500,
            "supplier_code": "SUP1",
        },
    ]
    result = await ingest_batch(client, records6)
    print(result)
    assert result["inserted"] == 1   # M1
    assert result["updated"] == 0    # P001 unchanged
    assert result["unchanged"] == 1  # P001
    assert result["rejected"] == 3   # E1 (invalid), M2 (unknown supplier), M1 dup
    rejects = {r["index"]: r["reason"] for r in result["rejects"]}
    assert rejects[0] == "invalid_record"
    assert rejects[2] == "unknown_supplier"
    assert rejects[3] == "duplicate_key"

    # Test 8: Verify revision tracking
    print("\n=== Test 8: Verify revision tracking ===")
    rows = await client.query_json(
        "select Product { source_system, external_id, name, price_cents, revision }"
        " filter .source_system = 'ERP1' and .external_id = 'P001'"
    )
    print(rows)
    data = json.loads(rows)
    assert data[0]["revision"] == 2
    assert data[0]["name"] == "Widget A v2"
    assert data[0]["price_cents"] == 1500

    # Test 9: Total sum check
    print("\n=== Test 9: Total sum check ===")
    result = await ingest_batch(client, records6)
    total = result["inserted"] + result["updated"] + result["unchanged"] + result["rejected"]
    assert total == len(records6), f"Expected {len(records6)}, got {total}"

    print("\n✅ All tests passed!")
    await client.close()


asyncio.run(main())
