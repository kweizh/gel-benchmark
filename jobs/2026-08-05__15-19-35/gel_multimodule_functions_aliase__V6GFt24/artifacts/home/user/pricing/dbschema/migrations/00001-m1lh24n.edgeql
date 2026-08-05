CREATE MIGRATION m1lh24nk7frwrqziqm57nmug6o7hyshyhlned2l4pcmcsueer24o4q
    ONTO initial
{
  CREATE MODULE billing IF NOT EXISTS;
  CREATE MODULE reports IF NOT EXISTS;
  CREATE MODULE util IF NOT EXISTS;
  CREATE FUNCTION util::money_round(amount: std::decimal) ->  std::decimal USING (std::round(amount, 2));
  CREATE FUNCTION util::apply_discount(amount: std::decimal, pct: std::decimal, floor_amount: OPTIONAL std::decimal) ->  std::decimal USING (WITH
      d := 
          util::money_round((amount - ((amount * pct) / <std::decimal>100)))
      ,
      f := 
          (floor_amount ?? d)
  SELECT
      (d IF (d >= f) ELSE f)
  );
  CREATE TYPE billing::Customer {
      CREATE REQUIRED PROPERTY discount_pct: std::decimal {
          SET default := (<std::decimal>0);
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
          CREATE CONSTRAINT std::expression ON ((util::money_round(__subject__) = __subject__)) {
              SET errmessage := 'unit_price must not have more than 2 decimal places';
          };
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
      billing::Customer {
          outstanding := util::money_round(std::sum(((SELECT
              .<customer[IS billing::Invoice]
          FILTER
              NOT (.paid)
          )).total_due))
      }
  );
  CREATE FUNCTION util::installments(total: std::decimal, count: std::int64) -> SET OF std::decimal USING (FOR i IN std::range_unpack(std::range(1, ((count + 1) IF (count >= 0) ELSE 1)))
  UNION 
      (util::money_round((total / count)) IF (i < count) ELSE (total - (util::money_round((total / count)) * (count - 1)))));
  CREATE ALIAS reports::InvoicePlan := (
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
  CREATE FUNCTION billing::customer_outstanding(customer_name: std::str) ->  std::decimal USING (util::money_round(std::sum(((SELECT
      billing::Invoice
  FILTER
      ((.customer.name = customer_name) AND NOT (.paid))
  )).total_due)));
  CREATE FUNCTION util::gross_with_tax(net: std::decimal, NAMED ONLY tax_pct: std::decimal = <decimal>0) ->  std::decimal USING (util::money_round((net + ((net * tax_pct) / <std::decimal>100))));
  CREATE FUNCTION util::total_of(VARIADIC amounts: std::decimal) ->  std::decimal USING (util::money_round(std::sum(std::array_unpack(amounts))));
  CREATE FUNCTION util::money_round(amount: std::decimal, places: std::int64) ->  std::decimal USING (std::round(amount, places));
};
