CREATE MIGRATION m1clq5s2vvmkhhla4bydem7oub24b3sgqaf6j3ne35nhmlffp2selq
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
          CREATE CONSTRAINT std::min_ex_value(0);
          SET REQUIRED USING (<std::float64>{});
      };
  };
  ALTER TYPE default::Warehouse {
      ALTER PROPERTY code {
          CREATE CONSTRAINT std::regexp('^[A-Z0-9-]{3,12}$');
      };
  };
};
