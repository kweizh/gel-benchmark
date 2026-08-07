CREATE MIGRATION m1eopvnu6glopbbecwj4p3suhiyvyjvuymyrlfd4mj4yw2iiwg6tlq
    ONTO initial
{
  CREATE TYPE default::Author {
      CREATE REQUIRED PROPERTY country: std::str;
      CREATE REQUIRED PROPERTY name: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
  };
};
