with
    groups := (
        group ShelfCount
        using warehouse := .warehouse.code, sku := .sku.code
        by warehouse, sku
    ),
    dupes := (
        select groups
        filter count(.elements) > 1
    ),
select {
    pairs := array_agg(
        (select dupes {
            warehouse := .key.warehouse,
            sku := .key.sku,
            rows := count(.elements),
            quantities := (
                with q := .elements.quantity,
                select array_agg(q order by q)
            ),
        } order by .warehouse then .sku)
    ),
    clean := count(dupes) = 0,
}
