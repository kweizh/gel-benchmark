with
    code := <str>$code,
    sku := (select Sku filter .code = code),
select {
    ex := exists sku,
    sole_warehouse_code := sku.sole_warehouse.code,
    shelf_warehouses := array_agg(distinct (
        with wh := sku.<sku[is ShelfCount].warehouse.code,
        select wh order by wh
    )),
    shelf_units := sum(sku.<sku[is ShelfCount].quantity),
    ledger_units := sum(sku.<sku[is LedgerLine].quantity),
}
