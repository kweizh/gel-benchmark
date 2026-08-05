CREATE MIGRATION m1snp236t2b6wze5ywvetphtawtq6qqp4i3lcdh7mgukhg6kf65cqq
    ONTO m1p7ayxtinlmvdccctfyx7qvmskh6mhxd2uzgl3f3y2cumsi6aq3ra
{
  CREATE TYPE default::AuditEntry {
      CREATE ACCESS POLICY allow_read_write
          ALLOW SELECT, INSERT ;
      CREATE ACCESS POLICY deny_modify
          DENY UPDATE, DELETE ;
      CREATE REQUIRED PROPERTY action: std::str;
      CREATE REQUIRED PROPERTY at: std::datetime;
      CREATE REQUIRED PROPERTY entity_id: std::uuid;
      CREATE REQUIRED PROPERTY entity_type: std::str;
      CREATE REQUIRED PROPERTY snapshot: std::json;
      CREATE REQUIRED PROPERTY version: std::int64;
  };
  ALTER TYPE default::Comment {
      CREATE REQUIRED PROPERTY document_slug: std::str {
          SET REQUIRED USING (.document.slug);
      };
  };
  CREATE ABSTRACT TYPE default::Auditable {
      CREATE REQUIRED PROPERTY created_at: std::datetime {
          CREATE REWRITE
              INSERT 
              USING (std::datetime_of_statement());
          CREATE REWRITE
              UPDATE 
              USING (__old__.created_at);
      };
      CREATE REQUIRED PROPERTY modified_at: std::datetime {
          CREATE REWRITE
              INSERT 
              USING (std::datetime_of_statement());
          CREATE REWRITE
              UPDATE 
              USING (std::datetime_of_statement());
      };
      CREATE REQUIRED PROPERTY version: std::int64 {
          CREATE REWRITE
              INSERT 
              USING (1);
          CREATE REWRITE
              UPDATE 
              USING ((__old__.version + 1));
      };
  };
  ALTER TYPE default::Comment {
      CREATE PROPERTY created_at: std::datetime {
          SET REQUIRED USING (std::datetime_current());
      };
      CREATE PROPERTY modified_at: std::datetime {
          SET REQUIRED USING (std::datetime_current());
      };
      CREATE PROPERTY version: std::int64 {
          SET REQUIRED USING (1);
      };
      EXTENDING default::Auditable LAST;
  };
  ALTER TYPE default::Document {
      CREATE PROPERTY created_at: std::datetime {
          SET REQUIRED USING (std::datetime_current());
      };
      CREATE PROPERTY modified_at: std::datetime {
          SET REQUIRED USING (std::datetime_current());
      };
      CREATE PROPERTY version: std::int64 {
          SET REQUIRED USING (1);
      };
      EXTENDING default::Auditable LAST;
  };
  ALTER TYPE default::Comment {
      ALTER LINK document {
          ON TARGET DELETE DELETE SOURCE;
      };
      ALTER PROPERTY document_slug {
          CREATE REWRITE
              INSERT 
              USING (.document.slug);
          CREATE REWRITE
              UPDATE 
              USING (.document.slug);
      };
      ALTER PROPERTY created_at {
          RESET OPTIONALITY;
          DROP OWNED;
          RESET TYPE;
      };
      ALTER PROPERTY version {
          RESET OPTIONALITY;
          DROP OWNED;
          RESET TYPE;
      };
      CREATE TRIGGER log_insert
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
      ALTER PROPERTY modified_at {
          RESET OPTIONALITY;
          DROP OWNED;
          RESET TYPE;
      };
      CREATE TRIGGER log_update
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
      CREATE TRIGGER log_delete
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
  };
  ALTER TYPE default::Document {
      ALTER PROPERTY created_at {
          RESET OPTIONALITY;
          DROP OWNED;
          RESET TYPE;
      };
      ALTER PROPERTY version {
          RESET OPTIONALITY;
          DROP OWNED;
          RESET TYPE;
      };
      CREATE TRIGGER log_insert
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
      ALTER PROPERTY modified_at {
          RESET OPTIONALITY;
          DROP OWNED;
          RESET TYPE;
      };
      CREATE TRIGGER log_update
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
      CREATE TRIGGER log_delete
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
