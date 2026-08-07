CREATE MIGRATION m1j3wi4cg4mlesfw7ad3u5g54zayi2m55vc3hioozjv46kzj2nc2da
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
          SET default := (std::datetime_current());
          SET readonly := true;
      };
      CREATE PROPERTY age := ((std::datetime_of_statement() - .intake_at));
      CREATE REQUIRED PROPERTY grams: std::float64;
      CREATE REQUIRED PROPERTY intake_ref: std::uuid {
          SET default := (std::uuid_generate_v4());
          SET readonly := true;
      };
      CREATE REQUIRED PROPERTY label: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
      CREATE PROPERTY label_key := (std::str_lower(.label));
  };
  ALTER TYPE default::Measurement {
      CREATE REQUIRED LINK sample: default::Sample;
  };
  ALTER TYPE default::Sample {
      CREATE MULTI LINK assays := (.<sample[IS default::Assay]);
      CREATE PROPERTY assay_count := (std::count(.assays));
      CREATE PROPERTY total_value := (std::sum(.assays.value));
  };
  CREATE TYPE default::Batch {
      CREATE MULTI LINK samples: default::Sample {
          CREATE PROPERTY position: std::int64;
      };
      CREATE PROPERTY sample_count := (std::count(.samples));
      CREATE REQUIRED PROPERTY code: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
  };
  ALTER TYPE default::Sample {
      CREATE MULTI LINK batches := (.<samples[IS default::Batch]);
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
      CREATE LINK certificate := (.<sample[IS default::Certificate]);
      CREATE MULTI LINK measurements := (.<sample[IS default::Measurement]);
      CREATE PROPERTY measurement_count := (std::count(.measurements));
  };
};
