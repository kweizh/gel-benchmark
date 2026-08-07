select <json>(
    with
        code_param := global current_sku_code,
        sku := (select Sku filter .code = code_param),
        is_present := exists sku,
        sole_wh := <json>sku.sole_warehouse.code ?? to_json('null'),
        shelf_whs := array_agg((select x := (select distinct (select ShelfCount filter .sku = sku).warehouse.code) order by x)),
        shelf_u := sum((select ShelfCount filter .sku = sku).quantity),
        ledger_u := sum((select LedgerLine filter .sku = sku).quantity)
    select (
        report := "sku",
        code := code_param,
        `exists` := is_present,
        sole_warehouse := sole_wh,
        shelf_warehouses := shelf_whs,
        shelf_units := shelf_u,
        ledger_units := ledger_u
    )
);
