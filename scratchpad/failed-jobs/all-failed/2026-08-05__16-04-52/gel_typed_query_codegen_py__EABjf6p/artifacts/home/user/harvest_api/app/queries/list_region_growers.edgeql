# List all growers in a region with their matching batches.
# Parameters:
#   $region_code: str - the region code to filter by
#   $min_kilograms: optional float64 - minimum kilograms filter (optional)
#   $certifications: array<str> - certifications filter (empty means no filter)
with
  certs := array_unpack(<array<str>>$certifications)
select Grower {
  slug,
  name,
  region: { code, name },
  batches := (
    select .<grower[is Batch] {
      code,
      kilograms,
      harvested_on,
      certifications
    }
    filter
      (.kilograms >= (<optional float64>$min_kilograms if exists <optional float64>$min_kilograms else 0.0))
      and
      (len(<array<str>>$certifications) = 0
       or exists (.certifications intersect certs))
    order by .code
  )
}
filter .region.code = <str>$region_code
order by .slug
