CREATE MIGRATION m1e5hedbquychaskn62yxfzrs3mobvmpt6o43xtyd24qsk5wgv5tua
    ONTO m1yirw3c2aa4o3ib76sg777mvzyfnfmohgzedpgocfjbw6o4cielba
{
  ALTER TYPE default::Document {
      ALTER LINK attachments {
          ON SOURCE DELETE DELETE TARGET IF ORPHAN;
      };
  };
};
