CREATE MIGRATION m17o7esggsyxdixekq3s6bc75jzhjz64tatomrth5nblbqootqnx5a
    ONTO initial
{
  CREATE TYPE default::Product {
      CREATE REQUIRED PROPERTY name: std::str;
      CREATE REQUIRED PROPERTY price_cents: std::int64 {
          CREATE CONSTRAINT std::min_value(1);
      };
      CREATE REQUIRED PROPERTY sku: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
      CREATE REQUIRED PROPERTY stock: std::int64 {
          SET default := 0;
      };
  };
};
