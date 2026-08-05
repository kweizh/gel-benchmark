CREATE MIGRATION m1f2pouu5eud4djqdfkcd7edb3brqsmhdeodc6nut6c2fhrtvuid2q
    ONTO initial
{
  CREATE SCALAR TYPE default::AnalyteCode EXTENDING std::str {
      CREATE CONSTRAINT std::regexp('^[A-Z][A-Z0-9]{2,7}$') {
          SET errmessage := 'invalid analyte code';
      };
  };
  CREATE SCALAR TYPE default::MeasuredValue EXTENDING std::float64 {
      CREATE CONSTRAINT std::max_value(100000.0) {
          SET errmessage := 'value exceeds instrument ceiling';
      };
      CREATE CONSTRAINT std::min_value(0.0) {
          SET errmessage := 'value must not be negative';
      };
  };
  CREATE SCALAR TYPE default::ReviewState EXTENDING enum<pending, validated, rejected>;
  CREATE SCALAR TYPE default::SpecimenCode EXTENDING std::str {
      CREATE CONSTRAINT std::regexp(r'^SPC-\d{6}-[A-Z]{2}$') {
          SET errmessage := 'invalid specimen code';
      };
  };
  CREATE SCALAR TYPE default::Unit EXTENDING enum<mg_per_dL, mmol_per_L, g_per_L, IU_per_L>;
  CREATE ABSTRACT CONSTRAINT default::clean_label(max_len: std::int64) {
      SET errmessage := 'malformed label';
      USING ((((std::len(__subject__) > 0) AND (std::str_trim(__subject__) = __subject__)) AND (std::len(__subject__) <= max_len)));
  };
  CREATE ABSTRACT TYPE default::Sample {
      CREATE REQUIRED PROPERTY label: std::str {
          CREATE CONSTRAINT default::clean_label(40);
      };
      CREATE REQUIRED PROPERTY specimen_code: default::SpecimenCode;
      CREATE REQUIRED PROPERTY volume_ml: std::float64 {
          CREATE CONSTRAINT std::expression ON ((__subject__ > 0.0)) {
              SET errmessage := 'volume must be positive';
          };
      };
  };
  CREATE TYPE default::BloodSample EXTENDING default::Sample {
      CREATE CONSTRAINT std::exclusive ON (__subject__.specimen_code) {
          SET errmessage := 'specimen code already registered';
      };
      CREATE CONSTRAINT std::expression ON ((__subject__.volume_ml <= 10.0)) {
          SET errmessage := 'blood volume exceeds 10 ml';
      };
      CREATE REQUIRED PROPERTY tube_count: std::int16 {
          CREATE CONSTRAINT std::expression ON (((__subject__ >= 1) AND (__subject__ <= 6))) {
              SET errmessage := 'tube count out of range';
          };
      };
  };
  CREATE TYPE default::Measurement {
      CREATE REQUIRED PROPERTY label: std::str {
          CREATE CONSTRAINT default::clean_label(80);
      };
      CREATE REQUIRED LINK sample: default::Sample;
      CREATE REQUIRED PROPERTY analyte: default::AnalyteCode;
      CREATE CONSTRAINT std::exclusive ON ((__subject__.sample, __subject__.analyte)) {
          SET errmessage := 'duplicate analyte for sample';
      };
      CREATE REQUIRED PROPERTY ref_high: std::float64;
      CREATE REQUIRED PROPERTY ref_low: std::float64;
      CREATE CONSTRAINT std::expression ON ((__subject__.ref_low < __subject__.ref_high)) {
          SET errmessage := 'reference interval not ascending';
      };
      CREATE REQUIRED PROPERTY state: default::ReviewState {
          SET default := (<default::ReviewState>'pending');
      };
      CREATE REQUIRED PROPERTY value: default::MeasuredValue;
      CREATE CONSTRAINT std::expression ON (((__subject__.state != <default::ReviewState>'validated') OR ((__subject__.value >= __subject__.ref_low) AND (__subject__.value <= __subject__.ref_high)))) {
          SET errmessage := 'validated value outside reference interval';
      };
      CREATE REQUIRED PROPERTY unit: default::Unit;
  };
  CREATE TYPE default::UrineSample EXTENDING default::Sample {
      CREATE CONSTRAINT std::exclusive ON (__subject__.specimen_code) {
          SET errmessage := 'specimen code already registered';
      };
      CREATE CONSTRAINT std::expression ON ((__subject__.volume_ml <= 500.0)) {
          SET errmessage := 'urine volume exceeds 500 ml';
      };
  };
};
