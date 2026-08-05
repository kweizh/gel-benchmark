"""Final-state verification for gel_custom_scalars_constraints_validation.

The suite talks to the real local Gel instance: plain EdgeQL statements prove
that the domain rules are enforced by the schema itself, and the executor's
Python module is imported and driven against the same live database.
"""

import glob
import importlib
import itertools
import json
import os
import subprocess
import sys
import time
import uuid

import pytest

PROJECT_DIR = "/home/user/labreg"
MIGRATIONS_DIR = os.path.join(PROJECT_DIR, "dbschema", "migrations")
GEL_START = "/usr/local/bin/gel-start"

_CODES = itertools.count(1)


def _next_code(suffix="AA"):
    """Return a fresh, format-valid specimen code."""
    return "SPC-{:06d}-{}".format(next(_CODES), suffix)


def _run(args, cwd=PROJECT_DIR, timeout=300):
    return subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
    )


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="session")
def gel_server():
    """Guarantee the local Gel server is up before anything else runs."""
    if os.path.isfile(GEL_START):
        _run([GEL_START], cwd="/", timeout=300)
    deadline = time.time() + 180.0
    last = ""
    while time.time() < deadline:
        probe = _run(["gel", "query", "select 1"])
        if probe.returncode == 0 and "1" in probe.stdout:
            return True
        last = probe.stdout + probe.stderr
        time.sleep(3.0)
    pytest.fail(f"The local Gel server never became reachable. Last output: {last}")


@pytest.fixture(scope="session")
def client(gel_server):
    """A blocking Gel client connected to the project's instance."""
    import gel  # noqa: PLC0415

    os.chdir(PROJECT_DIR)
    c = gel.create_client()
    c.ensure_connected()
    # Start from a clean data set so the suite is deterministic and re-runnable.
    c.execute("delete Measurement")
    c.execute("delete Sample")
    yield c
    c.close()


@pytest.fixture(scope="session")
def validation(client):
    """Import the executor's validation module from the project."""
    module_path = os.path.join(PROJECT_DIR, "labreg", "validation.py")
    assert os.path.isfile(module_path), f"Expected the module {module_path} to exist."
    if PROJECT_DIR not in sys.path:
        sys.path.insert(0, PROJECT_DIR)
    module = importlib.import_module("labreg.validation")
    for func in ("register_sample", "submit_measurement"):
        assert hasattr(module, func), (
            f"labreg.validation must expose a `{func}` function."
        )
        assert callable(getattr(module, func)), f"labreg.validation.{func} is not callable."
    return module


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _count(client, type_name):
    return client.query_single(f"select count({type_name})")


def _introspect(client, query, **kwargs):
    return json.loads(client.query_json(query, **kwargs))


def _expect_db_rejection(client, statement, expected_message):
    """Run raw EdgeQL and require the database to reject it with `expected_message`."""
    try:
        client.execute(statement)
    except Exception as exc:  # noqa: BLE001 - any Gel error is acceptable here
        text = str(exc)
        assert expected_message in text, (
            "The database rejected the statement but with the wrong message.\n"
            f"expected to contain: {expected_message!r}\ngot: {text!r}\n"
            f"statement: {statement}"
        )
        return
    pytest.fail(
        "The database accepted a statement that must be rejected with "
        f"{expected_message!r}: {statement}"
    )


def _expect_db_accepted(client, statement):
    try:
        client.execute(statement)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"The database rejected a valid statement ({exc}): {statement}")


def _blood_insert(code, label="'draw'", volume=5.0, tube=1):
    return (
        "insert BloodSample {{ specimen_code := '{code}', label := {label}, "
        "volume_ml := {volume}, tube_count := {tube} }}"
    ).format(code=code, label=label, volume=volume, tube=tube)


def _urine_insert(code, label="'collection'", volume=250.0):
    return (
        "insert UrineSample {{ specimen_code := '{code}', label := {label}, "
        "volume_ml := {volume} }}"
    ).format(code=code, label=label, volume=volume)


def _new_blood_sample(client, volume=5.0, tube=1):
    code = _next_code()
    return client.query_single(
        "select (" + _blood_insert(code, label="'draw'", volume=volume, tube=tube) + ").id"
    )


def _measurement_insert(
    sample_id,
    analyte="GLU",
    value=4.0,
    unit="Unit.mg_per_dL",
    state=None,
    ref_low=1.0,
    ref_high=10.0,
    label="'reading'",
):
    state_part = "" if state is None else f", state := {state}"
    return (
        "insert Measurement {{ "
        "sample := assert_exists((select Sample filter .id = <uuid>'{sid}')), "
        "analyte := '{analyte}', value := {value}, unit := {unit}{state_part}, "
        "ref_low := {ref_low}, ref_high := {ref_high}, label := {label} }}"
    ).format(
        sid=sample_id,
        analyte=analyte,
        value=value,
        unit=unit,
        state_part=state_part,
        ref_low=ref_low,
        ref_high=ref_high,
        label=label,
    )


