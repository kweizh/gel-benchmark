CREATE MIGRATION m1rmia6bdz3xhcjlpevxkk3lnbsttw7asts2szocbkcvuvp4l2vhwq
    ONTO initial
{
  CREATE SCALAR TYPE default::ArticleStatus EXTENDING enum<draft, published, archived>;
  CREATE TYPE default::Article {
      CREATE REQUIRED PROPERTY body: std::str;
      CREATE REQUIRED PROPERTY summary: std::str;
      CREATE REQUIRED PROPERTY title: std::str;
      CREATE INDEX std::fts::index ON ((std::fts::with_options(.title, language := std::fts::Language.eng, weight_category := std::fts::Weight.A), std::fts::with_options(.summary, language := std::fts::Language.eng, weight_category := std::fts::Weight.B), std::fts::with_options(.body, language := std::fts::Language.eng, weight_category := std::fts::Weight.C)));
      CREATE REQUIRED PROPERTY slug: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
      CREATE REQUIRED PROPERTY status: default::ArticleStatus;
      CREATE MULTI PROPERTY tags: std::str;
  };
};
