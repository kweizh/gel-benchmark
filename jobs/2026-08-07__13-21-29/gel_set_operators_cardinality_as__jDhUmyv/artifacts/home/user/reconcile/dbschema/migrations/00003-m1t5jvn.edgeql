CREATE MIGRATION m1t5jvngak5xittrcj22s5sdkpgmyvjvvgihbzdftv2t2prjv27u4q
    ONTO m17xjylwgeqhdueubsitrgmwpuqibh6fc47y3sbxxqqzhi7fj67naa
{
  ALTER TYPE default::Warehouse {
      ALTER LINK unreconciled_skus {
          USING (SELECT
              DISTINCT (((.counted_skus UNION .ledger_skus) EXCEPT (.counted_skus INTERSECT .ledger_skus)))
          );
      };
  };
};
