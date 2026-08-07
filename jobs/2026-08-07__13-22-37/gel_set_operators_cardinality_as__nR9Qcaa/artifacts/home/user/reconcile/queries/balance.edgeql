select {
    report := 'balance',
    warehouses := (
        select Warehouse {
            code,
            shelf_units,
            ledger_units,
            counted_skus: { code } order by .code,
            ledger_skus: { code } order by .code,
            both := (select (.counted_skus intersect .ledger_skus) order by .code).code,
            shelf_only := (select (.counted_skus except .ledger_skus) order by .code).code,
            ledger_only := (select (.ledger_skus except .counted_skus) order by .code).code,
            all_skus := (select distinct (.counted_skus union .ledger_skus) order by .code).code,
            unreconciled_skus: { code } order by .code,
            is_balanced,
        } order by .code
    )
};
