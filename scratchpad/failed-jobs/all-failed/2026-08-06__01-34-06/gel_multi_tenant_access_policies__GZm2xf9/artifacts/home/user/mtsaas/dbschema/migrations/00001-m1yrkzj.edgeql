CREATE MIGRATION m1yrkzjfsjxapwkf3psozdjfcnzdjaw6e7jc2aryxhu5gskbje5rlq
    ONTO initial
{
  CREATE TYPE default::Tenant {
      CREATE REQUIRED PROPERTY name: std::str;
      CREATE REQUIRED PROPERTY slug: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
  };
  CREATE TYPE default::Workspace {
      CREATE REQUIRED LINK tenant: default::Tenant;
      CREATE REQUIRED PROPERTY name: std::str;
      CREATE CONSTRAINT std::exclusive ON ((.tenant, .name));
      CREATE REQUIRED PROPERTY archived: std::bool {
          SET default := false;
      };
  };
  CREATE TYPE default::Document {
      CREATE REQUIRED PROPERTY title: std::str;
      CREATE INDEX ON (.title);
      CREATE REQUIRED LINK workspace: default::Workspace;
      CREATE REQUIRED PROPERTY body: std::str;
      CREATE REQUIRED PROPERTY created_at: std::datetime {
          SET default := (std::datetime_of_statement());
      };
  };
  CREATE TYPE default::Comment {
      CREATE REQUIRED LINK document: default::Document;
      CREATE REQUIRED PROPERTY author_email: std::str;
      CREATE REQUIRED PROPERTY body: std::str;
  };
};
