CREATE MIGRATION m1pynyqqhcq3rhtijufwddmkk5mts4jfivpccjnqceou2tca2vcnoa
    ONTO initial
{
  CREATE TYPE default::Attachment {
      CREATE REQUIRED PROPERTY byte_size: std::int64;
      CREATE REQUIRED PROPERTY filename: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
  };
  CREATE TYPE default::Editor {
      CREATE REQUIRED PROPERTY email: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
  };
  CREATE TYPE default::Workspace {
      CREATE REQUIRED PROPERTY name: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
  };
  CREATE TYPE default::Folder {
      CREATE REQUIRED LINK workspace: default::Workspace {
          ON TARGET DELETE DELETE SOURCE;
      };
      CREATE REQUIRED PROPERTY name: std::str;
  };
  CREATE TYPE default::Document {
      CREATE MULTI LINK attachments: default::Attachment;
      CREATE TRIGGER cleanup_orphan_attachments
          AFTER DELETE 
          FOR EACH DO (FOR att IN __old__.attachments
          UNION 
              (DELETE
                  att
              FILTER
                  NOT (EXISTS ((SELECT
                      default::Document
                  FILTER
                      (att IN .attachments)
                  )))
              ));
      CREATE LINK checked_out_by: default::Editor {
          ON TARGET DELETE DEFERRED RESTRICT;
      };
      CREATE REQUIRED LINK folder: default::Folder {
          ON TARGET DELETE DELETE SOURCE;
      };
      CREATE REQUIRED PROPERTY title: std::str;
  };
  CREATE TYPE default::ArchivedRecord {
      CREATE REQUIRED LINK document: default::Document;
      CREATE REQUIRED PROPERTY archived_at: std::datetime {
          SET default := (std::datetime_of_statement());
      };
      CREATE REQUIRED PROPERTY label: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
  };
};
