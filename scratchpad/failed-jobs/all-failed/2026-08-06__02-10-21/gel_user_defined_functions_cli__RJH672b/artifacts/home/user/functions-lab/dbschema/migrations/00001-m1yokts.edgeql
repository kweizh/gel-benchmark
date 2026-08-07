CREATE MIGRATION m1yoktsrph3shmeqtv37bcdvjx6zmyg2t3anqvyoeey6iiiaah2xda
    ONTO initial
{
  CREATE TYPE default::Carrier {
      CREATE REQUIRED PROPERTY hub_code: std::str;
      CREATE REQUIRED PROPERTY name: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
  };
};
