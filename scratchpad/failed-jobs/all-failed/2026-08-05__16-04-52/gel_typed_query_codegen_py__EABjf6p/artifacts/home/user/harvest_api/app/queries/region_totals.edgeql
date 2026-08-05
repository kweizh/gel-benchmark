# Aggregate region totals.
# Parameter:
#   $region_codes: array<str> - region codes to include (empty means all)
with
  target_regions := (
    select Region
    filter
      len(<array<str>>$region_codes) = 0
      or .code in array_unpack(<array<str>>$region_codes)
  )
select target_regions {
  code,
  name,
  grower_count := count(.<region[is Grower]),
  batch_count := count(.<region[is Grower].<grower[is Batch]),
  total_kilograms := sum(.<region[is Grower].<grower[is Batch].kilograms)
}
order by .code
