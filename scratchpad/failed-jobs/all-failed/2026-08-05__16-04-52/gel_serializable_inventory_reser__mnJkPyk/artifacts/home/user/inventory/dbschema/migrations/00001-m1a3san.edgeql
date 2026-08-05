CREATE MIGRATION m1a3sanectimacfq4xpavqo7zj62pq2ud37urqnvd2hgi5hlcjfiaa
    ONTO initial
{
  CREATE TYPE default::Reservation {
      CREATE PROPERTY expires_at: std::datetime;
      CREATE REQUIRED PROPERTY key: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
      CREATE REQUIRED PROPERTY state: std::str {
          SET default := 'active';
      };
  };
  CREATE TYPE default::StockItem {
      CREATE REQUIRED PROPERTY reserved: std::int64 {
          SET default := 0;
      };
      CREATE REQUIRED PROPERTY stock: std::int64 {
          CREATE CONSTRAINT std::min_value(0);
      };
      CREATE CONSTRAINT std::expression ON (((.reserved >= 0) AND (.reserved <= .stock))) {
          SET errmessage := 'reserved must be between 0 and stock';
      };
      CREATE REQUIRED PROPERTY sku: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
  };
  CREATE TYPE default::LedgerEntry {
      CREATE REQUIRED LINK item: default::StockItem;
      CREATE REQUIRED LINK reservation: default::Reservation;
      CREATE REQUIRED PROPERTY delta: std::int64;
      CREATE REQUIRED PROPERTY kind: std::str;
  };
  CREATE TYPE default::ReservationLine {
      CREATE REQUIRED LINK reservation: default::Reservation;
      CREATE REQUIRED LINK item: default::StockItem;
      CREATE REQUIRED PROPERTY quantity: std::int64 {
          CREATE CONSTRAINT std::min_value(1);
      };
  };
};
