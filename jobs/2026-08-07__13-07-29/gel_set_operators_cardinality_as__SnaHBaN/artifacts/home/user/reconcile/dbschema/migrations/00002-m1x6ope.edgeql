CREATE MIGRATION m1x6opevcrmvzubc5bi2iaxsqkfr7ajqtstwiscevslb2hzq64pw3q
    ONTO m1vlbzctsbmqmvud635ahit45rza5adnqs7lpp5k62b2w3anftpu4a
{
  ALTER TYPE default::Warehouse {
      CREATE MULTI LINK counted_skus := (DISTINCT (.<warehouse[IS default::ShelfCount].sku));
      CREATE MULTI LINK ledger_skus := (DISTINCT (.<warehouse[IS default::LedgerLine].sku));
      CREATE MULTI LINK unreconciled_skus := (DISTINCT (((.counted_skus EXCEPT .ledger_skus) UNION (.ledger_skus EXCEPT .counted_skus))));
      CREATE REQUIRED SINGLE PROPERTY ledger_units := (std::sum(.<warehouse[IS default::LedgerLine].quantity));
      CREATE REQUIRED SINGLE PROPERTY shelf_units := (std::sum(.<warehouse[IS default::ShelfCount].quantity));
      CREATE REQUIRED SINGLE PROPERTY is_balanced := ((NOT (EXISTS (.unreconciled_skus)) AND (.shelf_units = .ledger_units)));
  };
};
