CREATE MIGRATION m1t6z4s5xcb42r5sdxrqrppcdjhytlleqfmbmihrxmpktfk2pw6r4q
    ONTO initial
{
  CREATE TYPE default::Warehouse {
      CREATE REQUIRED PROPERTY code: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
      CREATE REQUIRED PROPERTY name: std::str;
  };
  CREATE TYPE default::Shipment {
      CREATE LINK origin: default::Warehouse;
      CREATE PROPERTY origin_code: std::str;
      CREATE REQUIRED PROPERTY seq: std::int64 {
          CREATE CONSTRAINT std::exclusive;
      };
      CREATE PROPERTY status: std::str;
      CREATE PROPERTY tracking: std::str;
      CREATE PROPERTY weight_kg: std::float64;
  };
};
