CREATE MIGRATION m16agewhul3uaiqc5cvhpemeemueodvtbh3tr3pkrrrgeqejsofaaq
    ONTO m17o7esggsyxdixekq3s6bc75jzhjz64tatomrth5nblbqootqnx5a
{
  CREATE TYPE default::AuditBatch {
      CREATE REQUIRED PROPERTY kind: std::str {
          CREATE CONSTRAINT std::one_of('insert', 'update', 'delete');
      };
      CREATE REQUIRED PROPERTY row_count: std::int64;
      CREATE REQUIRED PROPERTY recorded_at: std::datetime {
          SET default := (std::datetime_of_statement());
      };
  };
  ALTER TYPE default::Product {
      CREATE TRIGGER batch_delete_stats
          AFTER DELETE 
          FOR ALL DO (INSERT
              default::AuditBatch
              {
                  kind := 'delete',
                  row_count := std::count(__old__)
              });
      CREATE TRIGGER batch_insert_stats
          AFTER INSERT 
          FOR ALL DO (INSERT
              default::AuditBatch
              {
                  kind := 'insert',
                  row_count := std::count(__new__)
              });
      CREATE TRIGGER batch_update_stats
          AFTER UPDATE 
          FOR ALL DO (INSERT
              default::AuditBatch
              {
                  kind := 'update',
                  row_count := std::count(__new__)
              });
  };
  CREATE TYPE default::AuditEvent {
      CREATE REQUIRED PROPERTY action: std::str {
          CREATE CONSTRAINT std::one_of('insert', 'update', 'delete');
      };
      CREATE PROPERTY new_price_cents: std::int64;
      CREATE PROPERTY old_price_cents: std::int64;
      CREATE REQUIRED PROPERTY sku: std::str;
      CREATE REQUIRED PROPERTY summary: std::str {
          CREATE REWRITE
              INSERT 
              USING ((IF (__subject__.action = 'insert') THEN ((('INSERT ' ++ __subject__.sku) ++ ' price=') ++ <std::str>__subject__.new_price_cents) ELSE (IF (__subject__.action = 'update') THEN ((((('UPDATE ' ++ __subject__.sku) ++ ' price=') ++ <std::str>__subject__.old_price_cents) ++ '->') ++ <std::str>__subject__.new_price_cents) ELSE ((('DELETE ' ++ __subject__.sku) ++ ' price=') ++ <std::str>__subject__.old_price_cents))));
      };
      CREATE REQUIRED PROPERTY recorded_at: std::datetime {
          SET default := (std::datetime_of_statement());
      };
      CREATE TRIGGER forbid_mutation
          AFTER UPDATE, DELETE 
          FOR EACH DO (std::assert(false, message := 'AuditEvent is append-only'));
  };
  ALTER TYPE default::Product {
      CREATE TRIGGER log_price_update
          AFTER UPDATE 
          FOR EACH 
              WHEN ((__old__.price_cents != __new__.price_cents))
          DO (INSERT
              default::AuditEvent
              {
                  action := 'update',
                  sku := __new__.sku,
                  old_price_cents := __old__.price_cents,
                  new_price_cents := __new__.price_cents
              });
      CREATE TRIGGER log_product_delete
          AFTER DELETE 
          FOR EACH DO (INSERT
              default::AuditEvent
              {
                  action := 'delete',
                  sku := __old__.sku,
                  old_price_cents := __old__.price_cents
              });
      CREATE TRIGGER log_product_insert
          AFTER INSERT 
          FOR EACH DO (INSERT
              default::AuditEvent
              {
                  action := 'insert',
                  sku := __new__.sku,
                  new_price_cents := __new__.price_cents
              });
      CREATE REQUIRED PROPERTY price_history: array<std::int64> {
          SET default := (<array<std::int64>>[]);
          CREATE REWRITE
              INSERT 
              USING ([__subject__.price_cents]);
          CREATE REWRITE
              UPDATE 
              USING ((IF (__old__.price_cents != __subject__.price_cents) THEN (__old__.price_history ++ [__subject__.price_cents]) ELSE __old__.price_history));
      };
      CREATE REQUIRED PROPERTY revision: std::int64 {
          SET default := 1;
          CREATE REWRITE
              UPDATE 
              USING ((__old__.revision + 1));
      };
  };
};
