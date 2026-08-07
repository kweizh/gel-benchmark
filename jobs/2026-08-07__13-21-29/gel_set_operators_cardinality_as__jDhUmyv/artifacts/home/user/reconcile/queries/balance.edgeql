select Warehouse {
    code,
    shelf_units,
    ledger_units,
    counted_codes := (
        with c := .counted_skus.code,
        select array_agg(c order by c)
    ),
    ledger_codes := (
        with c := .ledger_skus.code,
        select array_agg(c order by c)
    ),
    unreconciled_codes := (
        with c := .unreconciled_skus.code,
        select array_agg(c order by c)
    ),
    is_balanced,
}
order by .code
