select Batch {
  code,
  kilograms,
  harvested_on,
  certifications,
  grower: {
    slug,
    name,
    region: {
      code,
      name,
    },
  },
  inspection_count := count(Batch.<batch[is Inspection]),
}
filter .code = <str>$code
