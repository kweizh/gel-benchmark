CREATE MIGRATION m1xgpeabunk2gailcdopdbmyg7gu6vf6kzhzr63u3qzijyabhivxla
    ONTO initial
{
  CREATE TYPE default::Category {
      CREATE REQUIRED PROPERTY name: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
  };
  CREATE TYPE default::Tag {
      CREATE REQUIRED PROPERTY label: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
  };
  CREATE TYPE default::Product {
      CREATE REQUIRED LINK category: default::Category;
      CREATE MULTI LINK tags: default::Tag;
      CREATE REQUIRED PROPERTY name: std::str;
      CREATE REQUIRED PROPERTY price_cents: std::int64 {
          CREATE CONSTRAINT std::expression ON ((__subject__ >= 0));
      };
      CREATE REQUIRED PROPERTY sku: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
      CREATE REQUIRED PROPERTY stock: std::int64 {
          CREATE CONSTRAINT std::expression ON (((__subject__ >= 0) AND (__subject__ <= 100000)));
      };
  };
};
