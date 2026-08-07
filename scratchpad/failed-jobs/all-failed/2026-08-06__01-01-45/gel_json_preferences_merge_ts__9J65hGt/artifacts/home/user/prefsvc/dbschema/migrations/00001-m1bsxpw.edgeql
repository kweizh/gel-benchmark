CREATE MIGRATION m1bsxpwnrywqngdbfp34ibldwoh72oi5lvjf6mj43ihr5wtqrorovq
    ONTO initial
{
  CREATE TYPE default::PrefUser {
      CREATE REQUIRED PROPERTY email: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
      CREATE REQUIRED PROPERTY preferences: std::json {
          SET default := (std::to_json('{}'));
      };
  };
};
