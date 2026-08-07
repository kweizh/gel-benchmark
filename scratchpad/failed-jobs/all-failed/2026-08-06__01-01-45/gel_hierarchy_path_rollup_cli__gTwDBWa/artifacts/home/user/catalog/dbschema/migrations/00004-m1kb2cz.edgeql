CREATE MIGRATION m1kb2czdenlsvxazc2ol4vcvxkdtsiwziyulfco4cjacwxq4ywioxa
    ONTO m137suqvggvxy7vv7z7sjyamb5aj4u342txmz6wtccd653yiqelcnq
{
  ALTER TYPE default::Category {
      ALTER LINK ancestors {
          USING (DISTINCT ((((((((((.parent UNION .parent.parent) UNION .parent.parent.parent) UNION .parent.parent.parent.parent) UNION .parent.parent.parent.parent.parent) UNION .parent.parent.parent.parent.parent.parent) UNION .parent.parent.parent.parent.parent.parent.parent) UNION .parent.parent.parent.parent.parent.parent.parent.parent) UNION .parent.parent.parent.parent.parent.parent.parent.parent.parent) UNION .parent.parent.parent.parent.parent.parent.parent.parent.parent.parent)));
      };
  };
};
