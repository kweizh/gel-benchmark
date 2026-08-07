CREATE MIGRATION m1kkwqzyek7u6mi2a573ow75y26jxheolseb45v6cselwthgrzw2nq
    ONTO m1yoktsrph3shmeqtv37bcdvjx6zmyg2t3anqvyoeey6iiiaah2xda
{
  CREATE MODULE logistics IF NOT EXISTS;
  CREATE SCALAR TYPE logistics::packable_grams EXTENDING std::int64 {
      CREATE CONSTRAINT std::min_value(50) {
          SET errmessage := 'weight must be at least 50 grams';
      };
  };
  CREATE SCALAR TYPE logistics::shipping_tier EXTENDING enum<Ground, Express, Overnight>;
  CREATE SCALAR TYPE logistics::sku_code EXTENDING std::str {
      CREATE CONSTRAINT std::regexp('^SKU-[0-9]{4}$') {
          SET errmessage := 'sku must match SKU-0000';
      };
  };
  CREATE ABSTRACT CONSTRAINT logistics::multiple_of(step: std::int64) {
      SET errmessage := 'weight must be a multiple of {step} grams';
      USING (((__subject__ % step) = 0));
  };
  ALTER SCALAR TYPE logistics::packable_grams {
      CREATE CONSTRAINT logistics::multiple_of(50);
  };
  CREATE FUNCTION logistics::batch_total_cents(VARIADIC quotes: std::int64) ->  std::int64 {
      SET volatility := 'Immutable';
      USING (std::sum(std::array_unpack(quotes)))
  ;};
  CREATE FUNCTION logistics::billable_grams(grams: std::int64) ->  std::int64 {
      SET volatility := 'Immutable';
      USING (WITH
          calculated := 
              <std::int64>std::math::ceil((grams * 1.00))
      SELECT
          (IF (calculated > 500) THEN calculated ELSE 500)
      )
  ;};
  CREATE FUNCTION logistics::billable_grams(grams: std::int64, tier: logistics::shipping_tier) ->  std::int64 {
      SET volatility := 'Immutable';
      USING (WITH
          factor := 
              (IF (tier = logistics::shipping_tier.Ground) THEN 1.00 ELSE (IF (tier = logistics::shipping_tier.Express) THEN 1.25 ELSE 1.50))
          ,
          floor := 
              (IF (tier = logistics::shipping_tier.Ground) THEN 500 ELSE (IF (tier = logistics::shipping_tier.Express) THEN 1000 ELSE 2000))
          ,
          calculated := 
              <std::int64>std::math::ceil((grams * factor))
      SELECT
          (IF (calculated > floor) THEN calculated ELSE floor)
      )
  ;};
  CREATE FUNCTION logistics::quote_cents(grams: std::int64, tier: logistics::shipping_tier, NAMED ONLY insured: std::bool = false) ->  std::int64 {
      SET volatility := 'Immutable';
      USING (WITH
          chargeable := 
              logistics::billable_grams(grams, tier)
          ,
          rate := 
              (IF (tier = logistics::shipping_tier.Ground) THEN 4 ELSE (IF (tier = logistics::shipping_tier.Express) THEN 9 ELSE 15))
          ,
          base_price := 
              ((chargeable * rate) // 100)
      SELECT
          (IF insured THEN (base_price + 250) ELSE base_price)
      )
  ;};
  CREATE FUNCTION logistics::delivery_note(tier: logistics::shipping_tier, hint: OPTIONAL std::str) ->  std::str {
      SET volatility := 'Immutable';
      USING (((std::str_lower(<std::str>tier) ++ '/') ++ (hint ?? 'default')))
  ;};
  CREATE FUNCTION logistics::heaviest_grams(parcels: array<std::int64>) -> OPTIONAL std::int64 {
      SET volatility := 'Immutable';
      USING (std::max(std::array_unpack(parcels)))
  ;};
  CREATE FUNCTION logistics::tariff_version() ->  std::str {
      SET volatility := 'Stable';
      USING ('2026.02')
  ;};
};
