# Record an inspection for a batch with defects.
# Parameters:
#   $batch_code: str - the batch code
#   $inspector: str - the inspector name
#   $passed: bool - whether the inspection passed
#   $defect_codes: array<str> - defect codes to create
#   $severity: int64 - severity for each defect
with
  batch := (select Batch filter .code = <str>$batch_code),
  inspection := (
    insert Inspection {
      batch := batch,
      inspector := <str>$inspector,
      passed := <bool>$passed,
      defects := (
        for code in array_unpack(<array<str>>$defect_codes)
        union (
          insert Defect {
            code := code,
            severity := <int64>$severity
          }
        )
      )
    }
  )
select inspection {
  id,
  inspector,
  passed,
  defect_count := count(.defects),
  batch_code := .batch.code
}
