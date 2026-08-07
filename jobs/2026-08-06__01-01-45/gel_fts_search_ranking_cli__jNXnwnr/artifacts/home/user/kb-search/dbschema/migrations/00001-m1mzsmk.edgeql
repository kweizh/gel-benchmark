CREATE MIGRATION m1mzsmkfnfzqdb5aofc4jdbvhqyqahj3su6i6vvclfwel6d7cac5za
    ONTO initial
{
  CREATE TYPE default::Article {
      CREATE REQUIRED PROPERTY body: std::str;
      CREATE REQUIRED PROPERTY summary: std::str;
      CREATE REQUIRED PROPERTY title: std::str;
      CREATE INDEX std::fts::index ON ((std::fts::with_options(.title, language := std::fts::Language.eng, weight_category := std::fts::Weight.A), std::fts::with_options(.summary, language := std::fts::Language.eng, weight_category := std::fts::Weight.B), std::fts::with_options(.body, language := std::fts::Language.eng, weight_category := std::fts::Weight.C)));
      CREATE REQUIRED PROPERTY published: std::bool;
      CREATE REQUIRED PROPERTY section: std::str;
      CREATE REQUIRED PROPERTY slug: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
  };
};
