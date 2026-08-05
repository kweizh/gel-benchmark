CREATE MIGRATION m1xnexea4sgtz7z263iwqxnspr5vqraja24gltm3aucyidvfrg26ca
    ONTO initial
{
  CREATE MODULE util IF NOT EXISTS;
  CREATE FUNCTION util::money_round(amount: std::decimal) ->  std::decimal USING (std::round(amount, 2));
  CREATE FUNCTION util::apply_discount(amount: std::decimal, pct: std::decimal, floor_amount: OPTIONAL std::decimal) ->  std::decimal USING (WITH
      d := 
          util::money_round((amount * (1 - (pct / 100))))
  SELECT
      (IF EXISTS (floor_amount) THEN std::max({d, floor_amount}) ELSE d)
  );
  CREATE FUNCTION util::gross_with_tax(net: std::decimal, NAMED ONLY tax_pct: std::decimal = 0) ->  std::decimal USING (util::money_round((net * (1 + (tax_pct / 100)))));
  CREATE FUNCTION util::installments(total: std::decimal, count: std::int64) -> SET OF std::decimal USING (WITH
      part := 
          util::money_round((total / count))
      ,
      parts := 
          std::array_fill(part, (count - 1))
      ,
      last := 
          (total - std::sum(std::array_unpack(parts)))
  SELECT
      ((IF (count < 1) THEN <std::decimal>{} ELSE (IF (count = 1) THEN {total} ELSE std::array_unpack(parts))) UNION {last})
  );
  CREATE FUNCTION util::total_of(VARIADIC amounts: std::decimal) ->  std::decimal USING (util::money_round(std::sum(std::array_unpack(amounts))));
  CREATE FUNCTION util::money_round(amount: std::decimal, places: std::int64) ->  std::decimal USING (std::round(amount, places));
};
