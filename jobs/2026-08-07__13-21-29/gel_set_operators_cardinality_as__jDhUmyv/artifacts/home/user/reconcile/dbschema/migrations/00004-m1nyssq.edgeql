CREATE MIGRATION m1nyssqmwh5s7kszht4g4hyw2ciofz5fleotzljz7sp644rqukzuda
    ONTO m1t5jvngak5xittrcj22s5sdkpgmyvjvvgihbzdftv2t2prjv27u4q
{
  ALTER TYPE default::Warehouse {
      ALTER LINK unreconciled_skus {
          USING (SELECT
              (DISTINCT ((.counted_skus UNION .ledger_skus)) EXCEPT (.counted_skus INTERSECT .ledger_skus))
          );
      };
  };
};
