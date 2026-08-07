CREATE MIGRATION m1pg7l64of5fw7qogvs7gqd63n6ppffyw4gtmkxtrvj2i2izvzvm6q
    ONTO m1ezyxs5xgn3z7x2u2lpdpqosvleyqsrnewmmxos4rxj2qs52psnva
{
  CREATE TYPE default::LedgerEntry {
      CREATE REQUIRED LINK account: default::Account;
      CREATE REQUIRED PROPERTY amount: std::decimal {
          CREATE CONSTRAINT std::expression ON ((__subject__ != 0));
      };
  };
  ALTER TYPE default::Account {
      CREATE PROPERTY balance := ((.opening_balance + std::sum(.<account[IS default::LedgerEntry].amount)));
      ALTER PROPERTY code {
          CREATE CONSTRAINT std::exclusive;
      };
  };
  CREATE TYPE default::Transfer {
      CREATE REQUIRED LINK recipient: default::Account;
      CREATE REQUIRED LINK sender: default::Account;
      CREATE CONSTRAINT std::expression ON ((.sender != .recipient));
      CREATE REQUIRED PROPERTY amount: std::decimal {
          CREATE CONSTRAINT std::expression ON ((__subject__ > 0));
      };
      CREATE REQUIRED PROPERTY created_at: std::datetime {
          SET default := (std::datetime_of_statement());
      };
      CREATE REQUIRED PROPERTY idempotency_key: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
  };
  ALTER TYPE default::LedgerEntry {
      CREATE REQUIRED LINK transfer: default::Transfer;
      CREATE CONSTRAINT std::exclusive ON ((.account, .transfer));
  };
  ALTER TYPE default::Transfer {
      CREATE MULTI LINK entries := (.<transfer[IS default::LedgerEntry]);
  };
};
