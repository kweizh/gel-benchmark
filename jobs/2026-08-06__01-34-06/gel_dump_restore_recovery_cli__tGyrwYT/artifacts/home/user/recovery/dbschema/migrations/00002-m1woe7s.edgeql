CREATE MIGRATION m1woe7smmo7rvobpx4fjmqwpty7furnq2wxppr7rgs6c5yqyfvjnhq
    ONTO m1t6z4s5xcb42r5sdxrqrppcdjhytlleqfmbmihrxmpktfk2pw6r4q
{
  ALTER TYPE default::Shipment {
      ALTER LINK origin {
          SET REQUIRED USING (<default::Warehouse>{});
      };
      ALTER PROPERTY origin_code {
          SET REQUIRED USING (<std::str>{});
      };
      ALTER PROPERTY status {
          CREATE CONSTRAINT std::expression ON (((((__subject__ = 'pending') OR (__subject__ = 'in_transit')) OR (__subject__ = 'delivered')) OR (__subject__ = 'returned'))) {
              SET errmessage := 'Shipment status must be one of: pending, in_transit, delivered, returned';
          };
          SET REQUIRED USING (<std::str>{});
      };
      ALTER PROPERTY tracking {
          CREATE CONSTRAINT std::exclusive;
          CREATE CONSTRAINT std::expression ON (std::re_test('^[A-Z0-9-]{6,20}$', __subject__)) {
              SET errmessage := 'Shipment tracking must match ^[A-Z0-9-]{6,20}$';
          };
          SET REQUIRED USING (<std::str>{});
      };
      ALTER PROPERTY weight_kg {
          CREATE CONSTRAINT std::min_value(0.0001);
          SET REQUIRED USING (<std::float64>{});
      };
  };
  ALTER TYPE default::Warehouse {
      ALTER PROPERTY code {
          CREATE CONSTRAINT std::expression ON (std::re_test('^[A-Z0-9-]{3,12}$', __subject__)) {
              SET errmessage := 'Warehouse code must match ^[A-Z0-9-]{3,12}$';
          };
      };
  };
};
