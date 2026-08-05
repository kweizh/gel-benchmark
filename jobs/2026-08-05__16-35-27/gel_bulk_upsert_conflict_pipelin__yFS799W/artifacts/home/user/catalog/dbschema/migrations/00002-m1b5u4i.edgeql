CREATE MIGRATION m1b5u4i6wjru7y5vh7ewgajspgd7yatlmnywwdgg7fgahvsqrs3qwa
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
          CREATE CONSTRAINT std::expression ON ((__subject__ >= 0));
      };
      CREATE REQUIRED PROPERTY revision: std::int64;
      CREATE REQUIRED PROPERTY updated_at: std::datetime;
  };
};
