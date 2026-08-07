CREATE MIGRATION m13fvfx562priknzzpchm75oajl5dzm6r2jd2yfaqwpl7sdpxi43vq
    ONTO initial
{
  CREATE TYPE default::Category {
      CREATE LINK parent: default::Category;
      CREATE REQUIRED PROPERTY name: std::str;
      CREATE REQUIRED PROPERTY slug: std::str;
  };
  CREATE TYPE default::Product {
      CREATE REQUIRED LINK category: default::Category;
      CREATE REQUIRED PROPERTY name: std::str;
      CREATE REQUIRED PROPERTY price: std::decimal;
      CREATE REQUIRED PROPERTY sku: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
      CREATE REQUIRED PROPERTY stock: std::int64;
  };
};
