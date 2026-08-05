select Region {
  code,
  name,
  grower_count := count(.<region[is Grower]),
  batch_count := count(.<region[is Grower].<grower[is Batch]),
  total_kilograms := sum(.<region[is Grower].<grower[is Batch].kilograms)
}
filter (
  (not exists array_unpack(<array<str>>$region_codes)) or
  (.code in array_unpack(<array<str>>$region_codes))
)
order by .code asc;
