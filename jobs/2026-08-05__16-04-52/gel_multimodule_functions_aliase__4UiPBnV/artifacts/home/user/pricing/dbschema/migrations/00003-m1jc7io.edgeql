CREATE MIGRATION m1jc7ioxgejekwdgtadb4kefn3ry4rdh6sd3epkhgps2iyijwez5aq
    ONTO m1gn3vsngxiycbgpwwqenth4rlx2woqld4527u3uzzuefs5txre55q
{
  CREATE MODULE billing IF NOT EXISTS;
  CREATE TYPE billing::Customer {
      CREATE REQUIRED PROPERTY discount_pct: std::decimal {
          SET default := 0;
      };
      CREATE REQUIRED PROPERTY name: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
  };
  CREATE TYPE billing::Invoice {
      CREATE REQUIRED LINK customer: billing::Customer;
      CREATE PROPERTY minimum_charge: std::decimal;
      CREATE REQUIRED PROPERTY paid: std::bool {
          SET default := false;
      };
      CREATE REQUIRED PROPERTY code: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
      CREATE REQUIRED PROPERTY installment_count: std::int64 {
          SET default := 1;
      };
  };
  CREATE TYPE billing::LineItem {
      CREATE REQUIRED LINK invoice: billing::Invoice;
      CREATE REQUIRED PROPERTY qty: std::int64;
      CREATE REQUIRED PROPERTY unit_price: std::decimal {
          CREATE CONSTRAINT std::expression ON ((__subject__ = util::money_round(__subject__)));
      };
      CREATE PROPERTY line_total := (util::money_round((.qty * .unit_price)));
      CREATE REQUIRED PROPERTY description: std::str;
  };
  ALTER TYPE billing::Invoice {
      CREATE MULTI LINK lines := (.<invoice[IS billing::LineItem]);
      CREATE PROPERTY subtotal := (util::money_round(std::sum(.lines.line_total)));
      CREATE PROPERTY total_due := (util::apply_discount(.subtotal, .customer.discount_pct, .minimum_charge));
  };
  CREATE FUNCTION billing::customer_outstanding(customer_name: std::str) ->  std::decimal {
      SET volatility := 'Stable';
      USING (WITH
          cust := 
              (SELECT
                  billing::Customer
              FILTER
                  (.name = customer_name)
              )
          ,
          unpaid := 
              (SELECT
                  billing::Invoice
              FILTER
                  ((.customer = cust) AND NOT (.paid))
              )
      SELECT
          util::money_round(std::sum(unpaid.total_due))
      )
  ;};
};
