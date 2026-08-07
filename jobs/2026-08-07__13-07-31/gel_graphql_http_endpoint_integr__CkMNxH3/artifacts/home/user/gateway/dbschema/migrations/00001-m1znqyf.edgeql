CREATE MIGRATION m1znqyfourcu522uv2qphqqiznzb2tano5wfdj3eegy7sjfklftewq
    ONTO initial
{
  CREATE EXTENSION graphql VERSION '1.0';
  CREATE TYPE default::Service {
      CREATE REQUIRED PROPERTY active: std::bool;
      CREATE REQUIRED PROPERTY name: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
      CREATE REQUIRED PROPERTY tier: std::int64;
  };
  CREATE ALIAS default::ActiveService := (
      SELECT
          default::Service
      FILTER
          (.active = true)
  );
  CREATE TYPE default::Team {
      CREATE REQUIRED PROPERTY name: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
      CREATE REQUIRED PROPERTY region: std::str;
  };
  ALTER TYPE default::Service {
      CREATE REQUIRED LINK owner: default::Team;
      CREATE PROPERTY team_name := (.owner.name);
  };
  ALTER TYPE default::Team {
      CREATE LINK services := (SELECT
          default::Service
      FILTER
          (.owner = default::Team)
      );
      CREATE PROPERTY service_count := (std::count(.services));
  };
};
