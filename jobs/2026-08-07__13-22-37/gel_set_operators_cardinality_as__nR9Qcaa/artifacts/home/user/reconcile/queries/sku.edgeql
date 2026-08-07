select {
    report := 'sku',
    skus := (
        select Sku {
            code,
            sole_warehouse: { code },
            shelf_warehouses := (select .<sku[is ShelfCount].warehouse order by .code).code,
            shelf_units := sum(.<sku[is ShelfCount].quantity) ?? 0,
            ledger_units := sum(.<sku[is LedgerLine].quantity) ?? 0,
        } order by .code
    )
};