def _call(func, client, payload):
    try:
        return func(client, dict(payload))
    except Exception as exc:  # noqa: BLE001
        pytest.fail(
            f"{func.__name__} raised {type(exc).__name__}: {exc} for payload {payload!r}; "
            "it must return a validation-error payload instead of raising."
        )


def _assert_accepted(client, func, payload, type_name):
    before = _count(client, type_name)
    result = _call(func, client, payload)
    assert isinstance(result, dict), f"{func.__name__} must return a dict, got {result!r}"
    assert result.get("ok") is True, (
        f"{func.__name__} must accept {payload!r}, got {result!r}"
    )
    new_id = result.get("id")
    assert isinstance(new_id, str) and new_id, (
        f"{func.__name__} must return the new object id as a str, got {result!r}"
    )
    try:
        stored = client.query_single(
            f"select count((select {type_name} filter .id = <uuid><str>$oid))", oid=new_id
        )
    except Exception as exc:  # noqa: BLE001
        pytest.fail(
            f"The id returned by {func.__name__} ({new_id!r}) is not a usable object "
            f"id: {exc}"
        )
    assert stored == 1, (
        f"The id returned by {func.__name__} ({new_id!r}) does not identify a stored "
        f"{type_name} object."
    )
    assert _count(client, type_name) == before + 1, (
        f"{func.__name__} must persist exactly one {type_name} object per accepted call."
    )
    return new_id


def _assert_rejected(client, func, payload, code, field, message, type_name):
    before = _count(client, type_name)
    result = _call(func, client, payload)
    assert isinstance(result, dict), f"{func.__name__} must return a dict, got {result!r}"
    assert result.get("ok") is False, (
        f"{func.__name__} must reject {payload!r}, got {result!r}"
    )
    error = result.get("error")
    assert isinstance(error, dict), (
        f"{func.__name__} must return an `error` dict on rejection, got {result!r}"
    )
    assert error.get("code") == code, (
        f"Wrong error code for {payload!r}: expected {code!r}, got {error.get('code')!r}"
    )
    assert error.get("field") == field, (
        f"Wrong error field for {payload!r}: expected {field!r}, got {error.get('field')!r}"
    )
    assert error.get("message") == message, (
        f"Wrong error message for {payload!r}: expected {message!r}, got "
        f"{error.get('message')!r}"
    )
    assert _count(client, type_name) == before, (
        f"{func.__name__} must not persist anything for the rejected payload {payload!r}."
    )


def _sample_payload(kind="blood", **overrides):
    payload = {
        "kind": kind,
        "specimen_code": _next_code(),
        "label": "morning draw",
        "volume_ml": 4.5 if kind == "blood" else 250.0,
    }
    if kind == "blood":
        payload["tube_count"] = 2
    payload.update(overrides)
    return payload


def _measurement_payload(sample_id, **overrides):
    payload = {
        "sample_id": str(sample_id),
        "analyte": "GLU",
        "value": 5.4,
        "unit": "mmol_per_L",
        "state": "pending",
        "ref_low": 3.9,
        "ref_high": 5.8,
        "label": "fasting glucose",
    }
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------- #
# A. schema shape
# --------------------------------------------------------------------------- #


def test_custom_scalar_types_exist(client):
    rows = _introspect(
        client,
        "select schema::ScalarType { name } filter .name in "
        "{'default::SpecimenCode', 'default::AnalyteCode', 'default::MeasuredValue', "
        "'default::Unit', 'default::ReviewState'}",
    )
    names = sorted(row["name"] for row in rows)
    assert names == [
        "default::AnalyteCode",
        "default::MeasuredValue",
        "default::ReviewState",
        "default::SpecimenCode",
        "default::Unit",
    ], f"Missing or misnamed custom scalar types, found: {names}"


def test_enum_labels_are_exact_and_ordered(client):
    rows = _introspect(
        client,
        "select schema::ScalarType { name, enum_values } filter .name in "
        "{'default::Unit', 'default::ReviewState'}",
    )
    labels = {row["name"]: row["enum_values"] for row in rows}
    assert labels.get("default::Unit") == [
        "mg_per_dL",
        "mmol_per_L",
        "g_per_L",
        "IU_per_L",
    ], f"Unit enum labels are wrong: {labels.get('default::Unit')!r}"
    assert labels.get("default::ReviewState") == [
        "pending",
        "validated",
        "rejected",
    ], f"ReviewState enum labels are wrong: {labels.get('default::ReviewState')!r}"


