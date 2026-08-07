select <json>(
    with
        # Determine whether we should filter by SKU
        do_filter := global filter_by_sku ?? false,
        
        # Sku set to use:
        skus := (
            select Sku
            filter (not do_filter) or ((.code = global current_sku_code) ?? false)
        ),
        
        # Construct cells
        cross_product := (
            select (
                wh := Warehouse,
                sku := skus,
                shelf := sum((select ShelfCount filter .warehouse = Warehouse and .sku = skus).quantity),
                ledger := sum((select LedgerLine filter .warehouse = Warehouse and .sku = skus).quantity)
            )
        ),
        
        cell_list := array_agg((
            select (
                warehouse := cross_product.wh.code,
                sku := cross_product.sku.code,
                shelf := cross_product.shelf,
                ledger := cross_product.ledger,
                delta := cross_product.shelf - cross_product.ledger
            ) order by .warehouse then .sku
        )),
        
        # Calculate total delta
        tot_delta := sum(cross_product.shelf - cross_product.ledger)
        
    select (
        report := "matrix",
        cells := cell_list,
        total_delta := tot_delta
    )
);
