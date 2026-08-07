CREATE MIGRATION m14ydklnd3mne74ffmzyjyxbkzkppzzdyxcjmgy6si6khosmluo3aq
    ONTO m13fvfx562priknzzpchm75oajl5dzm6r2jd2yfaqwpl7sdpxi43vq
{
  ALTER TYPE default::Category {
      CREATE PROPERTY path: std::str {
          CREATE REWRITE
              INSERT 
              USING ((((.parent.path ++ '/') ++ .slug) ?? ('/' ++ .slug)));
      };
      CREATE PROPERTY depth: std::int64 {
          CREATE REWRITE
              INSERT 
              USING (((.parent.depth + 1) ?? 0));
      };
  };

  # Populating existing categories
  UPDATE default::Category FILTER NOT EXISTS .parent SET { depth := 0, path := '/' ++ .slug };
  UPDATE default::Category FILTER EXISTS .parent AND .parent.depth = 0 SET { depth := 1, path := .parent.path ++ '/' ++ .slug };
  UPDATE default::Category FILTER EXISTS .parent AND .parent.depth = 1 SET { depth := 2, path := .parent.path ++ '/' ++ .slug };
  UPDATE default::Category FILTER EXISTS .parent AND .parent.depth = 2 SET { depth := 3, path := .parent.path ++ '/' ++ .slug };
  UPDATE default::Category FILTER EXISTS .parent AND .parent.depth = 3 SET { depth := 4, path := .parent.path ++ '/' ++ .slug };
  UPDATE default::Category FILTER EXISTS .parent AND .parent.depth = 4 SET { depth := 5, path := .parent.path ++ '/' ++ .slug };

  ALTER TYPE default::Category {
      ALTER PROPERTY path {
          SET REQUIRED USING (<std::str>.path);
          CREATE CONSTRAINT std::exclusive;
      };
      ALTER PROPERTY depth {
          SET REQUIRED USING (<std::int64>.depth);
      };
      CREATE MULTI LINK ancestors := (SELECT
          default::Category
      FILTER
          (__source__.path LIKE (.path ++ '/%'))
      );
      CREATE MULTI LINK children := (SELECT
          default::Category
      FILTER
          (.parent = __source__)
      );
  };

  CREATE TYPE default::Relocation {
      CREATE REQUIRED LINK category: default::Category;
      CREATE REQUIRED PROPERTY from_path: std::str {
          CREATE REWRITE
              INSERT 
              USING (.category.path);
      };
      CREATE LINK new_parent: default::Category;
      CREATE REQUIRED PROPERTY to_path: std::str {
          CREATE REWRITE
              INSERT 
              USING ((((.new_parent.path ++ '/') ++ .category.slug) ?? ('/' ++ .category.slug)));
      };
      CREATE TRIGGER apply_relocation
          AFTER INSERT 
          FOR EACH DO (UPDATE
              default::Category
          FILTER
              ((.id = __new__.category.id) OR (.path LIKE (__new__.from_path ++ '/%')))
          SET {
              parent := (__new__.new_parent IF (.id = __new__.category.id) ELSE .parent),
              path := (__new__.to_path IF (.id = __new__.category.id) ELSE (__new__.to_path ++ (.path)[std::len(__new__.from_path):])),
              depth := (((__new__.new_parent.depth + 1) ?? 0) IF (.id = __new__.category.id) ELSE (.depth + (((__new__.new_parent.depth + 1) ?? 0) - __new__.category.depth)))
          });
      CREATE TRIGGER reject_cycles
          AFTER INSERT 
          FOR EACH DO (SELECT
              std::assert(NOT ((EXISTS (__new__.new_parent) AND ((__new__.new_parent.path = __new__.category.path) OR (__new__.new_parent.path LIKE (__new__.category.path ++ '/%'))))), message := 'CATEGORY_CYCLE')
          );
  };
};
