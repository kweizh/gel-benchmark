select <json>(
    with
        pairs := (select (wh := ShelfCount.warehouse, sku := ShelfCount.sku)),
        distinct_pairs := (select distinct pairs),
        duplicated_pairs := (select distinct_pairs filter count((select ShelfCount filter .warehouse = distinct_pairs.wh and .sku = distinct_pairs.sku)) > 1),
        pair_list := array_agg((
            select (
                warehouse := duplicated_pairs.wh.code,
                sku := duplicated_pairs.sku.code,
                rows := count((select ShelfCount filter .warehouse = duplicated_pairs.wh and .sku = duplicated_pairs.sku)),
                quantities := array_agg((select x := (select ShelfCount filter .warehouse = duplicated_pairs.wh and .sku = duplicated_pairs.sku).quantity order by x))
            ) order by .warehouse then .sku
        ))
    select (
        report := "duplicates",
        clean := count(duplicated_pairs) = 0,
        pairs := pair_list
    )
);
