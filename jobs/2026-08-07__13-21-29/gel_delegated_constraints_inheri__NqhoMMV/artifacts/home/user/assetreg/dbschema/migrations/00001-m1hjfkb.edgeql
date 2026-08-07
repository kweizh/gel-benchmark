CREATE MIGRATION m1hjfkba67fxvnsedmcnggp2orj77435nm3b2hizb6b6fqsdiuzi2a
    ONTO initial
{
  CREATE ABSTRACT CONSTRAINT default::token_invalid(max: std::int64) {
      SET errmessage := 'TOKEN_INVALID(max={max})';
      USING ((std::re_test(<std::str>__subject__, '^[A-Z][A-Z0-9_]*$') AND (std::len(<std::str>__subject__) <= max)));
  };
  CREATE TYPE default::Region {
      CREATE REQUIRED PROPERTY key: std::str {
          CREATE CONSTRAINT std::exclusive {
              SET errmessage := 'REGION_KEY_DUPLICATE';
          };
      };
  };
  CREATE ABSTRACT TYPE default::Revisioned {
      CREATE REQUIRED PROPERTY revision: std::int64 {
          CREATE CONSTRAINT std::min_value(1) {
              SET errmessage := 'REVISION_TOO_LOW';
          };
      };
  };
  CREATE ABSTRACT TYPE default::Taggable {
      CREATE MULTI PROPERTY tags: std::str {
          CREATE CONSTRAINT default::token_invalid(12);
      };
  };
  CREATE ABSTRACT TYPE default::Asset EXTENDING default::Revisioned, default::Taggable {
      CREATE REQUIRED PROPERTY code: std::str {
          CREATE CONSTRAINT default::token_invalid(16);
          CREATE DELEGATED CONSTRAINT std::exclusive {
              SET errmessage := 'CODE_DUPLICATE_IN_KIND';
          };
      };
      CREATE REQUIRED LINK region: default::Region;
      CREATE REQUIRED PROPERTY capacity: std::int64;
      CREATE REQUIRED PROPERTY reserved: std::int64;
      CREATE REQUIRED PROPERTY serial: std::str {
          CREATE CONSTRAINT std::exclusive {
              SET errmessage := 'SERIAL_DUPLICATE_GLOBAL';
          };
      };
      CREATE REQUIRED PROPERTY slot: std::int64;
      CREATE DELEGATED CONSTRAINT std::exclusive ON ((.region, .slot)) {
          SET errmessage := 'SLOT_DUPLICATE_IN_KIND';
      };
      CREATE DELEGATED CONSTRAINT std::expression ON ((__subject__.reserved <= __subject__.capacity)) {
          SET errmessage := 'CAPACITY_EXCEEDED';
      };
  };
  CREATE TYPE default::ServerAsset EXTENDING default::Asset {
      CREATE REQUIRED PROPERTY hostname: std::str;
  };
  CREATE TYPE default::StorageAsset EXTENDING default::Asset {
      CREATE REQUIRED PROPERTY volume_gb: std::int64;
  };
  CREATE TYPE default::Operator {
      CREATE MULTI LINK crew: default::Asset {
          CREATE PROPERTY role: std::str;
          CREATE CONSTRAINT std::exclusive ON (__subject__@role) {
              SET errmessage := 'ROLE_DUPLICATE_FOR_OPERATOR';
          };
      };
      CREATE REQUIRED PROPERTY name: std::str {
          CREATE CONSTRAINT std::exclusive {
              SET errmessage := 'OPERATOR_NAME_DUPLICATE';
          };
      };
  };
};
