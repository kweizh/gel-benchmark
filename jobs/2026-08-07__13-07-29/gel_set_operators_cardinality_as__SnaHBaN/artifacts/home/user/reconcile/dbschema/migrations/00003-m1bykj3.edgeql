CREATE MIGRATION m1bykj3iaykjazyicngz3rhnad54k6m3v6b3s6f4phcnuvrbs2hhdq
    ONTO m1x6opevcrmvzubc5bi2iaxsqkfr7ajqtstwiscevslb2hzq64pw3q
{
  ALTER TYPE default::Sku {
      CREATE SINGLE LINK sole_warehouse := (std::assert_single((DISTINCT (.<sku[IS default::ShelfCount].warehouse) IF (std::count(DISTINCT (.<sku[IS default::ShelfCount].warehouse)) = 1) ELSE <default::Warehouse>{})));
  };
};
