CREATE MIGRATION m1mu7g2ktgzfngc77xyw6eqmbftrkw52xx436xz2yj6thu27gwnkjq
    ONTO m1kkwqzyek7u6mi2a573ow75y26jxheolseb45v6cselwthgrzw2nq
{
  CREATE TYPE default::Parcel {
      CREATE REQUIRED LINK carrier: default::Carrier;
      CREATE REQUIRED PROPERTY tier: logistics::shipping_tier;
      CREATE REQUIRED PROPERTY weight_grams: logistics::packable_grams;
      CREATE PROPERTY chargeable_grams := (logistics::billable_grams(.weight_grams, .tier));
      CREATE OPTIONAL PROPERTY handling_hint: std::str;
      CREATE PROPERTY note := (logistics::delivery_note(.tier, .handling_hint));
      CREATE REQUIRED PROPERTY insured: std::bool {
          SET default := false;
      };
      CREATE PROPERTY price_cents := (logistics::quote_cents(.weight_grams, .tier, insured := .insured));
      CREATE REQUIRED PROPERTY sku: logistics::sku_code {
          CREATE CONSTRAINT std::exclusive;
      };
  };
  CREATE TYPE default::Shipment {
      CREATE MULTI LINK parcels: default::Parcel;
      CREATE PROPERTY total_quote_cents := (std::sum(.parcels.price_cents));
      CREATE PROPERTY heaviest_parcel_grams := (logistics::heaviest_grams(std::array_agg(.parcels.weight_grams)));
      CREATE REQUIRED PROPERTY code: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
  };
};
