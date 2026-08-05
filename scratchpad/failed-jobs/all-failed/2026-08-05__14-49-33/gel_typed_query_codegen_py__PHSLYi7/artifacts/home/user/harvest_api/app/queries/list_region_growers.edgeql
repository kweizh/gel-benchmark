select Grower {
  slug,
  name,
  region: {
    code,
    name
  },
  batches := (
    select .<grower[is Batch] {
      code,
      kilograms,
      harvested_on,
      certifications
    }
    filter (
      ((.kilograms >= <optional float64>$min_kilograms) ?? true) and
      ((not exists array_unpack(<array<str>>$certifications)) or any(array_unpack(<array<str>>$certifications) in .certifications))
    )
    order by .code asc
  )
}
filter .region.code = <str>$region_code
order by .slug asc;
