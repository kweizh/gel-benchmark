select <json>(select array_agg((select (
    code := Warehouse.code,
    shelf_units := Warehouse.shelf_units,
    ledger_units := Warehouse.ledger_units,
    counted_skus := array_agg((select x := (select distinct Warehouse.counted_skus.code) order by x)),
    ledger_skus := array_agg((select x := (select distinct Warehouse.ledger_skus.code) order by x)),
    both := array_agg((select x := (select distinct (Warehouse.counted_skus intersect Warehouse.ledger_skus).code) order by x)),
    shelf_only := array_agg((select x := (select distinct (Warehouse.counted_skus except Warehouse.ledger_skus).code) order by x)),
    ledger_only := array_agg((select x := (select distinct (Warehouse.ledger_skus except Warehouse.counted_skus).code) order by x)),
    all_skus := array_agg((select x := (select distinct (Warehouse.counted_skus union Warehouse.ledger_skus).code) order by x)),
    unreconciled_skus := array_agg((select x := (select distinct Warehouse.unreconciled_skus.code) order by x)),
    is_balanced := Warehouse.is_balanced
) order by .code)))