def test_abstract_constraint_clean_label_exists(client):
    rows = _introspect(
        client,
        "select schema::Constraint { name } filter .abstract and "
        ".name = 'default::clean_label'",
    )
    assert len(rows) == 1, (
        "Expected exactly one abstract constraint named default::clean_label, found: "
        f"{rows!r}"
    )


def test_object_types_and_property_targets(client):
    rows = _introspect(
        client,
        """
        select schema::ObjectType {
          name,
          abstract,
          ancestor_names := (select .ancestors.name),
          props := (select .properties { name, required, tgt := .target.name }),
          lnks := (select .links { name, required, tgt := .target.name })
        }
        filter .name in {'default::Sample', 'default::BloodSample',
                         'default::UrineSample', 'default::Measurement'}
        """,
    )
    types = {row["name"]: row for row in rows}
    for expected in (
        "default::Sample",
        "default::BloodSample",
        "default::UrineSample",
        "default::Measurement",
    ):
        assert expected in types, f"Object type {expected} is missing from the schema."

    assert types["default::Sample"]["abstract"] is True, (
        "default::Sample must be an abstract object type."
    )
    for subtype in ("default::BloodSample", "default::UrineSample"):
        assert "default::Sample" in types[subtype]["ancestor_names"], (
            f"{subtype} must extend default::Sample."
        )

    sample_props = {p["name"]: p for p in types["default::Sample"]["props"]}
    assert sample_props.get("specimen_code", {}).get("tgt") == "default::SpecimenCode", (
        "Sample.specimen_code must be typed as default::SpecimenCode, got "
        f"{sample_props.get('specimen_code')!r}"
    )
    for name in ("specimen_code", "label", "volume_ml"):
        assert sample_props.get(name, {}).get("required") is True, (
            f"Sample.{name} must be a required property."
        )

    blood_props = {p["name"]: p for p in types["default::BloodSample"]["props"]}
    assert blood_props.get("tube_count", {}).get("required") is True, (
        "BloodSample.tube_count must be a required property."
    )

    m_props = {p["name"]: p for p in types["default::Measurement"]["props"]}
    expected_targets = {
        "analyte": "default::AnalyteCode",
        "value": "default::MeasuredValue",
        "unit": "default::Unit",
        "state": "default::ReviewState",
        "ref_low": "std::float64",
        "ref_high": "std::float64",
        "label": "std::str",
    }
    for name, target in expected_targets.items():
        assert name in m_props, f"Measurement.{name} is missing."
        assert m_props[name]["tgt"] == target, (
            f"Measurement.{name} must target {target}, got {m_props[name]['tgt']}"
        )
        assert m_props[name]["required"] is True, (
            f"Measurement.{name} must be a required property."
        )

    m_links = {link["name"]: link for link in types["default::Measurement"]["lnks"]}
    assert m_links.get("sample", {}).get("tgt") == "default::Sample", (
        f"Measurement.sample must be a link to default::Sample, got {m_links.get('sample')!r}"
    )
    assert m_links["sample"]["required"] is True, "Measurement.sample must be required."


def test_migration_files_exist_and_history_applied(client):
    files = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql")))
    assert files, f"No migration files were created under {MIGRATIONS_DIR}."
    applied = _count(client, "schema::Migration")
    assert applied >= 1, "The database migration history is empty."


def test_migration_status_reports_in_sync(client):
    proc = _run(["gel", "migration", "status"])
    combined = (proc.stdout + proc.stderr).lower()
    assert proc.returncode == 0, f"`gel migration status` failed: {proc.stdout} {proc.stderr}"
    assert "up to date" in combined, (
        f"`gel migration status` does not report an in-sync database: {proc.stdout} {proc.stderr}"
    )


# --------------------------------------------------------------------------- #
# B. database-level enforcement (plain EdgeQL, no Python layer involved)
# --------------------------------------------------------------------------- #


def test_specimen_code_format_enforced_by_database(client):
    for bad in ("SPC-12345-AB", "spc-000001-ab", "SPC-000001-ABC", " SPC-000001-AB",
                "SPC-000001-A1", "XPC-000001-AB"):
        _expect_db_rejection(
            client, _blood_insert(bad, label="'draw'"), "invalid specimen code"
        )
    _expect_db_accepted(client, _blood_insert(_next_code(), label="'draw'"))


