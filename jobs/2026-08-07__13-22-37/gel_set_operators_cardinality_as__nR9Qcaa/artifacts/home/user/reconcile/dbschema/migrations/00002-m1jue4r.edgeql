CREATE MIGRATION m1jue4robq4isa3xa7ek5jgetcm4p2xhsnc5ssemdnmluoa5hkvqsq
    ONTO m1vlbzctsbmqmvud635ahit45rza5adnqs7lpp5k62b2w3anftpu4a
{
  ALTER TYPE default::Sku {
      CREATE SINGLE LINK sole_warehouse := (((SELECT
          .<sku[IS default::ShelfCount].warehouse 
      LIMIT
          1
      ) IF (std::count(.<sku[IS default::ShelfCount].warehouse) = 1) ELSE {}));
  };
  ALTER TYPE default::Warehouse {
      CREATE MULTI LINK counted_skus := (.<warehouse[IS default::ShelfCount].sku);
      CREATE MULTI LINK ledger_skus := (.<warehouse[IS default::LedgerLine].sku);
      CREATE MULTI LINK unreconciled_skus := (((.counted_skus EXCEPT .ledger_skus) UNION (.ledger_skus EXCEPT .counted_skus)));
      CREATE REQUIRED PROPERTY ledger_units := ((std::sum(.<warehouse[IS default::LedgerLine].quantity) ?? 0));
      CREATE REQUIRED PROPERTY shelf_units := ((std::sum(.<warehouse[IS default::ShelfCount].quantity) ?? 0));
      CREATE REQUIRED PROPERTY is_balanced := ((NOT (EXISTS (.unreconciled_skus)) AND (.shelf_units = .ledger_units)));
  };
};
