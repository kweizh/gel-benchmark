with grp := (
  group ShelfCount by .warehouse, .sku
)
select {
  report := "duplicates",
  pairs := array_agg((
    select grp {
      warehouse := .key.warehouse.code,
      sku := .key.sku.code,
      rows := count(.elements),
      quantities := (with q := .elements select array_agg(q.quantity order by q.quantity)),
    }
    filter count(.elements) > 1
    order by .key.warehouse.code then .key.sku.code
  )),
};
