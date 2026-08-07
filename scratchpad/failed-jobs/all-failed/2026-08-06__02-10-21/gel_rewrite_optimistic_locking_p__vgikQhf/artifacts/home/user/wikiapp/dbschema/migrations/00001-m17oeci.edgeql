CREATE MIGRATION m17oeci6gnb3ylxvhm4zojbr3f5zupzz6oetqmgxbjdyam56ztcloq
    ONTO initial
{
  CREATE TYPE default::Document {
      CREATE REQUIRED PROPERTY body: std::str;
      CREATE REQUIRED PROPERTY last_editor: std::str;
      CREATE REQUIRED PROPERTY revision: std::int64 {
          CREATE REWRITE
              INSERT 
              USING (1);
          CREATE REWRITE
              UPDATE 
              USING ((__old__.revision + 1));
      };
      CREATE REQUIRED PROPERTY title: std::str;
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
      CREATE REQUIRED PROPERTY slug: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
      CREATE REQUIRED PROPERTY title_modified_at: std::datetime {
          CREATE REWRITE
              UPDATE 
              USING ((std::datetime_of_statement() IF __specified__.title ELSE __old__.title_modified_at));
          CREATE REWRITE
              INSERT 
              USING (std::datetime_of_statement());
      };
  };
  CREATE TYPE default::DocumentRevision {
      CREATE REQUIRED LINK document: default::Document;
      CREATE REQUIRED PROPERTY author: std::str;
      CREATE REQUIRED PROPERTY body: std::str;
      CREATE REQUIRED PROPERTY revision: std::int64;
      CREATE REQUIRED PROPERTY title: std::str;
      CREATE CONSTRAINT std::exclusive ON ((__subject__.document, __subject__.revision));
      CREATE PROPERTY recorded_at: std::datetime {
          SET default := (std::datetime_of_statement());
      };
  };
  ALTER TYPE default::Document {
      CREATE TRIGGER record_history
          AFTER UPDATE, INSERT 
          FOR EACH DO (INSERT
              default::DocumentRevision
              {
                  document := __new__,
                  revision := __new__.revision,
                  title := __new__.title,
                  body := __new__.body,
                  author := __new__.last_editor
              });
  };
};
