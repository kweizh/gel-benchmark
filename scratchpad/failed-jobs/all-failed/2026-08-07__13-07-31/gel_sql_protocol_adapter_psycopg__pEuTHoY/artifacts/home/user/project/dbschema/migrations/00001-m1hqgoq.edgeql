CREATE MIGRATION m1hqgoq4fbozgqukotwhbv7sl72jjqma4in77k27nmxctn4ruqpvsa
    ONTO initial
{
  CREATE MODULE catalog IF NOT EXISTS;
  CREATE ABSTRACT TYPE catalog::Asset {
      CREATE REQUIRED PROPERTY slug: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
      CREATE REQUIRED PROPERTY title: std::str;
  };
  CREATE TYPE catalog::Album EXTENDING catalog::Asset {
      CREATE REQUIRED PROPERTY year: std::int32;
      CREATE INDEX ON (.year);
      CREATE PROPERTY label: std::str;
  };
  CREATE TYPE catalog::Artist {
      CREATE MULTI PROPERTY aliases: std::str;
      CREATE REQUIRED PROPERTY country: std::str {
          CREATE CONSTRAINT std::expression ON (std::re_test('^[A-Z]{2}$', __subject__));
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
      CREATE REQUIRED PROPERTY duration_ms: std::int64 {
          CREATE CONSTRAINT std::expression ON ((__subject__ >= 1));
      };
      CREATE REQUIRED PROPERTY royalty_rate: std::decimal;
      CREATE PROPERTY payout_micros := (<std::int64>std::math::floor((<std::decimal>.duration_ms * .royalty_rate)));
      CREATE MULTI PROPERTY tags: std::str;
  };
};
