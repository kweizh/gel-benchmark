CREATE MIGRATION m1s4iae2dmm2dqfcdjxxysnpke6rlbfpvwyltfwd3i25skijp6hpwq
    ONTO m1p7ayxtinlmvdccctfyx7qvmskh6mhxd2uzgl3f3y2cumsi6aq3ra
{
  CREATE TYPE default::AuditEntry {
      CREATE REQUIRED PROPERTY action: std::str;
      CREATE REQUIRED PROPERTY at: std::datetime;
      CREATE REQUIRED PROPERTY entity_id: std::uuid;
      CREATE REQUIRED PROPERTY entity_type: std::str;
      CREATE REQUIRED PROPERTY snapshot: std::json;
      CREATE REQUIRED PROPERTY version: std::int64;
      CREATE TRIGGER immutable
          AFTER UPDATE, DELETE 
          FOR EACH DO (std::assert(false, message := 'AuditEntry is immutable'));
  };
  ALTER TYPE default::Comment {
      CREATE REQUIRED PROPERTY document_slug: std::str {
          SET REQUIRED USING (.document.slug);
      };
      CREATE REQUIRED PROPERTY version: std::int64 {
          SET default := 1;
          CREATE REWRITE
              INSERT 
              USING (1);
          CREATE REWRITE
              UPDATE 
              USING ((__old__.version + 1));
      };
      CREATE TRIGGER audit_delete
          AFTER DELETE 
          FOR EACH DO (INSERT
              default::AuditEntry
              {
                  action := 'delete',
                  entity_type := 'Comment',
                  entity_id := __old__.id,
                  version := __old__.version,
                  at := std::datetime_of_statement(),
                  snapshot := <std::json>{
                      body := __old__.body,
                      document_slug := __old__.document_slug,
                      version := __old__.version
                  }
              });
      CREATE REQUIRED PROPERTY created_at: std::datetime {
          SET default := (std::datetime_current());
          CREATE REWRITE
              INSERT 
              USING (std::datetime_of_statement());
          CREATE REWRITE
              UPDATE 
              USING (__old__.created_at);
      };
      CREATE TRIGGER audit_insert
          AFTER INSERT 
          FOR EACH DO (INSERT
              default::AuditEntry
              {
                  action := 'insert',
                  entity_type := 'Comment',
                  entity_id := __new__.id,
                  version := __new__.version,
                  at := __new__.created_at,
                  snapshot := <std::json>{
                      body := __new__.body,
                      document_slug := __new__.document_slug,
                      version := __new__.version
                  }
              });
      CREATE REQUIRED PROPERTY modified_at: std::datetime {
          SET default := (std::datetime_current());
          CREATE REWRITE
              INSERT 
              USING (std::datetime_of_statement());
          CREATE REWRITE
              UPDATE 
              USING (std::datetime_of_statement());
      };
      CREATE TRIGGER audit_update
          AFTER UPDATE 
          FOR EACH DO (INSERT
              default::AuditEntry
              {
                  action := 'update',
                  entity_type := 'Comment',
                  entity_id := __new__.id,
                  version := __new__.version,
                  at := __new__.modified_at,
                  snapshot := <std::json>{
                      body := __new__.body,
                      document_slug := __new__.document_slug,
                      version := __new__.version
                  }
              });
      ALTER LINK document {
          ON TARGET DELETE DELETE SOURCE;
      };
      ALTER PROPERTY document_slug {
          CREATE REWRITE
              INSERT 
              USING (__subject__.document.slug);
          CREATE REWRITE
              UPDATE 
              USING (__subject__.document.slug);
      };
  };
  ALTER TYPE default::Document {
      CREATE REQUIRED PROPERTY created_at: std::datetime {
          SET default := (std::datetime_current());
          CREATE REWRITE
              INSERT 
              USING (std::datetime_of_statement());
          CREATE REWRITE
              UPDATE 
              USING (__old__.created_at);
      };
      CREATE REQUIRED PROPERTY version: std::int64 {
          SET default := 1;
          CREATE REWRITE
              INSERT 
              USING (1);
          CREATE REWRITE
              UPDATE 
              USING ((__old__.version + 1));
      };
      CREATE TRIGGER audit_insert
          AFTER INSERT 
          FOR EACH DO (INSERT
              default::AuditEntry
              {
                  action := 'insert',
                  entity_type := 'Document',
                  entity_id := __new__.id,
                  version := __new__.version,
                  at := __new__.created_at,
                  snapshot := <std::json>{
                      slug := __new__.slug,
                      title := __new__.title,
                      version := __new__.version
                  }
              });
      CREATE REQUIRED PROPERTY modified_at: std::datetime {
          SET default := (std::datetime_current());
          CREATE REWRITE
              INSERT 
              USING (std::datetime_of_statement());
          CREATE REWRITE
              UPDATE 
              USING (std::datetime_of_statement());
      };
      CREATE TRIGGER audit_update
          AFTER UPDATE 
          FOR EACH DO (INSERT
              default::AuditEntry
              {
                  action := 'update',
                  entity_type := 'Document',
                  entity_id := __new__.id,
                  version := __new__.version,
                  at := __new__.modified_at,
                  snapshot := <std::json>{
                      slug := __new__.slug,
                      title := __new__.title,
                      version := __new__.version
                  }
              });
      CREATE TRIGGER audit_delete
          AFTER DELETE 
          FOR EACH DO (INSERT
              default::AuditEntry
              {
                  action := 'delete',
                  entity_type := 'Document',
                  entity_id := __old__.id,
                  version := __old__.version,
                  at := std::datetime_of_statement(),
                  snapshot := <std::json>{
                      slug := __old__.slug,
                      title := __old__.title,
                      version := __old__.version
                  }
              });
  };
};
