CREATE MIGRATION m1ikzxcpvr3aezoa4xmzbvxd7elowwrfqu5seetub4t6xuu27oqwnq
    ONTO initial
{
  CREATE TYPE default::Tag {
      CREATE REQUIRED PROPERTY label: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
  };
  CREATE TYPE default::Vendor {
      CREATE REQUIRED PROPERTY code: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
      CREATE REQUIRED PROPERTY name: std::str;
  };
  CREATE TYPE default::Product {
      CREATE MULTI LINK tags: default::Tag;
      CREATE REQUIRED LINK vendor: default::Vendor;
      CREATE REQUIRED PROPERTY price_cents: std::int64;
      CREATE REQUIRED PROPERTY revision: std::int64;
      CREATE REQUIRED PROPERTY sku: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
      CREATE REQUIRED PROPERTY title: std::str;
  };
  CREATE TYPE default::Variant {
      CREATE REQUIRED LINK product: default::Product;
      CREATE REQUIRED PROPERTY code: std::str;
      CREATE CONSTRAINT std::exclusive ON ((.product, .code));
      CREATE REQUIRED PROPERTY label: std::str;
      CREATE REQUIRED PROPERTY stock: std::int64;
  };
  CREATE TYPE default::SyncSource {
      CREATE REQUIRED PROPERTY code: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
      CREATE REQUIRED PROPERTY revision: std::int64;
  };
};
