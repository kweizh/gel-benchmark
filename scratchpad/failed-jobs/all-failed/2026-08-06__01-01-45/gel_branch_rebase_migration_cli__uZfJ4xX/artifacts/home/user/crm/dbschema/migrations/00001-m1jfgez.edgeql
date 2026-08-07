CREATE MIGRATION m1jfgezqqkznwxijetyevwiica5xkj4aix7wrv4pzonao5ktlpx2qa
    ONTO initial
{
  CREATE TYPE default::Contact {
      CREATE REQUIRED PROPERTY email: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
      CREATE REQUIRED PROPERTY full_name: std::str;
      CREATE PROPERTY note: std::str;
      CREATE REQUIRED PROPERTY stage: std::str {
          SET default := 'lead';
      };
  };
};
