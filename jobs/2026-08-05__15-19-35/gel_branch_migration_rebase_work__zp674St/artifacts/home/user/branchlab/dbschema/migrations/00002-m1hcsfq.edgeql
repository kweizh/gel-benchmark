CREATE MIGRATION m1hcsfqxlylwt44amrouhbglye3d5wctwy5nn7fzb7jqgyxvji2eca
    ONTO m1fp7i5llammtymee4klfnrdewt44xj53xej3zjdbalzdkm25pe5ra
{
  CREATE TYPE default::Tag {
      CREATE REQUIRED PROPERTY label: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
  };
  ALTER TYPE default::Article {
      CREATE MULTI LINK tags: default::Tag;
  };
};
