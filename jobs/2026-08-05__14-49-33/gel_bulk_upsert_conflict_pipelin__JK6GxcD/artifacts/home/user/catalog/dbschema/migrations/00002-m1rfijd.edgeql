CREATE MIGRATION m1rfijdmzg3wtj34bftzovc5q4zzxynkxamvh6f33g6seh5ibfd6za
    ONTO m16mbahhzza6p23gz6p6vpatvk7y4bx7uk3nrdycesukjxzavt7yia
{
  CREATE TYPE default::Product {
      CREATE REQUIRED PROPERTY external_id: std::str;
      CREATE REQUIRED PROPERTY source_system: std::str;
      CREATE CONSTRAINT std::exclusive ON ((.source_system, .external_id));
      CREATE REQUIRED LINK supplier: default::Supplier;
      CREATE REQUIRED PROPERTY name: std::str {
          CREATE CONSTRAINT std::max_len_value(200);
      };
      CREATE REQUIRED PROPERTY price_cents: std::int64 {
          CREATE CONSTRAINT std::min_value(0);
      };
      CREATE REQUIRED PROPERTY revision: std::int64;
      CREATE REQUIRED PROPERTY updated_at: std::datetime;
  };
};