def test_specimen_code_exclusivity_is_delegated_to_subtypes(client):
    code = _next_code()
    _expect_db_accepted(client, _blood_insert(code, label="'first'"))
    _expect_db_rejection(
        client, _blood_insert(code, label="'second'"), "specimen code already registered"
    )
    # The same code on another concrete sample type must be allowed.
    _expect_db_accepted(client, _urine_insert(code))
    _expect_db_rejection(
        client, _urine_insert(code, label="'dup urine'"), "specimen code already registered"
    )


def test_label_rules_enforced_by_database(client):
    for bad_label in ("''", "' padded'", "'padded '", "str_repeat('x', 41)", "'   '"):
        _expect_db_rejection(
            client, _blood_insert(_next_code(), label=bad_label), "malformed label"
        )
    _expect_db_accepted(client, _blood_insert(_next_code(), label="str_repeat('x', 40)"))


def test_volume_must_be_positive(client):
    for volume in (0.0, -1.0):
        _expect_db_rejection(
            client,
            _blood_insert(_next_code(), label="'draw'", volume=volume),
            "volume must be positive",
        )
        _expect_db_rejection(
            client,
            _urine_insert(_next_code(), volume=volume),
            "volume must be positive",
        )


def test_blood_volume_limit_enforced(client):
    _expect_db_rejection(
        client,
        _blood_insert(_next_code(), label="'draw'", volume=10.5),
        "blood volume exceeds 10 ml",
    )
    _expect_db_accepted(client, _blood_insert(_next_code(), label="'draw'", volume=10.0))


def test_urine_volume_limit_enforced(client):
    _expect_db_rejection(
        client, _urine_insert(_next_code(), volume=500.5), "urine volume exceeds 500 ml"
    )
    _expect_db_accepted(client, _urine_insert(_next_code(), volume=500.0))
    # The blood-specific limit must not leak into UrineSample.
    _expect_db_accepted(client, _urine_insert(_next_code(), volume=100.0))


def test_tube_count_range_enforced(client):
    for bad in (0, 7, -3, 99):
        _expect_db_rejection(
            client,
            _blood_insert(_next_code(), label="'draw'", tube=bad),
            "tube count out of range",
        )
    for good in (1, 6):
        _expect_db_accepted(
            client, _blood_insert(_next_code(), label="'draw'", tube=good)
        )


def test_analyte_code_format_enforced_by_database(client):
    sample_id = _new_blood_sample(client)
    for bad in ("gl", "GL", "GLUCOSE12", "1AB", "GL_U", "GLu"):
        _expect_db_rejection(
            client,
            _measurement_insert(sample_id, analyte=bad),
            "invalid analyte code",
        )
    for good in ("GLU", "GLUCOSE1", "A1B"):
        _expect_db_accepted(client, _measurement_insert(sample_id, analyte=good))


def test_measured_value_bounds_enforced_by_database(client):
    sample_id = _new_blood_sample(client)
    _expect_db_rejection(
        client,
        _measurement_insert(sample_id, analyte="LOW", value=-0.5, ref_low=-1.0, ref_high=10.0),
        "value must not be negative",
    )
    _expect_db_rejection(
        client,
        _measurement_insert(
            sample_id, analyte="HIGH", value=100000.5, ref_low=1.0, ref_high=200000.0
        ),
        "value exceeds instrument ceiling",
    )
    _expect_db_accepted(
        client,
        _measurement_insert(sample_id, analyte="ZERO", value=0.0, ref_low=0.0, ref_high=10.0),
    )
    _expect_db_accepted(
        client,
        _measurement_insert(
            sample_id, analyte="CEIL", value=100000.0, ref_low=1.0, ref_high=200000.0
        ),
    )


def test_reference_interval_must_be_ascending(client):
    sample_id = _new_blood_sample(client)
    _expect_db_rejection(
        client,
        _measurement_insert(sample_id, analyte="EQU", value=5.0, ref_low=5.0, ref_high=5.0),
        "reference interval not ascending",
    )
    _expect_db_rejection(
        client,
        _measurement_insert(sample_id, analyte="DESC", value=5.0, ref_low=9.0, ref_high=4.0),
        "reference interval not ascending",
    )
    _expect_db_accepted(
        client,
        _measurement_insert(sample_id, analyte="ASC", value=5.0, ref_low=4.0, ref_high=9.0),
    )


