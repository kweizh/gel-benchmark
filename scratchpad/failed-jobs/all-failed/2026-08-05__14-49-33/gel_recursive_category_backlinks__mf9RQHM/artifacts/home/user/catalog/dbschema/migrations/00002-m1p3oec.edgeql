CREATE MIGRATION m1p3oeccbwbmawjtgxptzxjn6l3cr7dimyief7c54fr7vskol5tukq
    ONTO m1psxcefu3s7kdpkntvm3mjlnbkthpyyjjpu5jrfy3d3cfunt3qn2a
{
  ALTER TYPE default::Category {
      CREATE LINK audit := (.<category[IS default::CategoryAudit]);
      CREATE MULTI LINK children := (.<parent[IS default::Category]);
      CREATE MULTI LINK products := (.<category[IS default::Product]);
  };
};
