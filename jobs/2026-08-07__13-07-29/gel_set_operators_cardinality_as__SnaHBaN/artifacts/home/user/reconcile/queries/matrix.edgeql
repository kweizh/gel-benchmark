with pairs := (
  for w in (select Warehouse) union (
    for s in (select Sku filter __SKU_FILTER__) union (
      select {
        warehouse := w.code,
        sku := s.code,
        shelf := sum((select ShelfCount filter .warehouse = w and .sku = s).quantity),
        ledger := sum((select LedgerLine filter .warehouse = w and .sku = s).quantity),
        delta := sum((select ShelfCount filter .warehouse = w and .sku = s).quantity) - sum((select LedgerLine filter .warehouse = w and .sku = s).quantity),
      }
    )
  )
)
select {
  report := "matrix",
  cells := array_agg((select pairs {warehouse, sku, shelf, ledger, delta} order by .warehouse then .sku)),
  total_delta := sum((select pairs {delta}).delta),
};