def test_validated_measurement_must_sit_inside_reference_interval(client):
    sample_id = _new_blood_sample(client)
    _expect_db_rejection(
        client,
        _measurement_insert(
            sample_id,
            analyte="OUT",
            value=12.0,
            state="ReviewState.validated",
            ref_low=1.0,
            ref_high=10.0,
        ),
        "validated value outside reference interval",
    )
    for state in ("ReviewState.pending", "ReviewState.rejected"):
        _expect_db_accepted(
            client,
            _measurement_insert(
                sample_id,
                analyte="OK" + state.split(".")[1][:3].upper(),
                value=12.0,
                state=state,
                ref_low=1.0,
                ref_high=10.0,
            ),
        )
    for value, analyte in ((1.0, "LOWB"), (10.0, "HIGHB")):
        _expect_db_accepted(
            client,
            _measurement_insert(
                sample_id,
                analyte=analyte,
                value=value,
                state="ReviewState.validated",
                ref_low=1.0,
                ref_high=10.0,
            ),
        )


def test_analyte_is_unique_per_sample(client):
    first = _new_blood_sample(client)
    second = _new_blood_sample(client)
    _expect_db_accepted(client, _measurement_insert(first, analyte="NAX"))
    _expect_db_rejection(
        client,
        _measurement_insert(first, analyte="NAX", label="'duplicate'"),
        "duplicate analyte for sample",
    )
    # Same analyte on a different sample and another analyte on the same sample are fine.
    _expect_db_accepted(client, _measurement_insert(second, analyte="NAX"))
    _expect_db_accepted(client, _measurement_insert(first, analyte="KAX"))


def test_measurement_state_defaults_to_pending(client):
    sample_id = _new_blood_sample(client)
    row = client.query_single(
        "select ("
        + _measurement_insert(sample_id, analyte="DEF", value=4.0)
        + ") { state_label := <str>.state }"
    )
    assert row.state_label == "pending", (
        f"Measurement.state must default to 'pending', got {row.state_label!r}"
    )


def test_unknown_enum_labels_rejected_by_database(client):
    sample_id = _new_blood_sample(client)
    for statement, label in (
        (_measurement_insert(sample_id, analyte="UEN", unit="<Unit>'kg_per_L'"), "kg_per_L"),
        (
            _measurement_insert(
                sample_id, analyte="USN", state="<ReviewState>'approved'"
            ),
            "approved",
        ),
    ):
        try:
            client.execute(statement)
        except Exception as exc:  # noqa: BLE001
            assert label in str(exc), (
                f"Expected the database to complain about {label!r}, got: {exc}"
            )
            continue
        pytest.fail(f"The database accepted the unknown enum label {label!r}.")


# --------------------------------------------------------------------------- #
# C. register_sample happy paths
# --------------------------------------------------------------------------- #


def test_register_sample_blood_happy_path(client, validation):
    payload = {
        "kind": "blood",
        "specimen_code": "SPC-100001-AA",
        "label": "morning draw",
        "volume_ml": 4.5,
        "tube_count": 2,
    }
    new_id = _assert_accepted(client, validation.register_sample, payload, "Sample")
    row = client.query_single(
        """
        select Sample {
          specimen_code,
          label,
          volume_ml,
          type_name := .__type__.name
        } filter .id = <uuid><str>$oid
        """,
        oid=new_id,
    )
    assert row.type_name == "default::BloodSample", (
        f"A blood payload must create a BloodSample, got {row.type_name}"
    )
    assert row.specimen_code == "SPC-100001-AA", f"Wrong specimen_code stored: {row.specimen_code!r}"
    assert row.label == "morning draw", f"Wrong label stored: {row.label!r}"
    assert abs(row.volume_ml - 4.5) < 1e-9, f"Wrong volume_ml stored: {row.volume_ml!r}"
    tubes = client.query_single(
        "select (select BloodSample filter .id = <uuid><str>$oid).tube_count", oid=new_id
    )
    assert tubes == 2, f"Wrong tube_count stored: {tubes!r}"


def test_register_sample_same_code_other_kind_accepted(client, validation):
    payload = {
        "kind": "urine",
        "specimen_code": "SPC-100001-AA",
        "label": "24h collection",
        "volume_ml": 250.0,
    }
    new_id = _assert_accepted(client, validation.register_sample, payload, "Sample")
    row = client.query_single(
        "select Sample { type_name := .__type__.name } filter .id = <uuid><str>$oid",
        oid=new_id,
    )
    assert row.type_name == "default::UrineSample", (
        f"A urine payload must create a UrineSample, got {row.type_name}"
    )


def test_register_sample_boundary_values_accepted(client, validation):
    cases = [
        _sample_payload("blood", volume_ml=10.0),
        _sample_payload("blood", tube_count=1),
        _sample_payload("blood", tube_count=6),
        _sample_payload("blood", label="x" * 40),
        _sample_payload("urine", volume_ml=500.0),
        _sample_payload("urine", volume_ml=0.1),
    ]
    for payload in cases:
        _assert_accepted(client, validation.register_sample, payload, "Sample")


