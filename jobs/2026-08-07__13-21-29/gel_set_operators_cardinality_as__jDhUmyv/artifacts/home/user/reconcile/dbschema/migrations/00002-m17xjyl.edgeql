CREATE MIGRATION m17xjylwgeqhdueubsitrgmwpuqibh6fc47y3sbxxqqzhi7fj67naa
    ONTO m1vlbzctsbmqmvud635ahit45rza5adnqs7lpp5k62b2w3anftpu4a
{
  ALTER TYPE default::Sku {
      CREATE SINGLE LINK sole_warehouse := (WITH
          my_counts := 
              .<sku[IS default::ShelfCount]
      SELECT
          DISTINCT (my_counts.warehouse) FILTER
              (std::count(DISTINCT (my_counts.warehouse)) = 1)
      LIMIT
          1
      );
  };
  ALTER TYPE default::Warehouse {
      CREATE MULTI LINK counted_skus := (SELECT
          DISTINCT (((SELECT
              .<warehouse[IS default::ShelfCount]
          )).sku)
      );
      CREATE MULTI LINK ledger_skus := (SELECT
          DISTINCT (((SELECT
              .<warehouse[IS default::LedgerLine]
          )).sku)
      );
      CREATE MULTI LINK unreconciled_skus := (((.counted_skus UNION .ledger_skus) EXCEPT (.counted_skus INTERSECT .ledger_skus)));
      CREATE REQUIRED SINGLE PROPERTY ledger_units := (WITH
          l := 
              (SELECT
                  .<warehouse[IS default::LedgerLine]
              )
      SELECT
          std::sum(l.quantity)
      );
      CREATE REQUIRED SINGLE PROPERTY shelf_units := (WITH
          s := 
              (SELECT
                  .<warehouse[IS default::ShelfCount]
              )
      SELECT
          std::sum(s.quantity)
      );
      CREATE REQUIRED SINGLE PROPERTY is_balanced := ((NOT (EXISTS (.unreconciled_skus)) AND (.shelf_units = .ledger_units)));
  };
};
