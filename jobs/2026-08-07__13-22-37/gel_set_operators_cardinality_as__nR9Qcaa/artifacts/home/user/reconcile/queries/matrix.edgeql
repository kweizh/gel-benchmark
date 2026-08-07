select {
    report := 'matrix',
    cells := (
        select (
            for w in Warehouse union (
                for s in Sku union (
                    with shelf := (sum((select w.<warehouse[is ShelfCount] filter .sku = s).quantity)) ?? 0,
                         ledger := (sum((select w.<warehouse[is LedgerLine] filter .sku = s).quantity)) ?? 0,
                    select {
                        warehouse := w.code,
                        sku := s.code,
                        shelf := shelf,
                        ledger := ledger,
                        delta := shelf - ledger,
                    }
                )
            )
        ) order by .warehouse then .sku
    )
};
