CREATE MIGRATION m1igybjiqjmrxbw3pv3t5nuwpo5jnu56cayztakotckd53ug3elhua
    ONTO m1eopvnu6glopbbecwj4p3suhiyvyjvuymyrlfd4mj4yw2iiwg6tlq
{
  CREATE ABSTRACT TYPE default::Resource {
      CREATE REQUIRED LINK author: default::Author;
      CREATE REQUIRED PROPERTY level: std::str;
      CREATE REQUIRED PROPERTY minutes: std::int64;
      CREATE REQUIRED PROPERTY title: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
  };
  CREATE TYPE default::Article EXTENDING default::Resource {
      CREATE REQUIRED PROPERTY word_count: std::int64;
  };
  ALTER TYPE default::Author {
      CREATE LINK resources := (.<author[IS default::Resource]);
      CREATE PROPERTY resource_count := (std::count(.<author[IS default::Resource]));
  };
  CREATE TYPE default::Video EXTENDING default::Resource {
      CREATE REQUIRED PROPERTY has_captions: std::bool;
  };
};