# --------------------------------------------------------------------------- #
# D. register_sample rejections
# --------------------------------------------------------------------------- #


def test_register_sample_missing_fields(client, validation):
    payload = _sample_payload("blood")
    payload.pop("label")
    _assert_rejected(
        client, validation.register_sample, payload, "missing_field", "label",
        "missing required field", "Sample",
    )

    payload = _sample_payload("blood")
    payload.pop("specimen_code")
    payload.pop("label")
    _assert_rejected(
        client, validation.register_sample, payload, "missing_field", "specimen_code",
        "missing required field", "Sample",
    )

    payload = _sample_payload("blood")
    payload.pop("tube_count")
    _assert_rejected(
        client, validation.register_sample, payload, "missing_field", "tube_count",
        "missing required field", "Sample",
    )


def test_register_sample_invalid_kind(client, validation):
    payload = _sample_payload("blood")
    payload["kind"] = "saliva"
    _assert_rejected(
        client, validation.register_sample, payload, "invalid_kind", "kind",
        "unknown sample kind", "Sample",
    )


def test_register_sample_invalid_specimen_code(client, validation):
    for bad in ("SPC-1-AB", "spc-000001-ab", "SPC-000001-abc", "SPC-000001-AB "):
        payload = _sample_payload("blood", specimen_code=bad)
        _assert_rejected(
            client, validation.register_sample, payload, "invalid_specimen_code",
            "specimen_code", "invalid specimen code", "Sample",
        )


def test_register_sample_duplicate_specimen_code(client, validation):
    payload = _sample_payload("blood")
    _assert_accepted(client, validation.register_sample, payload, "Sample")
    _assert_rejected(
        client, validation.register_sample, dict(payload), "duplicate_specimen_code",
        "specimen_code", "specimen code already registered", "Sample",
    )


def test_register_sample_malformed_label(client, validation):
    for bad in ("  spaced", "spaced  ", " ", "x" * 41):
        payload = _sample_payload("blood", label=bad)
        _assert_rejected(
            client, validation.register_sample, payload, "malformed_label", "label",
            "malformed label", "Sample",
        )


def test_register_sample_volume_not_positive(client, validation):
    for kind in ("blood", "urine"):
        payload = _sample_payload(kind, volume_ml=0.0)
        _assert_rejected(
            client, validation.register_sample, payload, "volume_not_positive",
            "volume_ml", "volume must be positive", "Sample",
        )
        payload = _sample_payload(kind, volume_ml=-2.5)
        _assert_rejected(
            client, validation.register_sample, payload, "volume_not_positive",
            "volume_ml", "volume must be positive", "Sample",
        )


def test_register_sample_volume_above_kind_limit(client, validation):
    _assert_rejected(
        client, validation.register_sample, _sample_payload("blood", volume_ml=12.0),
        "volume_above_kind_limit", "volume_ml", "blood volume exceeds 10 ml", "Sample",
    )
    _assert_rejected(
        client, validation.register_sample, _sample_payload("urine", volume_ml=900.0),
        "volume_above_kind_limit", "volume_ml", "urine volume exceeds 500 ml", "Sample",
    )


def test_register_sample_tube_count_out_of_range(client, validation):
    for bad in (0, 9):
        payload = _sample_payload("blood", tube_count=bad)
        _assert_rejected(
            client, validation.register_sample, payload, "tube_count_out_of_range",
            "tube_count", "tube count out of range", "Sample",
        )


def test_register_sample_rejections_leave_no_rows(client, validation):
    before = _count(client, "Sample")
    rejected = [
        _sample_payload("blood", specimen_code="nope"),
        _sample_payload("blood", label=" x "),
        _sample_payload("urine", volume_ml=0.0),
        _sample_payload("blood", tube_count=42),
        dict(_sample_payload("blood"), kind="plasma"),
    ]
    for payload in rejected:
        result = _call(validation.register_sample, client, payload)
        assert result.get("ok") is False, f"{payload!r} must be rejected, got {result!r}"
    assert _count(client, "Sample") == before, (
        "Rejected register_sample calls must not create any Sample objects."
    )


# --------------------------------------------------------------------------- #
# E. submit_measurement happy paths
# --------------------------------------------------------------------------- #


