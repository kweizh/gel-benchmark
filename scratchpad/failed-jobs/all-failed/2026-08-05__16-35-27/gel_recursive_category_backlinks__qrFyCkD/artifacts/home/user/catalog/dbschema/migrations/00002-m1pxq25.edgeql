CREATE MIGRATION m1pxq25dzipy44qhnmxuq2glkxudnp7ojhcnzoybjzd65aeofmjkdq
    ONTO m1psxcefu3s7kdpkntvm3mjlnbkthpyyjjpu5jrfy3d3cfunt3qn2a
{
  ALTER TYPE default::Category {
      CREATE LINK audit := (std::assert_single(.<category[IS default::CategoryAudit]));
      CREATE LINK children := (.<parent[IS default::Category]);
      CREATE LINK products := (.<category[IS default::Product]);
  };
};
