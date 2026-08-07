CREATE MIGRATION m1wi7wwipbam6pn227qfxpvcc5tixru3uq75gk23x4wvz36k4shbba
    ONTO m1ezyxs5xgn3z7x2u2lpdpqosvleyqsrnewmmxos4rxj2qs52psnva
{
  CREATE TYPE default::LedgerEntry {
      CREATE REQUIRED LINK account: default::Account;
      CREATE REQUIRED PROPERTY amount: std::decimal {
          CREATE CONSTRAINT std::expression ON ((__subject__ != 0.0n)) {
              SET errmessage := 'amount must not be zero';
          };
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
      CREATE CONSTRAINT std::expression ON ((__subject__.sender != __subject__.recipient)) {
          SET errmessage := 'sender and recipient must be different accounts';
      };
      CREATE REQUIRED PROPERTY amount: std::decimal {
          CREATE CONSTRAINT std::expression ON ((__subject__ > 0.0n)) {
              SET errmessage := 'amount must be positive';
          };
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
