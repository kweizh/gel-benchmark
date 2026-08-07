CREATE MIGRATION m1pvz7s47dbtjkgmoha7dtq56yrsmisk54acnmfloamhye2dxsx4qa
    ONTO m1j3wi4cg4mlesfw7ad3u5g54zayi2m55vc3hioozjv46kzj2nc2da
{
  ALTER TYPE default::Sample {
      ALTER PROPERTY intake_at {
          SET default := (std::datetime_of_statement());
      };
  };
};
