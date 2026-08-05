with
  batch := (select Batch filter .code = <str>$batch_code),
  new_inspection := (
    insert Inspection {
      batch := batch,
      inspector := <str>$inspector,
      passed := <bool>$passed,
      defects := (
        for defect_code in array_unpack(<array<str>>$defect_codes) union (
          insert Defect {
            code := defect_code,
            severity := <int64>$severity,
          }
        )
      ),
    }
  )
select {
  id := new_inspection.id,
  inspector := new_inspection.inspector,
  passed := new_inspection.passed,
  defect_count := count(new_inspection.defects),
  batch_code := batch.code,
}
