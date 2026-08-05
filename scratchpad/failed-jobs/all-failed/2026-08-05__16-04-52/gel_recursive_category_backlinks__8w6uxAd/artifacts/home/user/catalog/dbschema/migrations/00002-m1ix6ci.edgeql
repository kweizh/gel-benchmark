CREATE MIGRATION m1ix6cih5hkqfslfgsxt3np4exoejmb43sytmpgbk445hfuiuxmpwa
    ONTO m1psxcefu3s7kdpkntvm3mjlnbkthpyyjjpu5jrfy3d3cfunt3qn2a
{
  ALTER TYPE default::Category {
      CREATE SINGLE LINK audit := (.<category[IS default::CategoryAudit]);
      CREATE MULTI LINK children := (.<parent[IS default::Category]);
      CREATE MULTI LINK products := (.<category[IS default::Product]);
  };
};
