with s := (select Sku filter .code = '__CODE__')
select {
  report := "sku",
  code := '__CODE__',
  `exists` := exists s,
  sole_warehouse := s.sole_warehouse.code,
  shelf_warehouses := array_agg((with ws := distinct s.<sku[is ShelfCount].warehouse select ws.code order by ws.code)),
  shelf_units := sum(s.<sku[is ShelfCount].quantity),
  ledger_units := sum(s.<sku[is LedgerLine].quantity),
};
