CREATE MIGRATION m13edu47kjyjjbet3k2lh6twvfaaatkpnnlwvcnb6ptuktejmlmtwq
    ONTO initial
{
  CREATE ABSTRACT TYPE default::Measurement {
      CREATE REQUIRED PROPERTY code: std::str;
  };
  CREATE TYPE default::Assay EXTENDING default::Measurement {
      CREATE REQUIRED PROPERTY value: std::float64;
  };
  CREATE TYPE default::Sample {
      CREATE REQUIRED PROPERTY intake_at: std::datetime {
          SET default := (std::datetime_of_statement());
          SET readonly := true;
      };
      CREATE REQUIRED PROPERTY age := ((std::datetime_of_statement() - .intake_at));
      CREATE REQUIRED PROPERTY grams: std::float64;
      CREATE REQUIRED PROPERTY intake_ref: std::uuid {
          SET default := (std::uuid_generate_v4());
          SET readonly := true;
      };
      CREATE REQUIRED PROPERTY label: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
      CREATE REQUIRED PROPERTY label_key := (std::str_lower(.label));
  };
  ALTER TYPE default::Measurement {
      CREATE REQUIRED LINK sample: default::Sample;
  };
  ALTER TYPE default::Sample {
      CREATE MULTI LINK assays := (SELECT
          default::Assay
      FILTER
          (.sample = default::Sample)
      );
      CREATE REQUIRED PROPERTY assay_count := (std::count(.assays));
      CREATE REQUIRED PROPERTY total_value := (std::sum(.assays.value));
  };
  CREATE TYPE default::Batch {
      CREATE MULTI LINK samples: default::Sample {
          CREATE PROPERTY position: std::int64;
      };
      CREATE REQUIRED PROPERTY sample_count := (std::count(.samples));
      CREATE REQUIRED PROPERTY code: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
  };
  ALTER TYPE default::Sample {
      CREATE MULTI LINK batches := (SELECT
          default::Batch
      FILTER
          (default::Sample IN .samples)
      );
  };
  CREATE TYPE default::Calibration EXTENDING default::Measurement {
      CREATE REQUIRED PROPERTY bias: std::float64;
  };
  CREATE TYPE default::Certificate {
      CREATE REQUIRED LINK sample: default::Sample {
          CREATE CONSTRAINT std::exclusive;
      };
      CREATE REQUIRED PROPERTY serial: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
  };
  ALTER TYPE default::Sample {
      CREATE LINK certificate := (SELECT
          default::Certificate
      FILTER
          (.sample = default::Sample)
      );
      CREATE MULTI LINK measurements := (SELECT
          default::Measurement
      FILTER
          (.sample = default::Sample)
      );
      CREATE REQUIRED PROPERTY measurement_count := (std::count(.measurements));
  };
};
