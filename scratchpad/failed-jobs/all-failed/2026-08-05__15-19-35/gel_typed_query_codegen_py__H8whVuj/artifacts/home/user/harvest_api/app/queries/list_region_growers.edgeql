select Grower {
  slug,
  name,
  region: {
    code,
    name,
  },
  batches := (
    select Grower.<grower[is Batch] {
      code,
      kilograms,
      harvested_on,
      certifications,
    }
    filter
      (not exists <optional float64>$min_kilograms
        or .kilograms >= <optional float64>$min_kilograms)
      and (len(<array<str>>$certifications) = 0
        or exists (.certifications intersect array_unpack(<array<str>>$certifications)))
    order by .code
  ),
}
filter .region.code = <str>$region_code
order by .slug
