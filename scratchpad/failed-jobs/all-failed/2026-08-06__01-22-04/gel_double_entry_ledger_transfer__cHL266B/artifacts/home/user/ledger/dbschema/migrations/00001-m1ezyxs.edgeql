CREATE MIGRATION m1ezyxs5xgn3z7x2u2lpdpqosvleyqsrnewmmxos4rxj2qs52psnva
    ONTO initial
{
  CREATE TYPE default::Account {
      CREATE REQUIRED PROPERTY code: std::str;
      CREATE REQUIRED PROPERTY opening_balance: std::decimal;
  };
};
