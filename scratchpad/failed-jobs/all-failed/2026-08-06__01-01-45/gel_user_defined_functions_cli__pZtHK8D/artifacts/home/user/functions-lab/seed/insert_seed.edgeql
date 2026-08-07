insert Parcel {
  sku := <logistics::sku_code>"SKU-1001",
  weight_grams := <logistics::packable_grams>1200,
  tier := logistics::shipping_tier.Express,
  insured := false,
  handling_hint := "fragile",
  carrier := (select Carrier filter .name = "Northwind" limit 1)
};

insert Parcel {
  sku := <logistics::sku_code>"SKU-1002",
  weight_grams := <logistics::packable_grams>400,
  tier := logistics::shipping_tier.Ground,
  insured := true,
  carrier := (select Carrier filter .name = "Northwind" limit 1)
};

insert Parcel {
  sku := <logistics::sku_code>"SKU-1003",
  weight_grams := <logistics::packable_grams>2000,
  tier := logistics::shipping_tier.Overnight,
  insured := false,
  carrier := (select Carrier filter .name = "Halcyon" limit 1)
};

insert Parcel {
  sku := <logistics::sku_code>"SKU-1004",
  weight_grams := <logistics::packable_grams>50,
  tier := logistics::shipping_tier.Ground,
  insured := false,
  handling_hint := "sample",
  carrier := (select Carrier filter .name = "Halcyon" limit 1)
};

insert Shipment {
  code := "SHP-9001",
  parcels := (select Parcel filter .sku in {"SKU-1001", "SKU-1002", "SKU-1003"})
};

insert Shipment {
  code := "SHP-9002"
};
