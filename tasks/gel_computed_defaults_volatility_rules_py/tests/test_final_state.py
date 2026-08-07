import asyncio
import glob
import importlib.util
import inspect
import json
import os
import subprocess
import sys

import gel
import pytest

PROJECT_DIR = "/home/user/gelproj"
SCHEMA_DIR = os.path.join(PROJECT_DIR, "dbschema")
MIGRATIONS_DIR = os.path.join(SCHEMA_DIR, "migrations")
REPORT_PATH = os.path.join(PROJECT_DIR, "report.json")
SEMANTICS_PATH = os.path.join(PROJECT_DIR, "semantics.py")
MAIN_PATH = os.path.join(PROJECT_DIR, "main.py")
START_SCRIPT = "/usr/local/bin/gel-start.sh"

SHORT_TYPES = ["Sample", "Measurement", "Assay", "Calibration", "Certificate", "Batch"]

# (cardinality, required, computed, target)
MEASUREMENT_POINTERS = {
    "code": ("One", True, False, "std::str"),
    "sample": ("One", True, False, "default::Sample"),
}

EXPECTED_POINTERS = {
    "Sample": {
        "label": ("One", True, False, "std::str"),
        "grams": ("One", True, False, "std::float64"),
        "intake_ref": ("One", True, False, "std::uuid"),
        "intake_at": ("One", True, False, "std::datetime"),
        "label_key": ("One", True, True, "std::str"),
        "age": ("One", True, True, "std::duration"),
        "assay_count": ("One", True, True, "std::int64"),
        "measurement_count": ("One", True, True, "std::int64"),
        "total_value": ("One", True, True, "std::float64"),
        "measurements": ("Many", False, True, "default::Measurement"),
        "assays": ("Many", False, True, "default::Assay"),
        "batches": ("Many", False, True, "default::Batch"),
        "certificate": ("One", False, True, "default::Certificate"),
    },
    "Measurement": dict(MEASUREMENT_POINTERS),
    "Assay": dict(MEASUREMENT_POINTERS, value=("One", True, False, "std::float64")),
    "Calibration": dict(MEASUREMENT_POINTERS, bias=("One", True, False, "std::float64")),
    "Certificate": {
        "serial": ("One", True, False, "std::str"),
        "sample": ("One", True, False, "default::Sample"),
    },
    "Batch": {
        "code": ("One", True, False, "std::str"),
        "samples": ("Many", False, False, "default::Sample"),
        "sample_count": ("One", True, True, "std::int64"),
    },
}

EXPECTED_ABSTRACT = {
    "Sample": False,
    "Measurement": True,
    "Assay": False,
    "Calibration": False,
    "Certificate": False,
    "Batch": False,
}

EXPECTED_SAMPLES = [
    ("Alpha-01", "alpha-01", 2, 3, 12.5, True, ["BATCH-1"]),
    ("Bravo-02", "bravo-02", 1, 1, 7.25, False, ["BATCH-1"]),
    ("Charlie-03", "charlie-03", 0, 1, 0.0, False, ["BATCH-1"]),
    ("Echo-05", "echo-05", 0, 0, 0.0, False, []),
    ("Zulu-99", "zulu-99", 0, 0, 0.0, False, []),
]

INTROSPECT_QUERY = """
select schema::ObjectType {
    name,
    abstract,
    pointers: {
        name,
        cardinality,
        required,
        computed := exists .expr,
        readonly,
        default,
        target_name := .target.name
    } filter .name not in {'id', '__type__'}
}
filter .name in array_unpack(<array<str>>$names)
"""

LINK_PROPS_QUERY = """
select schema::ObjectType {
    name,
    pointers: {
        name,
        [is schema::Link].pointers: {
            name,
            cardinality,
            required,
            target_name := .target.name
        }
    } filter .name = 'samples'
}
filter .name = 'default::Batch'
"""

SAMPLES_QUERY = """
select Sample {
    label,
    label_key,
    assay_count,
    measurement_count,
    total_value,
    has_certificate := exists .certificate,
    batches: { code } order by .code
}
order by .label
"""

