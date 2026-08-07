CREATE MIGRATION m1bio572alz3mbthe2pphfttp6dtsf4sshji26bmwlwwk2ihjh3fqq
    ONTO initial
{
  CREATE MODULE catalog IF NOT EXISTS;
  CREATE ABSTRACT TYPE catalog::Asset {
      CREATE REQUIRED PROPERTY slug: std::str;
      CREATE REQUIRED PROPERTY title: std::str;
      CREATE CONSTRAINT std::exclusive ON (__subject__.slug);
  };
  CREATE TYPE catalog::Album EXTENDING catalog::Asset {
      CREATE REQUIRED PROPERTY year: std::int32;
      CREATE INDEX ON (__subject__.year);
      CREATE OPTIONAL PROPERTY label: std::str;
  };
  CREATE TYPE catalog::Artist {
      CREATE MULTI PROPERTY aliases: std::str;
      CREATE REQUIRED PROPERTY country: std::str {
          CREATE CONSTRAINT std::regexp('^[A-Z]{2}$');
      };
      CREATE REQUIRED PROPERTY handle: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
      CREATE REQUIRED PROPERTY name: std::str;
  };
  CREATE TYPE catalog::Track EXTENDING catalog::Asset {
      CREATE REQUIRED LINK album: catalog::Album;
      CREATE MULTI LINK contributors: catalog::Artist {
          CREATE PROPERTY role: std::str;
          CREATE PROPERTY share_bp: std::int64;
      };
      CREATE REQUIRED PROPERTY duration_ms: std::int64;
      CREATE CONSTRAINT std::expression ON ((.duration_ms >= 1));
      CREATE REQUIRED PROPERTY royalty_rate: std::decimal;
      CREATE PROPERTY payout_micros := (<std::int64>std::math::floor((.duration_ms * .royalty_rate)));
      CREATE MULTI PROPERTY tags: std::str;
  };
};
