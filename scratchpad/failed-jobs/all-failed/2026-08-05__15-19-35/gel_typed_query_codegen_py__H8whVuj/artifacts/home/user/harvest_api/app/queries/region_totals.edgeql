select Region {
  code,
  name,
  grower_count := count(Region.<region[is Grower]),
  batch_count := count(Region.<region[is Grower].<grower[is Batch]),
  total_kilograms := sum(Region.<region[is Grower].<grower[is Batch].kilograms),
}
filter len(<array<str>>$region_codes) = 0 or .code in array_unpack(<array<str>>$region_codes)
order by .code
