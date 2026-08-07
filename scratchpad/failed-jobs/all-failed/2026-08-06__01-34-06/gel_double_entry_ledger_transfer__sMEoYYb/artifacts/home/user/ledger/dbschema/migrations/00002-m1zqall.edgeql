CREATE MIGRATION m1zqallerc4wqp4xekqovkxds3b7tj3a2vmvtk47v4rpz6x5tsfkiq
    ONTO m1ezyxs5xgn3z7x2u2lpdpqosvleyqsrnewmmxos4rxj2qs52psnva
{
  ALTER TYPE default::Account {
      CREATE CONSTRAINT std::exclusive ON (.code);
  };
  CREATE TYPE default::Transfer {
      CREATE REQUIRED PROPERTY idempotency_key: std::str;
      CREATE CONSTRAINT std::exclusive ON (.idempotency_key);
      CREATE REQUIRED LINK recipient: default::Account;
      CREATE REQUIRED LINK sender: default::Account;
      CREATE CONSTRAINT std::expression ON ((.sender != .recipient)) {
          SET errmessage := 'Sender and recipient must be different accounts';
      };
      CREATE REQUIRED PROPERTY amount: std::decimal;
      CREATE CONSTRAINT std::expression ON ((.amount > 0)) {
          SET errmessage := 'Transfer amount must be positive';
      };
      CREATE REQUIRED PROPERTY created_at: std::datetime {
          SET default := (std::datetime_current());
      };
  };
  CREATE TYPE default::LedgerEntry {
      CREATE REQUIRED LINK account: default::Account;
      CREATE REQUIRED LINK transfer: default::Transfer;
      CREATE CONSTRAINT std::exclusive ON ((.account, .transfer));
      CREATE REQUIRED PROPERTY amount: std::decimal;
      CREATE CONSTRAINT std::expression ON ((.amount != 0)) {
          SET errmessage := 'LedgerEntry amount must not be zero';
      };
  };
};
