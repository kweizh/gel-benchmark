CREATE MIGRATION m1ruh54rxahaaxsghp2zx6shs5yzxtxqpxg4asmlnnfybpe4j5bbma
    ONTO initial
{
  CREATE TYPE default::Article {
      CREATE REQUIRED PROPERTY title: std::str;
      CREATE INDEX std::fts::index ON (std::fts::with_options(.title, language := std::fts::Language.eng, weight_category := std::fts::Weight.A));
      CREATE REQUIRED PROPERTY body: std::str;
      CREATE REQUIRED PROPERTY published: std::bool;
      CREATE REQUIRED PROPERTY section: std::str;
      CREATE REQUIRED PROPERTY slug: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
      CREATE REQUIRED PROPERTY summary: std::str;
  };
};
