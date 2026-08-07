select {
    report := 'duplicates',
    pairs := (
        select (
            for w in Warehouse union (
                for s in w.<warehouse[is ShelfCount].sku union (
                    with rows := count(w.<warehouse[is ShelfCount] filter .sku = s),
                    select {
                        warehouse := w.code,
                        sku := s.code,
                        rows := rows,
                        quantities := (select w.<warehouse[is ShelfCount] filter .sku = s order by .quantity).quantity,
                    }
                    filter rows > 1
                )
            )
        ) order by .warehouse then .sku
    )
};