def test_submit_measurement_happy_path(client, validation):
    sample_id = _assert_accepted(
        client, validation.register_sample, _sample_payload("blood"), "Sample"
    )
    payload = _measurement_payload(sample_id)
    new_id = _assert_accepted(client, validation.submit_measurement, payload, "Measurement")
    row = client.query_single(
        """
        select Measurement {
          analyte,
          value,
          unit_label := <str>.unit,
          state_label := <str>.state,
          ref_low,
          ref_high,
          label,
          sample_id := .sample.id
        } filter .id = <uuid><str>$oid
        """,
        oid=new_id,
    )
    assert row.analyte == "GLU", f"Wrong analyte stored: {row.analyte!r}"
    assert abs(row.value - 5.4) < 1e-9, f"Wrong value stored: {row.value!r}"
    assert row.unit_label == "mmol_per_L", f"Wrong unit stored: {row.unit_label!r}"
    assert row.state_label == "pending", f"Wrong state stored: {row.state_label!r}"
    assert abs(row.ref_low - 3.9) < 1e-9, f"Wrong ref_low stored: {row.ref_low!r}"
    assert abs(row.ref_high - 5.8) < 1e-9, f"Wrong ref_high stored: {row.ref_high!r}"
    assert row.label == "fasting glucose", f"Wrong label stored: {row.label!r}"
    assert str(row.sample_id) == str(sample_id), (
        f"The measurement is linked to the wrong sample: {row.sample_id!r}"
    )


def test_submit_measurement_validated_boundaries_accepted(client, validation):
    sample_id = _assert_accepted(
        client, validation.register_sample, _sample_payload("blood"), "Sample"
    )
    low_payload = _measurement_payload(
        sample_id, analyte="LOWB", state="validated", value=1.0, ref_low=1.0, ref_high=10.0
    )
    high_payload = _measurement_payload(
        sample_id, analyte="HIGHB", state="validated", value=10.0, ref_low=1.0, ref_high=10.0
    )
    _assert_accepted(client, validation.submit_measurement, low_payload, "Measurement")
    _assert_accepted(client, validation.submit_measurement, high_payload, "Measurement")


def test_submit_measurement_accepts_every_enum_label(client, validation):
    sample_id = _assert_accepted(
        client, validation.register_sample, _sample_payload("blood"), "Sample"
    )
    units = ["mg_per_dL", "mmol_per_L", "g_per_L", "IU_per_L"]
    for index, unit in enumerate(units):
        payload = _measurement_payload(
            sample_id, analyte=f"UNI{index}", unit=unit, value=5.0, ref_low=1.0, ref_high=10.0
        )
        new_id = _assert_accepted(
            client, validation.submit_measurement, payload, "Measurement"
        )
        row = client.query_single(
            "select Measurement { u := <str>.unit } filter .id = <uuid><str>$oid",
            oid=new_id,
        )
        assert row.u == unit, f"Expected stored unit {unit!r}, got {row.u!r}"

    for index, state in enumerate(["pending", "validated", "rejected"]):
        payload = _measurement_payload(
            sample_id, analyte=f"STA{index}", state=state, value=5.0, ref_low=1.0, ref_high=10.0
        )
        new_id = _assert_accepted(
            client, validation.submit_measurement, payload, "Measurement"
        )
        row = client.query_single(
            "select Measurement { s := <str>.state } filter .id = <uuid><str>$oid",
            oid=new_id,
        )
        assert row.s == state, f"Expected stored state {state!r}, got {row.s!r}"


# --------------------------------------------------------------------------- #
# F. submit_measurement rejections
# --------------------------------------------------------------------------- #


@pytest.fixture()
def sample_for_measurement(client, validation):
    return _assert_accepted(
        client, validation.register_sample, _sample_payload("blood"), "Sample"
    )


def test_submit_measurement_missing_fields(client, validation, sample_for_measurement):
    payload = _measurement_payload(sample_for_measurement)
    payload.pop("unit")
    _assert_rejected(
        client, validation.submit_measurement, payload, "missing_field", "unit",
        "missing required field", "Measurement",
    )

    payload = _measurement_payload(sample_for_measurement)
    payload.pop("analyte")
    payload.pop("value")
    _assert_rejected(
        client, validation.submit_measurement, payload, "missing_field", "analyte",
        "missing required field", "Measurement",
    )

    payload = _measurement_payload(sample_for_measurement)
    payload.pop("sample_id")
    _assert_rejected(
        client, validation.submit_measurement, payload, "missing_field", "sample_id",
        "missing required field", "Measurement",
    )


def test_submit_measurement_unknown_sample(client, validation):
    for bad_id in (str(uuid.uuid4()), "not-a-uuid", "00000000-0000-0000-0000-000000000000"):
        payload = _measurement_payload(bad_id)
        _assert_rejected(
            client, validation.submit_measurement, payload, "sample_not_found",
            "sample_id", "unknown sample", "Measurement",
        )


