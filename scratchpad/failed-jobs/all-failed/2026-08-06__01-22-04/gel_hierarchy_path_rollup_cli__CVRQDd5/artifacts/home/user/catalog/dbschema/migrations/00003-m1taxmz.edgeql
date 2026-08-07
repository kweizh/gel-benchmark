CREATE MIGRATION m1taxmzm3tt3qd45sld2alyvxu74rjfeybcrcjmisuspkjcd6xytqa
    ONTO m1a4elizxl6i6ntklzap46ddn4mz7dy6eqdilznltav56pgz432xpa
{
  ALTER TYPE default::Category {
      ALTER PROPERTY depth {
          SET REQUIRED USING (<std::int64>{});
      };
      ALTER PROPERTY path {
          SET REQUIRED USING (<std::str>{});
      };
  };
};
