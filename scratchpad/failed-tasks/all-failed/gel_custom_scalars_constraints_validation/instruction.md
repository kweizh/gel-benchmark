# Gel: Schema-Enforced Validation for a Clinical Measurement Registry

## Background
A clinical lab needs a measurement registry whose domain rules live in the database itself instead of in application code. Model the registry in a local Gel 6 instance and expose a thin Python layer that turns database rejections into a stable validation-error payload for an upstream service.

The container ships a local Gel 6 server, the `gel` CLI, and the Python `gel` package. The server is started by the idempotent helper `/usr/local/bin/gel-start`, which is safe to re-run at any time and returns once the server accepts connections. Connection settings are preconfigured through environment variables, so `gel` CLI commands and `gel.create_client()` need no extra configuration. There is no network access; everything runs against that local server.

## Requirements

### 1. Schema
Declare everything in module `default` in `dbschema/default.gel`.

Custom scalar types (exact names):
- `SpecimenCode` extending `str` — accepts only the literal `SPC-`, then exactly 6 digits, then `-`, then exactly 2 uppercase ASCII letters (e.g. `SPC-000123-AB`), and nothing else before or after.
- `AnalyteCode` extending `str` — accepts only strings 3 to 8 characters long whose first character is an uppercase ASCII letter and whose remaining characters are uppercase ASCII letters or digits.
- `MeasuredValue` extending `float64` — accepts only values `>= 0.0` and `<= 100000.0`.
- `Unit` — an enum with exactly the labels `mg_per_dL`, `mmol_per_L`, `g_per_L`, `IU_per_L`, in that order.
- `ReviewState` — an enum with exactly the labels `pending`, `validated`, `rejected`, in that order.

Reusable validation:
- Exactly one **abstract** constraint named `clean_label`, parameterised by a maximum length. It accepts a string only when the string is non-empty, has no leading or trailing whitespace, and is no longer than the given maximum length. Both label properties below must be validated through this one abstract constraint.

Object types (exact type, property and link names):
- abstract `Sample` — required `specimen_code: SpecimenCode`, required `label: str` validated by `clean_label` with maximum length 40, required `volume_ml: float64`.
- `BloodSample` extending `Sample` — required `tube_count: int16` restricted to 1..6 inclusive.
- `UrineSample` extending `Sample`.
- `Measurement` — required link `sample: Sample`, required `analyte: AnalyteCode`, required `value: MeasuredValue`, required `unit: Unit`, required `state: ReviewState` whose default is `pending`, required `ref_low: float64`, required `ref_high: float64`, required `label: str` validated by `clean_label` with maximum length 80.

Rules that the database itself must enforce. When a rule is broken, the error message reported by the database must be exactly the text in the right-hand column, character for character:

| rule that must be rejected | error message |
| --- | --- |
| a `specimen_code` that does not match the `SpecimenCode` format | `invalid specimen code` |
| two samples **of the same concrete object type** carrying the same `specimen_code` — while the same `specimen_code` on one `BloodSample` and one `UrineSample` must be accepted | `specimen code already registered` |
| a `label` that breaks the `clean_label` rules | `malformed label` |
| a `volume_ml` that is not strictly greater than `0.0` | `volume must be positive` |
| a `BloodSample` with `volume_ml` greater than `10.0` | `blood volume exceeds 10 ml` |
| a `UrineSample` with `volume_ml` greater than `500.0` | `urine volume exceeds 500 ml` |
| a `tube_count` outside 1..6 | `tube count out of range` |
| an `analyte` that does not match the `AnalyteCode` format | `invalid analyte code` |
| a `value` below `0.0` | `value must not be negative` |
| a `value` above `100000.0` | `value exceeds instrument ceiling` |
| a `ref_low` that is not strictly less than `ref_high` | `reference interval not ascending` |
| a `Measurement` whose `state` is `validated` and whose `value` lies outside the inclusive interval `[ref_low, ref_high]`; measurements in any other state are unrestricted in this respect | `validated value outside reference interval` |
| two `Measurement` objects sharing the same `sample` and the same `analyte` | `duplicate analyte for sample` |

