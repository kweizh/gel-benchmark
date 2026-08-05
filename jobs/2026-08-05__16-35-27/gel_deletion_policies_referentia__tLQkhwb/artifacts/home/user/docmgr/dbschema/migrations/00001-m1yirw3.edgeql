CREATE MIGRATION m1yirw3c2aa4o3ib76sg777mvzyfnfmohgzedpgocfjbw6o4cielba
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
      CREATE MULTI LINK attachments: default::Attachment {
          ON SOURCE DELETE DELETE TARGET;
      };
      CREATE LINK checked_out_by: default::Editor {
          ON TARGET DELETE DEFERRED RESTRICT;
      };
      CREATE REQUIRED LINK folder: default::Folder {
          ON TARGET DELETE DELETE SOURCE;
      };
      CREATE REQUIRED PROPERTY title: std::str;
  };
  CREATE TYPE default::ArchivedRecord {
      CREATE REQUIRED LINK document: default::Document {
          ON TARGET DELETE RESTRICT;
      };
      CREATE REQUIRED PROPERTY archived_at: std::datetime {
          SET default := (std::datetime_of_statement());
      };
      CREATE REQUIRED PROPERTY label: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
  };
};
