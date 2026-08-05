CREATE MIGRATION m1e5fswpmhz74wxw22rrtjrlpnqyemyt7ubzn3umbaytfcrqczopqq
    ONTO initial
{
  CREATE TYPE default::Region {
      CREATE REQUIRED PROPERTY code: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
      CREATE REQUIRED PROPERTY name: std::str;
  };
  CREATE TYPE default::Grower {
      CREATE REQUIRED LINK region: default::Region;
      CREATE REQUIRED PROPERTY name: std::str;
      CREATE REQUIRED PROPERTY slug: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
  };
  CREATE TYPE default::Batch {
      CREATE REQUIRED PROPERTY code: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
      CREATE INDEX ON (.code);
      CREATE REQUIRED LINK grower: default::Grower;
      CREATE MULTI PROPERTY certifications: std::str;
      CREATE REQUIRED PROPERTY harvested_on: std::cal::local_date;
      CREATE REQUIRED PROPERTY kilograms: std::float64;
  };
  CREATE TYPE default::Defect {
      CREATE REQUIRED PROPERTY code: std::str;
      CREATE REQUIRED PROPERTY severity: std::int64;
  };
  CREATE TYPE default::Inspection {
      CREATE REQUIRED LINK batch: default::Batch;
      CREATE MULTI LINK defects: default::Defect;
      CREATE REQUIRED PROPERTY inspector: std::str;
      CREATE REQUIRED PROPERTY passed: std::bool;
      CREATE REQUIRED PROPERTY recorded_at: std::datetime {
          SET default := (std::datetime_of_statement());
      };
  };
};