VOLATILE_SCHEMA_PROBE = (
    "start migration to { module default { "
    "type ZzVolatilityProbe { p := datetime_current(); } } }"
)


def _json(client, query, **kwargs):
    return json.loads(client.query_json(query, **kwargs))


def _single(client, query, **kwargs):
    return json.loads(client.query_single_json(query, **kwargs))


def _run_gel_cli(*args):
    return subprocess.run(
        ["gel", *args],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=180,
        env=os.environ.copy(),
    )


def _error_name(exc):
    return type(exc).__name__


@pytest.fixture(scope="session")
def gel_server():
    proc = subprocess.run(
        [START_SCRIPT], capture_output=True, text=True, timeout=600
    )
    assert proc.returncode == 0, (
        "Failed to start the local Gel server with "
        f"{START_SCRIPT}: stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    return True


@pytest.fixture(scope="session")
def client(gel_server):
    conn = gel.create_client()
    try:
        conn.query("select 1")
    except Exception as exc:  # pragma: no cover - environment failure
        pytest.fail(f"Could not connect to the local Gel server: {exc}")
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def report(client):
    if os.path.exists(REPORT_PATH):
        os.remove(REPORT_PATH)
    proc = subprocess.run(
        ["python3", "main.py"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=900,
        env=os.environ.copy(),
    )
    assert proc.returncode == 0, (
        "`python3 main.py` did not exit with status 0: "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert os.path.isfile(REPORT_PATH), (
        f"{REPORT_PATH} was not created by `python3 main.py`."
    )
    with open(REPORT_PATH, encoding="utf-8") as handle:
        data = json.load(handle)
    assert isinstance(data, dict), "report.json must contain a JSON object."
    return data


@pytest.fixture(scope="session")
def semantics_module():
    assert os.path.isfile(SEMANTICS_PATH), f"{SEMANTICS_PATH} does not exist."
    if PROJECT_DIR not in sys.path:
        sys.path.insert(0, PROJECT_DIR)
    spec = importlib.util.spec_from_file_location("semantics", SEMANTICS_PATH)
    assert spec is not None and spec.loader is not None, (
        f"Could not build an import spec for {SEMANTICS_PATH}."
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["semantics"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def db_introspection(client):
    rows = _json(client, INTROSPECT_QUERY, names=[f"default::{n}" for n in SHORT_TYPES])
    by_name = {row["name"]: row for row in rows}
    return by_name


# ---------------------------------------------------------------- artifacts


def test_solution_files_exist():
    assert os.path.isfile(MAIN_PATH), f"{MAIN_PATH} does not exist."
    assert os.path.isfile(SEMANTICS_PATH), f"{SEMANTICS_PATH} does not exist."


def test_schema_and_migration_files_exist():
    schema_files = glob.glob(os.path.join(SCHEMA_DIR, "*.gel"))
    assert schema_files, f"No *.gel schema file found under {SCHEMA_DIR}."
    migrations = glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql"))
    assert migrations, (
        f"No *.edgeql migration file found under {MIGRATIONS_DIR}; a migration "
        "history on disk is mandatory."
    )


def test_migration_status_is_in_sync(client):
    proc = _run_gel_cli("migration", "status")
    combined = f"{proc.stdout}\n{proc.stderr}"
    assert proc.returncode == 0, (
        f"`gel migration status` failed (rc={proc.returncode}): {combined}"
    )
    assert "up to date" in combined.lower(), (
        f"`gel migration status` does not report the branch as up to date: {combined}"
    )


def test_build_report_is_async_callable(semantics_module):
    assert hasattr(semantics_module, "build_report"), (
        "semantics.py does not define `build_report`."
    )
    assert inspect.iscoroutinefunction(semantics_module.build_report), (
        "semantics.build_report must be an `async def` coroutine function."
    )
    signature = inspect.signature(semantics_module.build_report)
    required = [
        p
        for p in signature.parameters.values()
        if p.default is inspect.Parameter.empty
        and p.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    assert not required, (
        "semantics.build_report must be callable without arguments, but requires: "
        f"{[p.name for p in required]}"
    )


# ------------------------------------------------------------ introspection


def test_declared_object_types_are_exactly_the_six(client):
    rows = _json(
        client,
        "select schema::ObjectType { name } filter .name like 'default::%' order by .name",
    )
    names = sorted(row["name"] for row in rows)
    expected = sorted(f"default::{n}" for n in SHORT_TYPES)
    assert names == expected, (
        f"Module `default` must contain exactly {expected}, found {names}."
    )


@pytest.mark.parametrize("short_name", SHORT_TYPES)
def test_pointer_contract_in_database(db_introspection, short_name):
    full_name = f"default::{short_name}"
    assert full_name in db_introspection, f"Object type {full_name} does not exist."
    row = db_introspection[full_name]
    assert row["abstract"] == EXPECTED_ABSTRACT[short_name], (
        f"{full_name}.abstract should be {EXPECTED_ABSTRACT[short_name]}, "
        f"got {row['abstract']}."
    )
    pointers = {p["name"]: p for p in row["pointers"]}
    for name, (cardinality, required, computed, target) in EXPECTED_POINTERS[
        short_name
    ].items():
        assert name in pointers, f"{full_name} has no pointer named '{name}'."
        actual = pointers[name]
        assert actual["cardinality"] == cardinality, (
            f"{full_name}.{name} cardinality should be {cardinality}, "
            f"got {actual['cardinality']}."
        )
        assert bool(actual["required"]) == required, (
            f"{full_name}.{name} required should be {required}, got {actual['required']}."
        )
        assert bool(actual["computed"]) == computed, (
            f"{full_name}.{name} computed should be {computed} "
            f"(expr={actual.get('expr')!r})."
        )
        assert actual["target_name"] == target, (
            f"{full_name}.{name} target should be {target}, got {actual['target_name']}."
        )


def test_stored_defaults_are_declared_readonly_with_defaults(db_introspection):
    pointers = {p["name"]: p for p in db_introspection["default::Sample"]["pointers"]}
    for name in ("intake_ref", "intake_at"):
        assert pointers[name]["default"], (
            f"Sample.{name} must declare a stored default expression."
        )
        assert bool(pointers[name]["readonly"]) is True, (
            f"Sample.{name} must be declared read-only."
        )


def test_link_property_introspection(client):
    rows = _json(client, LINK_PROPS_QUERY)
    assert rows, "Object type default::Batch was not found."
    pointers = rows[0]["pointers"]
    assert pointers, "default::Batch has no pointer named 'samples'."
    nested = {
        p["name"]: p
        for p in (pointers[0].get("pointers") or [])
        if p["name"] not in ("source", "target")
    }
    assert "position" in nested, (
        f"Link property 'position' not found on Batch.samples; found {sorted(nested)}."
    )
    prop = nested["position"]
    assert prop["cardinality"] == "One", (
        f"Batch.samples@position cardinality should be One, got {prop['cardinality']}."
    )
    assert bool(prop["required"]) is False, (
        "Batch.samples@position must not be required (link properties cannot be required)."
    )
    assert prop["target_name"] == "std::int64", (
        f"Batch.samples@position target should be std::int64, got {prop['target_name']}."
    )


def test_report_introspection_matches_database(report, db_introspection):
    introspection = report.get("introspection")
    assert isinstance(introspection, dict), "report['introspection'] must be an object."
    for short_name in SHORT_TYPES:
        assert short_name in introspection, (
            f"report['introspection'] is missing the key '{short_name}'."
        )
        entry = introspection[short_name]
        assert entry.get("abstract") == EXPECTED_ABSTRACT[short_name], (
            f"report['introspection']['{short_name}']['abstract'] should be "
            f"{EXPECTED_ABSTRACT[short_name]}."
        )
        reported = entry.get("pointers")
        assert isinstance(reported, dict), (
            f"report['introspection']['{short_name}']['pointers'] must be an object."
        )
        for name, (cardinality, required, computed, target) in EXPECTED_POINTERS[
            short_name
        ].items():
            assert name in reported, (
                f"report['introspection']['{short_name}']['pointers'] is missing '{name}'."
            )
            assert reported[name] == {
                "cardinality": cardinality,
                "required": required,
                "computed": computed,
                "target": target,
            }, (
                f"report['introspection']['{short_name}']['pointers']['{name}'] is "
                f"{reported[name]!r}, expected cardinality={cardinality}, "
                f"required={required}, computed={computed}, target={target}."
            )


def test_report_link_properties_match_database(report):
    link_properties = report.get("link_properties")
    assert isinstance(link_properties, dict), (
        "report['link_properties'] must be an object."
    )
    assert "Batch.samples" in link_properties, (
        "report['link_properties'] is missing the key 'Batch.samples'."
    )
    entry = link_properties["Batch.samples"]
    assert set(entry) == {"position"}, (
        "report['link_properties']['Batch.samples'] must describe exactly the link "
        f"property 'position', got {sorted(entry)}."
    )
    assert entry["position"] == {
        "cardinality": "One",
        "required": False,
        "target": "std::int64",
    }, (
        "report['link_properties']['Batch.samples']['position'] is "
        f"{entry['position']!r}, expected One / not required / std::int64."
    )


# ------------------------------------------------------------------ dataset


def test_seeded_row_counts(client, report):
    counts = _single(
        client,
        """
        select {
            samples := count(Sample),
            assays := count(Assay),
            calibrations := count(Calibration),
            certificates := count(Certificate),
            batches := count(Batch)
        }
        """,
    )
    assert counts == {
        "samples": 5,
        "assays": 3,
        "calibrations": 2,
        "certificates": 1,
        "batches": 1,
    }, f"Unexpected row counts after running the program: {counts}"


def test_sample_labels_and_updated_row(client, report):
    rows = _json(client, "select Sample { label, grams } order by .label")
    labels = [row["label"] for row in rows]
    assert labels == ["Alpha-01", "Bravo-02", "Charlie-03", "Echo-05", "Zulu-99"], (
        f"Unexpected sample labels: {labels}"
    )
    assert "Delta-04" not in labels, "The sample labelled 'Delta-04' must be re-labelled."
    zulu = [row for row in rows if row["label"] == "Zulu-99"][0]
    assert float(zulu["grams"]) == pytest.approx(9.0), (
        f"Sample 'Zulu-99' should have grams 9.0, got {zulu['grams']}."
    )


def test_report_samples_array(report):
    samples = report.get("samples")
    assert isinstance(samples, list), "report['samples'] must be a list."
    assert len(samples) == len(EXPECTED_SAMPLES), (
        f"report['samples'] must contain {len(EXPECTED_SAMPLES)} entries, "
        f"got {len(samples)}."
    )
    for index, expected in enumerate(EXPECTED_SAMPLES):
        label, label_key, assays, measurements, total, has_cert, batch_codes = expected
        entry = samples[index]
        assert entry.get("label") == label, (
            f"report['samples'][{index}]['label'] should be {label} "
            f"(entries must be ordered by label ascending), got {entry.get('label')}."
        )
        assert entry.get("label_key") == label_key, (
            f"report['samples'][{index}]['label_key'] should be {label_key}."
        )
        assert entry.get("assay_count") == assays, (
            f"report['samples'][{index}]['assay_count'] should be {assays}."
        )
        assert entry.get("measurement_count") == measurements, (
            f"report['samples'][{index}]['measurement_count'] should be {measurements}."
        )
        assert float(entry.get("total_value")) == pytest.approx(total), (
            f"report['samples'][{index}]['total_value'] should be {total}."
        )
        assert bool(entry.get("has_certificate")) is has_cert, (
            f"report['samples'][{index}]['has_certificate'] should be {has_cert}."
        )
        assert entry.get("batch_codes") == batch_codes, (
            f"report['samples'][{index}]['batch_codes'] should be {batch_codes}."
        )


def test_computed_values_match_direct_query(client, report):
    rows = _json(client, SAMPLES_QUERY)
    actual = [
        (
            row["label"],
            row["label_key"],
            row["assay_count"],
            row["measurement_count"],
            float(row["total_value"]),
            bool(row["has_certificate"]),
            [b["code"] for b in row["batches"]],
        )
        for row in rows
    ]
    expected = [
        (label, key, a, m, float(t), c, codes)
        for label, key, a, m, t, c, codes in EXPECTED_SAMPLES
    ]
    assert actual == expected, (
        f"Computed pointers queried directly from the database returned {actual}, "
        f"expected {expected}."
    )


def test_batch_and_link_property_values(client, report):
    batch = _single(
        client,
        """
        select assert_single(Batch {
            code,
            sample_count,
            members := (select .samples { label, position := @position } order by @position)
        })
        """,
    )
    assert batch["code"] == "BATCH-1", f"Batch code should be BATCH-1, got {batch['code']}."
    assert batch["sample_count"] == 3, (
        f"Batch.sample_count should be 3, got {batch['sample_count']}."
    )
    members = [(m["label"], m["position"]) for m in batch["members"]]
    assert members == [("Alpha-01", 1), ("Bravo-02", 2), ("Charlie-03", 3)], (
        f"Batch members/positions queried from the database are {members}."
    )
    reported = report.get("batch")
    assert isinstance(reported, dict), "report['batch'] must be an object."
    assert reported.get("code") == "BATCH-1", "report['batch']['code'] should be BATCH-1."
    assert reported.get("sample_count") == 3, "report['batch']['sample_count'] should be 3."
    reported_members = [
        (m.get("label"), m.get("position")) for m in reported.get("members", [])
    ]
    assert reported_members == members, (
        f"report['batch']['members'] is {reported_members}, expected {members} "
        "ordered by position ascending."
    )


# ----------------------------------------------------------------- defaults


def test_report_defaults_block(report):
    defaults = report.get("defaults")
    assert isinstance(defaults, dict), "report['defaults'] must be an object."
    assert defaults.get("batch_insert_size") == 4, (
        "report['defaults']['batch_insert_size'] should be 4."
    )
    assert defaults.get("distinct_intake_at_in_batch") == 1, (
        "report['defaults']['distinct_intake_at_in_batch'] should be 1: a statement-stable "
        "default yields one single value for every row created by one statement."
    )
    assert defaults.get("distinct_intake_ref_in_batch") == 4, (
        "report['defaults']['distinct_intake_ref_in_batch'] should be 4: a volatile default "
        "is re-evaluated for every row."
    )
    for key in (
        "intake_at_unchanged_after_update",
        "intake_ref_unchanged_after_update",
        "late_intake_at_not_before_batch",
    ):
        assert defaults.get(key) is True, f"report['defaults']['{key}'] should be true."
    rejected = defaults.get("readonly_update_rejected")
    assert isinstance(rejected, dict), (
        "report['defaults']['readonly_update_rejected'] must be an object."
    )
    assert rejected.get("rejected") is True, (
        "Assigning a new value to a read-only stored default must be rejected."
    )
    assert rejected.get("error_class") == "QueryError", (
        "report['defaults']['readonly_update_rejected']['error_class'] should be "
        f"'QueryError', got {rejected.get('error_class')!r}."
    )
    assert "read-only" in str(rejected.get("error_message", "")).lower(), (
        "report['defaults']['readonly_update_rejected']['error_message'] should mention "
        f"read-only, got {rejected.get('error_message')!r}."
    )


def test_defaults_captured_once_per_statement(client, report):
    client.execute(
        """
        for s in {('Verify-A', 1.0), ('Verify-B', 2.0), ('Verify-C', 3.0)}
        union (insert Sample { label := s.0, grams := s.1 })
        """
    )
    try:
        stats = _single(
            client,
            """
            with v := (select Sample filter .label in {'Verify-A', 'Verify-B', 'Verify-C'}),
                 a := assert_single((select Sample filter .label = 'Alpha-01'))
            select {
                n := count(v),
                distinct_intake_at := count(distinct v.intake_at),
                distinct_intake_ref := count(distinct v.intake_ref),
                not_before_alpha := all(v.intake_at >= a.intake_at)
            }
            """,
        )
        assert stats["n"] == 3, f"Expected 3 verifier samples, got {stats['n']}."
        assert stats["distinct_intake_at"] == 1, (
            "Three rows inserted by one statement must share a single stored intake "
            f"timestamp, got {stats['distinct_intake_at']} distinct values."
        )
        assert stats["distinct_intake_ref"] == 3, (
            "Three rows inserted by one statement must each get their own stored "
            f"reference value, got {stats['distinct_intake_ref']} distinct values."
        )
        assert stats["not_before_alpha"] is True, (
            "Samples inserted later must not carry an earlier intake timestamp."
        )

        before = _single(
            client,
            """
            select assert_single((
                select Sample { i_at := <str>.intake_at, i_ref := <str>.intake_ref }
                filter .label = 'Verify-A'
            ))
            """,
        )
        client.execute("update Sample filter .label = 'Verify-A' set { grams := 42.0 }")
        after = _single(
            client,
            """
            select assert_single((
                select Sample { i_at := <str>.intake_at, i_ref := <str>.intake_ref, grams }
                filter .label = 'Verify-A'
            ))
            """,
        )
        assert float(after["grams"]) == pytest.approx(42.0), (
            "The verifier update of `grams` did not take effect."
        )
        assert after["i_at"] == before["i_at"], (
            "The stored intake timestamp must not change when another property is updated."
        )
        assert after["i_ref"] == before["i_ref"], (
            "The stored reference value must not change when another property is updated."
        )

        with pytest.raises(Exception) as excinfo:
            client.execute(
                "update Sample filter .label = 'Verify-A' "
                "set { intake_ref := <uuid>'11111111-1111-1111-1111-111111111111' }"
            )
        message = str(excinfo.value).lower()
        assert "read-only" in message, (
            "Assigning the stored reference value directly must be refused because the "
            f"pointer is read-only; got: {excinfo.value!r}"
        )
    finally:
        client.execute(
            "delete Sample filter .label in {'Verify-A', 'Verify-B', 'Verify-C'}"
        )
    remaining = _single(client, "select { n := count(Sample) }")
    assert remaining["n"] == 5, (
        f"After cleanup there must be 5 samples again, found {remaining['n']}."
    )


# ----------------------------------------------------------------- computeds


def test_computed_pointers_react_to_new_measurements(client, report):
    inserted = _single(
        client,
        """
        select (insert Assay {
            code := 'VZ',
            value := 3.5,
            sample := assert_single((select Sample filter .label = 'Charlie-03'))
        }) { id }
        """,
    )
    try:
        after = _single(
            client,
            """
            select assert_single((
                select Sample { assay_count, measurement_count, total_value }
                filter .label = 'Charlie-03'
            ))
            """,
        )
        assert after["assay_count"] == 1, (
            "assay_count must be derived at read time: after inserting one assay for "
            f"Charlie-03 it should be 1, got {after['assay_count']}."
        )
        assert after["measurement_count"] == 2, (
            "measurement_count must be derived at read time: it should be 2, got "
            f"{after['measurement_count']}."
        )
        assert float(after["total_value"]) == pytest.approx(3.5), (
            f"total_value should be 3.5, got {after['total_value']}."
        )
    finally:
        client.execute("delete Assay filter .code = 'VZ'")
    restored = _single(
        client,
        """
        select assert_single((
            select Sample { assay_count, measurement_count, total_value }
            filter .label = 'Charlie-03'
        ))
        """,
    )
    assert restored["assay_count"] == 0, (
        f"After deleting the extra assay, assay_count should be 0, got {restored['assay_count']}."
    )
    assert restored["measurement_count"] == 1, (
        "After deleting the extra assay, measurement_count should be 1, got "
        f"{restored['measurement_count']}."
    )
    assert float(restored["total_value"]) == pytest.approx(0.0), (
        f"After deleting the extra assay, total_value should be 0.0, got {restored['total_value']}."
    )
    assert inserted["id"], "The verifier assay insert did not return an id."


def test_computed_key_follows_its_source_property(client, report):
    client.execute("update Sample filter .label = 'Charlie-03' set { label := 'charlieX' }")
    try:
        row = _single(
            client,
            "select assert_single((select Sample { label_key } filter .label = 'charlieX'))",
        )
        assert row["label_key"] == "charliex", (
            "label_key must be derived from label at read time; expected 'charliex', "
            f"got {row['label_key']!r}."
        )
    finally:
        client.execute("update Sample filter .label = 'charlieX' set { label := 'Charlie-03' }")
    restored = _single(
        client,
        "select assert_single((select Sample { label_key } filter .label = 'Charlie-03'))",
    )
    assert restored["label_key"] == "charlie-03", (
        f"label_key should be back to 'charlie-03', got {restored['label_key']!r}."
    )


def test_report_computed_block(report):
    computed = report.get("computed")
    assert isinstance(computed, dict), "report['computed'] must be an object."
    assert computed.get("label_key_before_update") == "delta-04", (
        "report['computed']['label_key_before_update'] should be 'delta-04'."
    )
    assert computed.get("label_key_after_update") == "zulu-99", (
        "report['computed']['label_key_after_update'] should be 'zulu-99'."
    )
    assert computed.get("alpha_assay_count_before") == 0, (
        "report['computed']['alpha_assay_count_before'] should be 0."
    )
    assert computed.get("alpha_measurement_count_before") == 0, (
        "report['computed']['alpha_measurement_count_before'] should be 0."
    )
    assert "alpha_total_value_before" in computed, (
        "report['computed'] is missing 'alpha_total_value_before'."
    )
    assert float(computed["alpha_total_value_before"]) == pytest.approx(0.0), (
        "report['computed']['alpha_total_value_before'] should be 0.0."
    )
    assert computed.get("alpha_assay_count_after") == 2, (
        "report['computed']['alpha_assay_count_after'] should be 2."
    )
    assert computed.get("alpha_measurement_count_after") == 3, (
        "report['computed']['alpha_measurement_count_after'] should be 3."
    )
    assert "alpha_total_value_after" in computed, (
        "report['computed'] is missing 'alpha_total_value_after'."
    )
    assert float(computed["alpha_total_value_after"]) == pytest.approx(12.5), (
        "report['computed']['alpha_total_value_after'] should be 12.5."
    )
    assert computed.get("age_non_negative") is True, (
        "report['computed']['age_non_negative'] should be true."
    )
    assert computed.get("age_difference_matches_intake_difference") is True, (
        "report['computed']['age_difference_matches_intake_difference'] should be true."
    )


def test_read_time_age_invariant_from_database(client, report):
    row = _single(
        client,
        """
        with a := assert_single((select Sample filter .label = 'Alpha-01')),
             e := assert_single((select Sample filter .label = 'Echo-05'))
        select {
            invariant := ((a.age - e.age) = (e.intake_at - a.intake_at)),
            non_negative := (a.age >= <duration>'0 seconds')
        }
        """,
    )
    assert row["non_negative"] is True, (
        "The read-time age of a sample must never be negative."
    )
    assert row["invariant"] is True, (
        "Within a single statement the derived ages of two samples must differ exactly "
        "by the difference of their stored intake timestamps."
    )


# ---------------------------------------------------------------- volatility


def test_report_volatility_block(report):
    volatility = report.get("volatility")
    assert isinstance(volatility, dict), "report['volatility'] must be an object."
    schema_probe = volatility.get("schema_computed_volatile")
    assert isinstance(schema_probe, dict), (
        "report['volatility']['schema_computed_volatile'] must be an object."
    )
    assert schema_probe.get("rejected") is True, (
        "A schema-level computed pointer with a volatile expression must be rejected."
    )
    assert schema_probe.get("error_class") == "SchemaDefinitionError", (
        "report['volatility']['schema_computed_volatile']['error_class'] should be "
        f"'SchemaDefinitionError', got {schema_probe.get('error_class')!r}."
    )
    assert "volatile" in str(schema_probe.get("error_message", "")).lower(), (
        "report['volatility']['schema_computed_volatile']['error_message'] should mention "
        f"volatility, got {schema_probe.get('error_message')!r}."
    )
    cartesian = volatility.get("cartesian_volatile")
    assert isinstance(cartesian, dict), (
        "report['volatility']['cartesian_volatile'] must be an object."
    )
    assert cartesian.get("rejected") is True, (
        "A cartesian product with a volatile expression must be rejected."
    )
    assert cartesian.get("error_class") == "QueryError", (
        "report['volatility']['cartesian_volatile']['error_class'] should be 'QueryError', "
        f"got {cartesian.get('error_class')!r}."
    )
    assert "volatile" in str(cartesian.get("error_message", "")).lower(), (
        "report['volatility']['cartesian_volatile']['error_message'] should mention "
        f"volatility, got {cartesian.get('error_message')!r}."
    )
    assert volatility.get("schema_unchanged_after_probe") is True, (
        "report['volatility']['schema_unchanged_after_probe'] should be true."
    )


def test_volatile_expressions_are_refused_by_the_server(client, report):
    with pytest.raises(Exception) as schema_exc:
        try:
            client.execute(VOLATILE_SCHEMA_PROBE)
        finally:
            try:
                client.execute("abort migration")
            except Exception:
                pass
    assert _error_name(schema_exc.value) == "SchemaDefinitionError", (
        "Submitting a schema whose computed pointer calls a volatile function must raise "
        f"SchemaDefinitionError, got {_error_name(schema_exc.value)}: {schema_exc.value}"
    )
    assert "volatile" in str(schema_exc.value).lower(), (
        f"Unexpected error message for the schema probe: {schema_exc.value}"
    )

    with pytest.raises(Exception) as query_exc:
        client.query("select {1, 2} + random()")
    assert _error_name(query_exc.value) == "QueryError", (
        "A cartesian product with a volatile expression must raise QueryError, got "
        f"{_error_name(query_exc.value)}: {query_exc.value}"
    )
    assert "volatile" in str(query_exc.value).lower(), (
        f"Unexpected error message for the cartesian probe: {query_exc.value}"
    )


def test_probes_left_no_residue(client, report):
    rows = _json(
        client,
        "select schema::ObjectType { name } filter .name like 'default::%' order by .name",
    )
    names = sorted(row["name"] for row in rows)
    expected = sorted(f"default::{n}" for n in SHORT_TYPES)
    assert names == expected, (
        f"The volatility probes must not leave residue in module `default`: {names}"
    )
    proc = _run_gel_cli("migration", "status")
    combined = f"{proc.stdout}\n{proc.stderr}"
    assert proc.returncode == 0 and "up to date" in combined.lower(), (
        f"The branch is no longer in sync with its migration history: {combined}"
    )


# -------------------------------------------------------------- rerunnability


def test_program_is_rerunnable_and_deterministic(client, report):
    proc = subprocess.run(
        ["python3", "main.py"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=900,
        env=os.environ.copy(),
    )
    assert proc.returncode == 0, (
        "The second `python3 main.py` run did not exit with status 0: "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    with open(REPORT_PATH, encoding="utf-8") as handle:
        second = json.load(handle)
    assert second == report, (
        "Two consecutive runs must produce exactly equal report documents."
    )
    counts = _single(
        client,
        """
        select {
            samples := count(Sample),
            assays := count(Assay),
            calibrations := count(Calibration),
            certificates := count(Certificate),
            batches := count(Batch)
        }
        """,
    )
    assert counts == {
        "samples": 5,
        "assays": 3,
        "calibrations": 2,
        "certificates": 1,
        "batches": 1,
    }, f"Re-running the program must not accumulate rows, found {counts}."


def test_build_report_coroutine_returns_the_same_document(semantics_module, report):
    produced = asyncio.run(semantics_module.build_report())
    assert isinstance(produced, dict), "semantics.build_report must return a dict."
    assert produced == report, (
        "The dict returned by semantics.build_report must equal the report written to "
        "report.json."
    )
