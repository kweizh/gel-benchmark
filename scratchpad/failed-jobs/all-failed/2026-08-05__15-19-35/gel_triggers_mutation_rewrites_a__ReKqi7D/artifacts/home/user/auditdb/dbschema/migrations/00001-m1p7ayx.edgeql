CREATE MIGRATION m1p7ayxtinlmvdccctfyx7qvmskh6mhxd2uzgl3f3y2cumsi6aq3ra
    ONTO initial
{
  CREATE TYPE default::Document {
      CREATE REQUIRED PROPERTY body: std::str;
      CREATE REQUIRED PROPERTY slug: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
      CREATE REQUIRED PROPERTY title: std::str;
  };
  CREATE TYPE default::Comment {
      CREATE REQUIRED LINK document: default::Document;
      CREATE REQUIRED PROPERTY body: std::str;
  };
};
