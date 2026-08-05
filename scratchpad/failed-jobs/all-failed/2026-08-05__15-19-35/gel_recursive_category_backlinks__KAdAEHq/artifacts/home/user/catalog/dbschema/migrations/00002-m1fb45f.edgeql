CREATE MIGRATION m1fb45fkdlbssyrvszbtovzashnil3j7yrjrrrmypdekqerje7jcua
    ONTO m1psxcefu3s7kdpkntvm3mjlnbkthpyyjjpu5jrfy3d3cfunt3qn2a
{
  ALTER TYPE default::Category {
      CREATE SINGLE LINK audit := (std::assert_single(.<category[IS default::CategoryAudit]));
      CREATE MULTI LINK children := (.<parent[IS default::Category]);
      CREATE MULTI LINK products := (.<category[IS default::Product]);
  };
};
