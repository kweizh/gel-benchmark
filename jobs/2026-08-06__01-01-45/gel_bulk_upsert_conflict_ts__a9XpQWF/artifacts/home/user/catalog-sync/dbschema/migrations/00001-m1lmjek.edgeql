CREATE MIGRATION m1lmjekw4tskowpk37aonazqafw7wav2todzfboov7473flq632koq
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
          CREATE CONSTRAINT std::min_value(0);
      };
      CREATE REQUIRED PROPERTY sku: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
      CREATE REQUIRED PROPERTY stock: std::int64 {
          CREATE CONSTRAINT std::max_value(100000);
          CREATE CONSTRAINT std::min_value(0);
      };
  };
};
