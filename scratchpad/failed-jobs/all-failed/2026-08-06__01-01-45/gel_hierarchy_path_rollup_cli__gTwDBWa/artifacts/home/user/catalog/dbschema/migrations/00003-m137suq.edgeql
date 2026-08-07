CREATE MIGRATION m137suqvggvxy7vv7z7sjyamb5aj4u342txmz6wtccd653yiqelcnq
    ONTO m14ydklnd3mne74ffmzyjyxbkzkppzzdyxcjmgy6si6khosmluo3aq
{
  ALTER TYPE default::Category {
      ALTER LINK ancestors {
          USING (SELECT
              default::Category
          FILTER
              (.path LIKE (default::Category.path ++ '/%'))
          );
      };
      ALTER LINK children {
          USING (.<parent[IS default::Category]);
      };
  };
};
