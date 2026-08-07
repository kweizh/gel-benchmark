CREATE MIGRATION m1vlbzctsbmqmvud635ahit45rza5adnqs7lpp5k62b2w3anftpu4a
    ONTO initial
{
  CREATE TYPE default::Sku {
      CREATE REQUIRED PROPERTY code: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
      CREATE REQUIRED PROPERTY label: std::str;
  };
  CREATE TYPE default::Warehouse {
      CREATE REQUIRED PROPERTY code: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
      CREATE REQUIRED PROPERTY region: std::str;
  };
  CREATE TYPE default::LedgerLine {
      CREATE REQUIRED LINK sku: default::Sku;
      CREATE REQUIRED LINK warehouse: default::Warehouse;
      CREATE REQUIRED PROPERTY quantity: std::int64;
      CREATE REQUIRED PROPERTY tag: std::str;
  };
  CREATE TYPE default::ShelfCount {
      CREATE REQUIRED LINK sku: default::Sku;
      CREATE REQUIRED LINK warehouse: default::Warehouse;
      CREATE REQUIRED PROPERTY quantity: std::int64;
      CREATE REQUIRED PROPERTY tag: std::str;
  };
};
