CREATE MIGRATION m16u4whobai5jwhin3k2bma5yykdopqnlob4khxvavoznz7ngq6yqq
    ONTO initial
{
  CREATE SCALAR TYPE default::Role EXTENDING enum<Owner, Editor, Viewer>;
  CREATE TYPE default::ActivityLog {
      CREATE REQUIRED PROPERTY action: std::str;
      CREATE REQUIRED PROPERTY actor_email: std::str;
      CREATE REQUIRED PROPERTY at: std::datetime {
          SET default := (std::datetime_of_statement());
      };
      CREATE REQUIRED PROPERTY doc_title: std::str;
  };
  CREATE TYPE default::Actor {
      CREATE REQUIRED PROPERTY display_name: std::str;
      CREATE REQUIRED PROPERTY email: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
  };
  CREATE TYPE default::Workspace {
      CREATE REQUIRED PROPERTY name: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
  };
  CREATE TYPE default::Membership {
      CREATE REQUIRED LINK actor: default::Actor;
      CREATE REQUIRED LINK workspace: default::Workspace;
      CREATE CONSTRAINT std::exclusive ON ((.actor, .workspace));
      CREATE REQUIRED PROPERTY role: default::Role;
  };
  CREATE TYPE default::Document {
      CREATE REQUIRED LINK owner: default::Actor;
      CREATE REQUIRED LINK workspace: default::Workspace;
      CREATE REQUIRED PROPERTY archived: std::bool {
          SET default := false;
      };
      CREATE REQUIRED PROPERTY body: std::str;
      CREATE REQUIRED PROPERTY title: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
  };
};
