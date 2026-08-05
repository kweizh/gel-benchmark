CREATE MIGRATION m1koznkr3ublqqt4nvp4y2bhr7evr5bqs4yp2cmuivknkv35zg376a
    ONTO initial
{
  CREATE TYPE default::SettingsLayer {
      CREATE REQUIRED PROPERTY active: std::bool {
          SET default := true;
      };
      CREATE REQUIRED PROPERTY doc: std::json;
      CREATE REQUIRED PROPERTY key: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
      CREATE REQUIRED PROPERTY revision: std::int64 {
          SET default := 0;
      };
      CREATE REQUIRED PROPERTY tier: std::str;
  };
  CREATE TYPE default::SettingsRecord {
      CREATE MULTI LINK layers: default::SettingsLayer {
          CREATE PROPERTY precedence: std::int64;
      };
      CREATE REQUIRED PROPERTY label: std::str;
      CREATE REQUIRED PROPERTY slug: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
  };
};