def test_submit_measurement_invalid_analyte(client, validation, sample_for_measurement):
    for bad in ("gl", "GLUCOSE123", "1AB", "GL_U"):
        payload = _measurement_payload(sample_for_measurement, analyte=bad)
        _assert_rejected(
            client, validation.submit_measurement, payload, "invalid_analyte_code",
            "analyte", "invalid analyte code", "Measurement",
        )


def test_submit_measurement_value_bounds(client, validation, sample_for_measurement):
    payload = _measurement_payload(
        sample_for_measurement, analyte="NEG", value=-2.0, ref_low=-5.0, ref_high=10.0
    )
    _assert_rejected(
        client, validation.submit_measurement, payload, "value_negative", "value",
        "value must not be negative", "Measurement",
    )
    payload = _measurement_payload(
        sample_for_measurement, analyte="BIG", value=100001.0, ref_low=1.0, ref_high=200000.0
    )
    _assert_rejected(
        client, validation.submit_measurement, payload, "value_above_ceiling", "value",
        "value exceeds instrument ceiling", "Measurement",
    )


def test_submit_measurement_invalid_enum_labels(client, validation, sample_for_measurement):
    payload = _measurement_payload(sample_for_measurement, unit="mg/dL")
    _assert_rejected(
        client, validation.submit_measurement, payload, "invalid_unit", "unit",
        "invalid unit", "Measurement",
    )
    payload = _measurement_payload(sample_for_measurement, state="approved")
    _assert_rejected(
        client, validation.submit_measurement, payload, "invalid_state", "state",
        "invalid review state", "Measurement",
    )


def test_submit_measurement_malformed_label(client, validation, sample_for_measurement):
    for bad in ("   ", " leading", "trailing ", "y" * 81):
        payload = _measurement_payload(sample_for_measurement, label=bad)
        _assert_rejected(
            client, validation.submit_measurement, payload, "malformed_label", "label",
            "malformed label", "Measurement",
        )


def test_submit_measurement_interval_not_ascending(client, validation, sample_for_measurement):
    for low, high in ((7.0, 7.0), (9.0, 4.0)):
        payload = _measurement_payload(
            sample_for_measurement, value=7.0, ref_low=low, ref_high=high
        )
        _assert_rejected(
            client, validation.submit_measurement, payload, "interval_not_ascending",
            "ref_high", "reference interval not ascending", "Measurement",
        )


def test_submit_measurement_validated_value_outside_interval(
    client, validation, sample_for_measurement
):
    payload = _measurement_payload(
        sample_for_measurement, state="validated", value=99.0, ref_low=1.0, ref_high=10.0
    )
    _assert_rejected(
        client, validation.submit_measurement, payload, "value_outside_reference", "value",
        "validated value outside reference interval", "Measurement",
    )


def test_submit_measurement_duplicate_analyte(client, validation):
    first = _assert_accepted(
        client, validation.register_sample, _sample_payload("blood"), "Sample"
    )
    second = _assert_accepted(
        client, validation.register_sample, _sample_payload("blood"), "Sample"
    )
    payload = _measurement_payload(first, analyte="DUP", value=5.0, ref_low=1.0, ref_high=10.0)
    _assert_accepted(client, validation.submit_measurement, payload, "Measurement")
    _assert_rejected(
        client, validation.submit_measurement, dict(payload), "duplicate_analyte",
        "analyte", "duplicate analyte for sample", "Measurement",
    )
    other = _measurement_payload(second, analyte="DUP", value=5.0, ref_low=1.0, ref_high=10.0)
    _assert_accepted(client, validation.submit_measurement, other, "Measurement")


def test_submit_measurement_rejections_leave_no_rows(client, validation):
    sample_id = _assert_accepted(
        client, validation.register_sample, _sample_payload("blood"), "Sample"
    )
    before = _count(client, "Measurement")
    rejected = [
        _measurement_payload(sample_id, analyte="bad"),
        _measurement_payload(sample_id, unit="nope"),
        _measurement_payload(sample_id, state="nope"),
        _measurement_payload(sample_id, label=" bad "),
        _measurement_payload(sample_id, ref_low=9.0, ref_high=1.0),
        _measurement_payload(sample_id, value=-1.0, ref_low=-5.0, ref_high=5.0),
        _measurement_payload(str(uuid.uuid4())),
    ]
    for payload in rejected:
        result = _call(validation.submit_measurement, client, payload)
        assert result.get("ok") is False, f"{payload!r} must be rejected, got {result!r}"
    assert _count(client, "Measurement") == before, (
        "Rejected submit_measurement calls must not create any Measurement objects."
    )