Every rule above must hold for plain EdgeQL statements run against the instance, not only for writes going through the Python layer.

### 2. Migrations
The schema must reach the running instance through the project's migration history (files under `dbschema/migrations/`), and `gel migration status` must report that the database is up to date with the local schema.

### 3. Python validation layer
Create an importable package `labreg` inside the project containing the module `labreg/validation.py`, which exposes exactly these two synchronous functions:

- `register_sample(client, payload)`
- `submit_measurement(client, payload)`

`client` is a blocking Gel client (the kind returned by `gel.create_client()`) that is created and closed by the caller and may be reused across many calls. `payload` is a `dict`.

`register_sample` required payload keys, in this order: `kind` (`"blood"` or `"urine"`), `specimen_code`, `label`, `volume_ml`, plus `tube_count` when `kind` is `"blood"`.

`submit_measurement` required payload keys, in this order: `sample_id`, `analyte`, `value`, `unit`, `state`, `ref_low`, `ref_high`, `label`. `sample_id` is the `str` form of an existing `Sample` id, and `unit`/`state` are enum label strings.

On success a function persists exactly one object and returns `{"ok": True, "id": <str id of the created object>}`.

On rejection a function must return `{"ok": False, "error": {"code": <str>, "field": <str>, "message": <str>}}`, must never raise, and must leave the database unchanged (no partially created objects).

`register_sample` rejection payloads:

| code | field | message |
| --- | --- | --- |
| `missing_field` | the missing key | `missing required field` |
| `invalid_kind` | `kind` | `unknown sample kind` |
| `invalid_specimen_code` | `specimen_code` | `invalid specimen code` |
| `duplicate_specimen_code` | `specimen_code` | `specimen code already registered` |
| `malformed_label` | `label` | `malformed label` |
| `volume_not_positive` | `volume_ml` | `volume must be positive` |
| `volume_above_kind_limit` | `volume_ml` | `blood volume exceeds 10 ml` for a blood sample, `urine volume exceeds 500 ml` for a urine sample |
| `tube_count_out_of_range` | `tube_count` | `tube count out of range` |

`submit_measurement` rejection payloads:

| code | field | message |
| --- | --- | --- |
| `missing_field` | the missing key | `missing required field` |
| `sample_not_found` | `sample_id` | `unknown sample` |
| `invalid_analyte_code` | `analyte` | `invalid analyte code` |
| `value_negative` | `value` | `value must not be negative` |
| `value_above_ceiling` | `value` | `value exceeds instrument ceiling` |
| `invalid_unit` | `unit` | `invalid unit` |
| `invalid_state` | `state` | `invalid review state` |
| `malformed_label` | `label` | `malformed label` |
| `interval_not_ascending` | `ref_high` | `reference interval not ascending` |
| `value_outside_reference` | `value` | `validated value outside reference interval` |
| `duplicate_analyte` | `analyte` | `duplicate analyte for sample` |

Resolution rules:
- Absent required keys are reported before every other check; when several required keys are absent, report the first one in the key order listed above.
- Apart from that, every rejected payload used during evaluation breaks exactly one rule.
- A `sample_id` that does not identify an existing `Sample`, including a string that is not a valid UUID, yields `sample_not_found`.
- A `unit` or `state` string that is not one of the enum labels yields `invalid_unit` or `invalid_state`.

## Implementation Hints
- Project path: `/home/user/labreg`
- Schema file: `/home/user/labreg/dbschema/default.gel`; migration files: `/home/user/labreg/dbschema/migrations/`
- Python module file: `/home/user/labreg/labreg/validation.py`, imported as `labreg.validation` with `/home/user/labreg` on `sys.path`.
- The instance must stay reachable with the preconfigured settings: `gel` CLI commands run inside the project directory without connection flags and `gel.create_client()` called with no arguments must both keep working.
- A payload key that is present but holds an empty or blank string counts as present, not as missing.
- Keep the stored data set tiny; the container has ~4 GB of RAM and few CPUs.

