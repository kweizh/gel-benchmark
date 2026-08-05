CREATE MIGRATION m1fp7i5llammtymee4klfnrdewt44xj53xej3zjdbalzdkm25pe5ra
    ONTO initial
{
  CREATE TYPE default::Author {
      CREATE REQUIRED PROPERTY country: std::str;
      CREATE REQUIRED PROPERTY name: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
  };
  CREATE TYPE default::Article {
      CREATE REQUIRED LINK author: default::Author;
      CREATE REQUIRED PROPERTY title: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
      CREATE REQUIRED PROPERTY word_count: std::int64;
  };
};
