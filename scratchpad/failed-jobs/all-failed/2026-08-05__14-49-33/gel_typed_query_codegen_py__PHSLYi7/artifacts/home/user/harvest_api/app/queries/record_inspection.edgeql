select (
  insert Inspection {
    batch := (select Batch filter .code = <str>$batch_code limit 1),
    inspector := <str>$inspector,
    passed := <bool>$passed,
    defects := (
      for x in array_unpack(<array<str>>$defect_codes)
      union (
        insert Defect {
          code := x,
          severity := <int64>$severity
        }
      )
    )
  }
) {
  id,
  inspector,
  passed,
  defect_count := count(.defects),
  batch_code := .batch.code
};
