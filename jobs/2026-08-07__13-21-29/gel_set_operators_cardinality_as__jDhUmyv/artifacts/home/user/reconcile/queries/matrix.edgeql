with
    filter_code := <str>$filter_code,
    warehouses := (select Warehouse order by .code),
    skus := (
        select Sku
        filter (filter_code = '' or .code = filter_code)
        order by .code
    ),
    cells := (
        for w in warehouses union (
            for s in skus union (
                with
                    shelf_qty := sum((select ShelfCount filter .warehouse.code = w.code and .sku.code = s.code).quantity),
                    ledger_qty := sum((select LedgerLine filter .warehouse.code = w.code and .sku.code = s.code).quantity),
                select {
                    warehouse := w.code,
                    sku := s.code,
                    shelf := shelf_qty,
                    ledger := ledger_qty,
                    delta := shelf_qty - ledger_qty,
                }
            )
        )
    ),
select {
    cells := array_agg(cells { warehouse, sku, shelf, ledger, delta } order by cells.warehouse then cells.sku),
    total_delta := sum(cells.delta),
}
