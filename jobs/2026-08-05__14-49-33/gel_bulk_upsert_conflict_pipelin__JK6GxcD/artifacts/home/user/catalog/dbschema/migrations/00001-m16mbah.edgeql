CREATE MIGRATION m16mbahhzza6p23gz6p6vpatvk7y4bx7uk3nrdycesukjxzavt7yia
    ONTO initial
{
  CREATE TYPE default::Supplier {
      CREATE REQUIRED PROPERTY code: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
      CREATE REQUIRED PROPERTY name: std::str;
  };
};
