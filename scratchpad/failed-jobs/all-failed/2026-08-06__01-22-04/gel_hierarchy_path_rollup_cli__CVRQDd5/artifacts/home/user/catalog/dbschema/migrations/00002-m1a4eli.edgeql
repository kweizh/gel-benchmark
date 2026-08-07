CREATE MIGRATION m1a4elizxl6i6ntklzap46ddn4mz7dy6eqdilznltav56pgz432xpa
    ONTO m13fvfx562priknzzpchm75oajl5dzm6r2jd2yfaqwpl7sdpxi43vq
{
  ALTER TYPE default::Category {
      CREATE PROPERTY path: std::str {
          CREATE REWRITE
              INSERT 
              USING ((((.parent.path ?? '') ++ '/') ++ .slug));
      };
      CREATE CONSTRAINT std::exclusive ON (.path);
      CREATE MULTI LINK ancestors := (DISTINCT ((((((((.parent UNION .parent.parent) UNION .parent.parent.parent) UNION .parent.parent.parent.parent) UNION .parent.parent.parent.parent.parent) UNION .parent.parent.parent.parent.parent.parent) UNION .parent.parent.parent.parent.parent.parent.parent) UNION .parent.parent.parent.parent.parent.parent.parent.parent)));
      CREATE MULTI LINK children := (.<parent[IS default::Category]);
      CREATE PROPERTY depth: std::int64 {
          CREATE REWRITE
              INSERT 
              USING (((.parent.depth ?? -1) + 1));
      };
  };
  CREATE TYPE default::Relocation {
      CREATE REQUIRED LINK category: default::Category;
      CREATE LINK new_parent: default::Category;
      CREATE TRIGGER reject_cycles
          AFTER INSERT 
          FOR EACH DO (std::assert(NOT ((EXISTS (__new__.new_parent) AND ((__new__.new_parent = __new__.category) OR (__new__.category IN __new__.new_parent.ancestors)))), message := 'CATEGORY_CYCLE: cannot move a category under itself or one of its own descendants'));
      CREATE TRIGGER apply_relocation
          AFTER INSERT 
          FOR EACH DO (WITH
              moved := 
                  __new__.category
              ,
              newp := 
                  __new__.new_parent
              ,
              old_depth := 
                  moved.depth
              ,
              old_path := 
                  moved.path
              ,
              new_depth := 
                  ((newp.depth ?? -1) + 1)
              ,
              new_path := 
                  (((newp.path ?? '') ++ '/') ++ moved.slug)
              ,
              depth_delta := 
                  (new_depth - old_depth)
              ,
              prefix_len := 
                  std::len(old_path)
              ,
              subtree := 
                  DISTINCT ((((((((moved UNION moved.<parent[IS default::Category]) UNION moved.<parent[IS default::Category].<parent[IS default::Category]) UNION moved.<parent[IS default::Category].<parent[IS default::Category].<parent[IS default::Category]) UNION moved.<parent[IS default::Category].<parent[IS default::Category].<parent[IS default::Category].<parent[IS default::Category]) UNION moved.<parent[IS default::Category].<parent[IS default::Category].<parent[IS default::Category].<parent[IS default::Category].<parent[IS default::Category]) UNION moved.<parent[IS default::Category].<parent[IS default::Category].<parent[IS default::Category].<parent[IS default::Category].<parent[IS default::Category].<parent[IS default::Category]) UNION moved.<parent[IS default::Category].<parent[IS default::Category].<parent[IS default::Category].<parent[IS default::Category].<parent[IS default::Category].<parent[IS default::Category].<parent[IS default::Category]))
          UPDATE
              default::Category
          FILTER
              (.id IN subtree.id)
          SET {
              parent := (newp IF (.id = moved.id) ELSE .parent),
              depth := (.depth + depth_delta),
              path := (new_path ++ (.path)[prefix_len:])
          });
      CREATE REQUIRED PROPERTY from_path: std::str {
          CREATE REWRITE
              INSERT 
              USING (.category.path);
      };
      CREATE REQUIRED PROPERTY to_path: std::str {
          CREATE REWRITE
              INSERT 
              USING ((((.new_parent.path ?? '') ++ '/') ++ .category.slug));
      };
  };
};
