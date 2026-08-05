CREATE MIGRATION m1zo2cdkwwxcaqbfl7xsg5sg5lusnfo6tvtcomyl35qkxvrc65ux7a
    ONTO initial
{
  CREATE MODULE billing IF NOT EXISTS;
  CREATE MODULE reports IF NOT EXISTS;
  CREATE MODULE util IF NOT EXISTS;
  CREATE FUNCTION util::apply_discount(amount: std::decimal, pct: std::decimal, floor_amount: OPTIONAL std::decimal) ->  std::decimal USING (WITH
      d := 
          std::round((amount * (1n - (pct / 100n))), 2)
  SELECT
      std::max({d, floor_amount})
  );
  CREATE FUNCTION util::money_round(amount: std::decimal) ->  std::decimal USING (std::round(amount, 2));
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
      CREATE REQUIRED PROPERTY installment_count: std::int64 {
          SET default := 1;
      };
      CREATE REQUIRED PROPERTY code: std::str {
          CREATE CONSTRAINT std::exclusive;
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
      CREATE LINK lines := (.<invoice[IS billing::LineItem]);
      CREATE PROPERTY subtotal := (util::money_round(std::sum(.lines.line_total)));
      CREATE PROPERTY total_due := (util::apply_discount(.subtotal, .customer.discount_pct, .minimum_charge));
  };
  CREATE ALIAS reports::CustomerBalance := (
      SELECT
          billing::Customer {
              outstanding := util::money_round(std::sum(((SELECT
                  billing::Invoice
              FILTER
                  ((.customer = billing::Customer) AND NOT (.paid))
              )).total_due))
          }
  );
  CREATE FUNCTION util::installments(total: std::decimal, count: std::int64) -> SET OF std::decimal USING ((IF (count < 1) THEN <std::decimal>{} ELSE (WITH
      c := 
          count
      ,
      upper := 
          (IF (count >= 1) THEN (count + 1) ELSE 1)
  FOR i IN std::range_unpack(std::range(1, upper))
  UNION 
      (IF (i < count) THEN std::round((total / c), 2) ELSE (total - ((count - 1) * std::round((total / c), 2)))))));
  CREATE ALIAS reports::InvoicePlan := (
      SELECT
          billing::Invoice {
              plan := util::installments(.total_due, .installment_count)
          }
  );
  CREATE ALIAS reports::UnpaidInvoice := (
      SELECT
          billing::Invoice
      FILTER
          NOT (.paid)
  );
  CREATE FUNCTION billing::customer_outstanding(customer_name: std::str) ->  std::decimal {
      SET volatility := 'Stable';
      USING (util::money_round(std::sum(((SELECT
          billing::Invoice
      FILTER
          ((.customer.name = customer_name) AND NOT (.paid))
      )).total_due)))
  ;};
  CREATE FUNCTION util::gross_with_tax(net: std::decimal, NAMED ONLY tax_pct: std::decimal = 0) ->  std::decimal USING (std::round((net * (1n + (tax_pct / 100n))), 2));
  CREATE FUNCTION util::money_round(amount: std::decimal, places: std::int64) ->  std::decimal USING (std::round(amount, places));
  CREATE FUNCTION util::total_of(VARIADIC amounts: std::decimal) ->  std::decimal USING (std::round(std::sum(std::array_unpack(amounts)), 2));
};
