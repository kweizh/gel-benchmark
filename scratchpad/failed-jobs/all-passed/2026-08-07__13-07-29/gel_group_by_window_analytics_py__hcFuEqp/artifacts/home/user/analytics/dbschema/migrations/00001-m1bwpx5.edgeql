CREATE MIGRATION m1bwpx5zxi42ukjkrmkw4vgcbzlhkieofpu727p33z6sqdthk3u42a
    ONTO initial
{
  CREATE TYPE default::Category {
      CREATE REQUIRED PROPERTY name: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
      CREATE REQUIRED PROPERTY region: std::str;
  };
  CREATE TYPE default::Sale {
      CREATE REQUIRED LINK category: default::Category;
      CREATE REQUIRED PROPERTY amount_cents: std::int64;
      CREATE REQUIRED PROPERTY channel: std::str;
      CREATE REQUIRED PROPERTY occurred_at: std::datetime;
      CREATE REQUIRED PROPERTY order_ref: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
      CREATE REQUIRED PROPERTY units: std::int64;
  };
};
