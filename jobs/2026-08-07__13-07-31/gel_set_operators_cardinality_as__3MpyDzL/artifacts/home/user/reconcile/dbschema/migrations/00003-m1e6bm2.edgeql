CREATE MIGRATION m1e6bm2eniga233g7d55xxehien4jwspidjebjdd7zjbohe6x4bvtq
    ONTO m1vbzbwaukmrdesadinyfe6cqkegpc4j56hy6xf5mkgpbmi4qxzgqa
{
  ALTER TYPE default::Sku {
      ALTER LINK sole_warehouse {
          USING (std::assert_single((SELECT
              (.<sku[IS default::ShelfCount].warehouse IF (std::count(.<sku[IS default::ShelfCount].warehouse) = 1) ELSE <default::Warehouse>{})
          )));
          SET SINGLE USING (std::assert_single(.sole_warehouse));
      };
  };
};
