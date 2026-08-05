CREATE MIGRATION m1psxcefu3s7kdpkntvm3mjlnbkthpyyjjpu5jrfy3d3cfunt3qn2a
    ONTO initial
{
  CREATE ABSTRACT TYPE default::Node {
      CREATE REQUIRED PROPERTY label: std::str;
      CREATE REQUIRED PROPERTY slug: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
  };
  CREATE TYPE default::Category EXTENDING default::Node {
      CREATE LINK parent: default::Category;
      CREATE REQUIRED PROPERTY rank: std::int64 {
          SET default := 0;
      };
  };
  CREATE ABSTRACT TYPE default::Listing EXTENDING default::Node {
      CREATE REQUIRED LINK category: default::Category;
      CREATE REQUIRED PROPERTY price_cents: std::int64 {
          CREATE CONSTRAINT std::min_value(0);
      };
  };
  CREATE TYPE default::Bundle EXTENDING default::Listing {
      CREATE REQUIRED PROPERTY item_count: std::int64 {
          CREATE CONSTRAINT std::min_value(2);
      };
  };
  CREATE TYPE default::CategoryAudit {
      CREATE REQUIRED LINK category: default::Category {
          CREATE CONSTRAINT std::exclusive;
      };
      CREATE REQUIRED PROPERTY checked_by: std::str;
  };
  CREATE TYPE default::Product EXTENDING default::Listing {
      CREATE REQUIRED PROPERTY in_stock: std::bool {
          SET default := true;
      };
  };
};
