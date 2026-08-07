CREATE MIGRATION m1dwvln23wkvtbui5adkrxho35h6fqlnwlbl7e4d7kfe5crixiqvvq
    ONTO m1t6z4s5xcb42r5sdxrqrppcdjhytlleqfmbmihrxmpktfk2pw6r4q
{
  ALTER TYPE default::Shipment {
      ALTER LINK origin {
          SET REQUIRED USING (<default::Warehouse>{});
      };
      ALTER PROPERTY status {
          CREATE CONSTRAINT std::one_of('pending', 'in_transit', 'delivered', 'returned');
          SET REQUIRED USING (<std::str>{});
      };
      ALTER PROPERTY tracking {
          CREATE CONSTRAINT std::exclusive;
          CREATE CONSTRAINT std::regexp('^[A-Z0-9-]{6,20}$');
          SET REQUIRED USING (<std::str>{});
      };
      ALTER PROPERTY weight_kg {
          CREATE CONSTRAINT std::expression ON ((__subject__ > 0));
          SET REQUIRED USING (<std::float64>{});
      };
  };
  ALTER TYPE default::Warehouse {
      ALTER PROPERTY code {
          CREATE CONSTRAINT std::regexp('^[A-Z0-9-]{3,12}$');
      };
  };
};
