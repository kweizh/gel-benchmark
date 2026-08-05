CREATE MIGRATION m1j6oruoijwmse4bc2lrvps3jyooim4rg64xxx6syrmpnodyq572tq
    ONTO m1hcsfqxlylwt44amrouhbglye3d5wctwy5nn7fzb7jqgyxvji2eca
{
  ALTER TYPE default::Article {
      CREATE REQUIRED PROPERTY review_state: std::str {
          SET REQUIRED USING (('needs_review' IF (.word_count >= 1200) ELSE 'archived'));
      };
  };
};
