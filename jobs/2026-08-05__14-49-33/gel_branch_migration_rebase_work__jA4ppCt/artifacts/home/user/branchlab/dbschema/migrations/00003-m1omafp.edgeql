CREATE MIGRATION m1omafpphgl7rmfmblglzpmf7gxxs44ln5gfuv6famtj6cadlhnhna
    ONTO m1hcsfqxlylwt44amrouhbglye3d5wctwy5nn7fzb7jqgyxvji2eca
{
  ALTER TYPE default::Article {
      CREATE REQUIRED PROPERTY review_state: std::str {
          SET default := (('needs_review' IF (.word_count >= 1200) ELSE 'archived'));
      };
  };
};
