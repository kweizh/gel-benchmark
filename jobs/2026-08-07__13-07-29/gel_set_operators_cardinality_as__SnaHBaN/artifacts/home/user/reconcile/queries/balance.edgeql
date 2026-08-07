select {
  report := "balance",
  warehouses := array_agg((
    select Warehouse {
      code,
      shelf_units,
      ledger_units,
      counted_codes := (with cs := .counted_skus select cs.code order by cs.code),
      ledger_codes := (with ls := .ledger_skus select ls.code order by ls.code),
      both_codes := (with x := (.counted_skus intersect .ledger_skus) select x.code order by x.code),
      shelf_only_codes := (with x := (.counted_skus except .ledger_skus) select x.code order by x.code),
      ledger_only_codes := (with x := (.ledger_skus except .counted_skus) select x.code order by x.code),
      all_codes := (with x := distinct (.counted_skus union .ledger_skus) select x.code order by x.code),
      unreconciled_codes := (with u := .unreconciled_skus select u.code order by u.code),
      is_balanced,
    }
    order by .code
  )),
};
