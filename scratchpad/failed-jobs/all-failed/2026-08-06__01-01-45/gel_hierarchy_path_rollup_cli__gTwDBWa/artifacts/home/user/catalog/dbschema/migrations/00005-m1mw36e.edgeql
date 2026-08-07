CREATE MIGRATION m1mw36enckdg22vnsk2zgtsxgyo5xe2w4lj2xijj24zdbkq7g2kndq
    ONTO m1kb2czdenlsvxazc2ol4vcvxkdtsiwziyulfco4cjacwxq4ywioxa
{
  ALTER TYPE default::Relocation {
      ALTER TRIGGER apply_relocation USING (UPDATE
          default::Category
      FILTER
          ((.id = __new__.category.id) OR (.path LIKE (__new__.from_path ++ '/%')))
      SET {
          parent := (__new__.new_parent IF (.id = __new__.category.id) ELSE .parent),
          path := (__new__.to_path IF (.id = __new__.category.id) ELSE (__new__.to_path ++ (.path)[std::len(__new__.from_path):])),
          depth := (std::len(std::str_split((__new__.to_path IF (.id = __new__.category.id) ELSE (__new__.to_path ++ (.path)[std::len(__new__.from_path):])), '/')) - 2)
      });
      ALTER TRIGGER reject_cycles USING (SELECT
          std::assert(NOT ((EXISTS (__new__.new_parent) AND (((SELECT
              __new__.new_parent.path 
          LIMIT
              1
          ) = __new__.from_path) OR ((SELECT
              __new__.new_parent.path 
          LIMIT
              1
          ) LIKE (__new__.from_path ++ '/%'))))), message := 'CATEGORY_CYCLE')
      );
  };
};
