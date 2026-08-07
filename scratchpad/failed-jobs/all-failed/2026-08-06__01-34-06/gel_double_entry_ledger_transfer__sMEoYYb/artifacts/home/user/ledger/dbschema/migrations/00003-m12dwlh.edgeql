CREATE MIGRATION m12dwlhnwoiiludmq43vkxwjuibwbn6uk3swhjfm7swdnw4ngb7y2q
    ONTO m1zqallerc4wqp4xekqovkxds3b7tj3a2vmvtk47v4rpz6x5tsfkiq
{
  ALTER TYPE default::Account {
      CREATE PROPERTY balance := ((.opening_balance + std::sum(.<account[IS default::LedgerEntry].amount)));
  };
};
