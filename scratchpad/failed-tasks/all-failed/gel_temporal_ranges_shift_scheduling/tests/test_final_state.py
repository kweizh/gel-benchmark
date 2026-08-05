"""Final-state verification for the Gel shift-scheduling & availability engine."""

import glob
import importlib
import os
import subprocess
import sys
import time

import gel
import gel.errors
import pytest

PROJECT_DIR = "/home/user/shiftops"
ENGINE_PATH = os.path.join(PROJECT_DIR, "shiftops_engine.py")
MIGRATIONS_DIR = os.path.join(PROJECT_DIR, "dbschema", "migrations")

_ERR_CANDIDATES = [
    getattr(gel.errors, name, None) for name in ("GelError", "EdgeDBError")
]
GEL_ERRORS = tuple(e for e in _ERR_CANDIDATES if isinstance(e, type)) or (Exception,)

WORKERS = [
    ("w-anna", "Anna Ortiz"),
    ("w-ben", "Ben Iwu"),
    ("w-cleo", "Cleo Park"),
]

# (worker_code, iso_weekday, start_time, end_time)
WINDOWS = [
    ("w-anna", 1, "08:00:00", "18:00:00"),
    ("w-anna", 2, "09:00:00", "12:00:00"),
    ("w-anna", 3, "08:00:00", "12:00:00"),
    ("w-anna", 3, "13:00:00", "17:00:00"),
    ("w-ben", 4, "16:00:00", "20:00:00"),
    ("w-cleo", 1, "00:00:00", "12:00:00"),
]

# (tag, worker_code, role, starts_at_utc, ends_at_utc)
SHIFTS = [
    ("A1", "w-anna", "floor", "2025-03-03T14:00:00Z", "2025-03-03T22:00:00Z"),
    ("A2", "w-anna", "floor", "2025-03-04T14:00:00Z", "2025-03-04T22:00:00Z"),
    ("A3", "w-anna", "floor", "2025-03-05T14:00:00Z", "2025-03-05T22:00:00Z"),
    ("A4", "w-anna", "floor", "2025-03-06T14:00:00Z", "2025-03-06T22:00:00Z"),
    ("A5", "w-anna", "floor", "2025-03-07T14:00:00Z", "2025-03-07T22:00:00Z"),
    ("A6", "w-anna", "night", "2025-03-09T03:00:00Z", "2025-03-09T10:00:00Z"),
    ("A7", "w-anna", "floor", "2025-10-27T13:00:00Z", "2025-10-27T21:00:00Z"),
    ("A8", "w-anna", "night", "2025-11-02T02:00:00Z", "2025-11-02T11:00:00Z"),
    ("B1", "w-ben", "dock", "2025-03-03T13:00:00Z", "2025-03-03T17:00:00Z"),
    ("B2", "w-ben", "dock", "2025-03-03T17:00:00Z", "2025-03-03T21:00:00Z"),
    ("B3", "w-ben", "dock", "2025-03-04T13:00:00Z", "2025-03-05T01:00:00Z"),
    ("B4", "w-ben", "dock", "2025-03-05T13:00:00Z", "2025-03-06T01:00:00Z"),
    ("B5", "w-ben", "dock", "2025-03-06T13:00:00Z", "2025-03-06T21:00:00Z"),
    ("C1", "w-cleo", "night", "2025-03-03T02:00:00Z", "2025-03-03T10:00:00Z"),
    ("C2", "w-cleo", "night", "2025-03-10T00:00:00Z", "2025-03-10T08:00:00Z"),
]

REPORT_KEYS = {
    "worker_code",
    "iso_year",
    "iso_week",
    "week_start_local",
    "week_end_local",
    "total_hours",
    "regular_hours",
    "overtime_hours",
    "shifts",
}
SHIFT_ENTRY_KEYS = {
    "shift_id",
    "role",
    "starts_at_utc",
    "ends_at_utc",
    "starts_at_local",
    "ends_at_local",
    "hours",
}
TOTALS_KEYS = {
    "worker_code",
    "total_hours",
    "regular_hours",
    "overtime_hours",
    "shift_count",
}
SLOT_KEYS = {"start_local", "end_local", "minutes"}


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def gel_server():
    """Start the local Gel server and block until it accepts connections."""
    proc = subprocess.run(
        ["gel-start"], capture_output=True, text=True, timeout=600
    )
    print("gel-start stdout:\n" + (proc.stdout or ""))
    print("gel-start stderr:\n" + (proc.stderr or ""))
    assert proc.returncode == 0, (
        f"`gel-start` failed with exit code {proc.returncode}. "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )

    deadline = time.time() + 240
    last_error = None
    while time.time() < deadline:
        try:
            probe = gel.create_client()
            try:
                probe.query_single("select 1")
            finally:
                probe.close()
            return True
        except Exception as exc:  # noqa: BLE001 - readiness polling
            last_error = exc
            time.sleep(2)
    pytest.fail(
        "The local Gel server never became reachable after `gel-start`. "
        f"Last connection error: {last_error!r}"
    )


@pytest.fixture(scope="session")
def client(gel_server):
    conn = gel.create_client()
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def engine(gel_server):
    assert os.path.isfile(ENGINE_PATH), (
        f"Expected the scheduling module at {ENGINE_PATH}, but it does not exist."
    )
    if PROJECT_DIR not in sys.path:
        sys.path.insert(0, PROJECT_DIR)
    module = importlib.import_module("shiftops_engine")
    for fn in ("assign_shift", "worker_week_report", "weekly_totals", "free_slots"):
        assert callable(getattr(module, fn, None)), (
            f"`shiftops_engine` does not expose a callable named `{fn}`."
        )
    return module


def _wipe(conn):
    conn.execute("delete Shift;")
    conn.execute("delete AvailabilityWindow;")
    conn.execute("delete Worker;")


def _insert_worker(conn, code, full_name):
    conn.execute(
        "insert Worker { code := <str>$code, full_name := <str>$full_name }",
        code=code,
        full_name=full_name,
    )


def _insert_window(conn, code, dow, start_time, end_time):
    conn.execute(
        """
        insert AvailabilityWindow {
          worker := assert_single((select Worker filter .code = <str>$code)),
          iso_weekday := <int64>$dow,
          start_time := <cal::local_time><str>$st,
          end_time := <cal::local_time><str>$et
        }
        """,
        code=code,
        dow=dow,
        st=start_time,
        et=end_time,
    )


def _raw_insert_shift(conn, code, role, starts_at, ends_at):
    return conn.query_single(
        """
        select (
          insert Shift {
            worker := assert_single((select Worker filter .code = <str>$code)),
            role := <str>$role,
            starts_at := <datetime><str>$s,
            ends_at := <datetime><str>$e
          }
        ).id
        """,
        code=code,
        role=role,
        s=starts_at,
        e=ends_at,
    )


def _shift_count(conn):
    return conn.query_single("select count(Shift)")


@pytest.fixture
def seeded(client, engine):
    """Wipe the database and load the deterministic baseline dataset."""
    _wipe(client)
    for code, full_name in WORKERS:
        _insert_worker(client, code, full_name)
    for code, dow, start_time, end_time in WINDOWS:
        _insert_window(client, code, dow, start_time, end_time)

    ids = {}
    for tag, code, role, starts_at, ends_at in SHIFTS:
        result = engine.assign_shift(client, code, role, starts_at, ends_at)
        assert isinstance(result, dict), (
            f"assign_shift must return a dict, got {type(result)!r} for {tag}."
        )
        assert result.get("status") == "created", (
            f"Seeding shift {tag} ({code} {starts_at} -> {ends_at}) should have "
            f"succeeded, but assign_shift returned {result!r}."
        )
        assert set(result.keys()) == {"status", "shift_id"}, (
            "A successful assign_shift result must have exactly the keys "
            f"'status' and 'shift_id', got {sorted(result.keys())}."
        )
        ids[tag] = str(result["shift_id"])

    assert _shift_count(client) == 15, (
        "The baseline seed must leave exactly 15 shifts in the database, found "
        f"{_shift_count(client)}."
    )
    return ids


# --------------------------------------------------------------------------
# schema & migrations
# --------------------------------------------------------------------------


def test_schema_shape(client):
    rows = client.query(
        """
        select schema::ObjectType {
          name,
          pointers: {
            name,
            required,
            cardinality,
            target: { name }
          } filter .name != '__type__'
        }
        filter .name in {
          'default::Worker', 'default::Shift', 'default::AvailabilityWindow'
        }
        """
    )
    by_type = {}
    for row in rows:
        by_type[row.name] = {
            p.name: (p.target.name if p.target else None, str(p.cardinality), p.required)
            for p in row.pointers
        }

    for expected in (
        "default::Worker",
        "default::Shift",
        "default::AvailabilityWindow",
    ):
        assert expected in by_type, (
            f"Object type `{expected}` is missing from the schema. Found: "
            f"{sorted(by_type)}"
        )

    expectations = {
        "default::Worker": {
            "code": "std::str",
            "full_name": "std::str",
        },
        "default::Shift": {
            "worker": "default::Worker",
            "role": "std::str",
            "starts_at": "std::datetime",
            "ends_at": "std::datetime",
            "span": "range<std::datetime>",
        },
        "default::AvailabilityWindow": {
            "worker": "default::Worker",
            "iso_weekday": "std::int64",
            "start_time": "std::cal::local_time",
            "end_time": "std::cal::local_time",
        },
    }
    for type_name, pointers in expectations.items():
        for pointer_name, target_name in pointers.items():
            assert pointer_name in by_type[type_name], (
                f"`{type_name}` is missing the pointer `{pointer_name}`. Found: "
                f"{sorted(by_type[type_name])}"
            )
            actual_target, cardinality, required = by_type[type_name][pointer_name]
            assert actual_target == target_name, (
                f"`{type_name}.{pointer_name}` must target `{target_name}`, got "
                f"`{actual_target}`."
            )
            assert cardinality == "One", (
                f"`{type_name}.{pointer_name}` must be single-valued, got "
                f"cardinality `{cardinality}`."
            )
            assert required, f"`{type_name}.{pointer_name}` must be required."


def test_worker_code_is_exclusive(client):
    rows = client.query(
        """
        select schema::ObjectType {
          properties: { name, constraints: { name } }
        }
        filter .name = 'default::Worker'
        """
    )
    assert rows, "Object type `default::Worker` was not found."
    names = []
    for prop in rows[0].properties:
        if prop.name == "code":
            names = [c.name for c in prop.constraints]
    assert "std::exclusive" in names, (
        "`Worker.code` must carry an `exclusive` constraint; found constraints: "
        f"{names}"
    )


def test_migrations_exist_and_are_in_sync(client):
    files = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql")))
    assert files, (
        f"No migration files found in {MIGRATIONS_DIR}; the schema must be "
        "delivered as a real Gel migration."
    )
    applied = client.query_single("select count(schema::Migration)")
    assert applied > 0, (
        "No migrations are recorded in the database (`schema::Migration` is "
        "empty); the migration was never applied."
    )
    proc = subprocess.run(
        ["gel", "migration", "status"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=300,
    )
    combined = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, (
        "`gel migration status` reports the project is not in sync "
        f"(exit code {proc.returncode}): {combined}"
    )
    assert "up to date" in combined.lower(), (
        f"`gel migration status` did not report an up-to-date database: {combined}"
    )


# --------------------------------------------------------------------------
# database-enforced invariants
# --------------------------------------------------------------------------


def test_span_is_derived_and_half_open(client, seeded):
    mismatched = client.query_single(
        "select count((select Shift filter .span != range(.starts_at, .ends_at)))"
    )
    assert mismatched == 0, (
        f"{mismatched} Shift object(s) have a `span` that is not equal to "
        "range(.starts_at, .ends_at)."
    )
    bad_bounds = client.query_single(
        """
        select count((
          select Shift
          filter range_is_inclusive_upper(.span)
             or not range_is_inclusive_lower(.span)
        ))
        """
    )
    assert bad_bounds == 0, (
        f"{bad_bounds} Shift object(s) have a `span` that is not half-open "
        "[starts_at, ends_at)."
    )


def test_overlap_invariant_rejects_raw_edgeql_overlap(client, seeded):
    before = _shift_count(client)
    with pytest.raises(GEL_ERRORS):
        _raw_insert_shift(
            client,
            "w-anna",
            "floor",
            "2025-03-03T21:00:00Z",
            "2025-03-03T23:00:00Z",
        )
    assert _shift_count(client) == before, (
        "An overlapping raw-EdgeQL insert was rejected but still changed the "
        "shift count; the rejected write must leave the database untouched."
    )


def test_overlap_invariant_allows_exactly_adjacent_raw_insert(client, seeded):
    before = _shift_count(client)
    probe_id = _raw_insert_shift(
        client,
        "w-anna",
        "floor",
        "2025-03-03T22:00:00Z",
        "2025-03-04T00:00:00Z",
    )
    assert _shift_count(client) == before + 1, (
        "A shift that starts exactly when another one ends must be accepted "
        "(overlap uses half-open semantics)."
    )
    client.execute(
        "delete Shift filter .id = <uuid><str>$sid", sid=str(probe_id)
    )
    assert _shift_count(client) == before


def test_overlap_invariant_rejects_conflicting_update(client, seeded):
    with pytest.raises(GEL_ERRORS):
        client.execute(
            """
            update Shift
            filter .id = <uuid><str>$sid
            set {
              starts_at := <datetime>'2025-03-03T16:00:00Z',
              ends_at := <datetime>'2025-03-03T20:00:00Z'
            }
            """,
            sid=seeded["B2"],
        )
    still = client.query_single(
        "select <str>(select Shift filter .id = <uuid><str>$sid).starts_at",
        sid=seeded["B2"],
    )
    assert still.startswith("2025-03-03T17:00:00"), (
        "An update that would make two shifts of the same worker overlap must be "
        f"rejected and leave the row unchanged; starts_at is now {still!r}."
    )


def test_different_workers_may_share_identical_spans(client, seeded):
    before = _shift_count(client)
    probe_id = _raw_insert_shift(
        client,
        "w-cleo",
        "floor",
        "2025-03-03T14:00:00Z",
        "2025-03-03T22:00:00Z",
    )
    assert _shift_count(client) == before + 1, (
        "Two different workers must be allowed to hold identical spans."
    )
    client.execute(
        "delete Shift filter .id = <uuid><str>$sid", sid=str(probe_id)
    )


def test_degenerate_shift_spans_are_rejected(client, seeded):
    before = _shift_count(client)
    with pytest.raises(GEL_ERRORS):
        _raw_insert_shift(
            client,
            "w-anna",
            "floor",
            "2025-04-01T10:00:00Z",
            "2025-04-01T10:00:00Z",
        )
    with pytest.raises(GEL_ERRORS):
        _raw_insert_shift(
            client,
            "w-anna",
            "floor",
            "2025-04-01T12:00:00Z",
            "2025-04-01T10:00:00Z",
        )
    assert _shift_count(client) == before, (
        "Rejected degenerate shifts must not be persisted."
    )


def test_invalid_availability_windows_are_rejected(client, seeded):
    before = client.query_single("select count(AvailabilityWindow)")
    with pytest.raises(GEL_ERRORS):
        _insert_window(client, "w-anna", 5, "09:00:00", "09:00:00")
    with pytest.raises(GEL_ERRORS):
        _insert_window(client, "w-anna", 5, "12:00:00", "09:00:00")
    with pytest.raises(GEL_ERRORS):
        _insert_window(client, "w-anna", 0, "09:00:00", "10:00:00")
    with pytest.raises(GEL_ERRORS):
        _insert_window(client, "w-anna", 8, "09:00:00", "10:00:00")
    after = client.query_single("select count(AvailabilityWindow)")
    assert after == before, (
        "Rejected availability windows must not be persisted "
        f"(count went from {before} to {after})."
    )


# --------------------------------------------------------------------------
# assign_shift
# --------------------------------------------------------------------------


def test_assign_shift_reports_single_conflict(client, engine, seeded):
    result = engine.assign_shift(
        client, "w-anna", "floor", "2025-03-03T16:00:00Z", "2025-03-03T18:00:00Z"
    )
    assert isinstance(result, dict), f"Expected a dict, got {type(result)!r}."
    assert set(result.keys()) == {"status", "conflicts"}, (
        "A conflict result must have exactly the keys 'status' and 'conflicts', "
        f"got {sorted(result.keys())}."
    )
    assert result["status"] == "conflict", (
        f"Expected status 'conflict' for an overlapping request, got {result!r}."
    )
    assert [str(x) for x in result["conflicts"]] == [seeded["A1"]], (
        "Expected the conflict list to contain exactly the id of the Monday "
        f"09:00-17:00 shift, got {result['conflicts']!r}."
    )
    assert _shift_count(client) == 15, (
        "A conflicting assign_shift must not persist anything."
    )


def test_assign_shift_reports_multiple_conflicts_in_order(client, engine, seeded):
    result = engine.assign_shift(
        client, "w-ben", "dock", "2025-03-03T16:00:00Z", "2025-03-03T18:00:00Z"
    )
    assert result.get("status") == "conflict", (
        f"Expected status 'conflict' spanning two adjacent shifts, got {result!r}."
    )
    assert [str(x) for x in result["conflicts"]] == [seeded["B1"], seeded["B2"]], (
        "Conflicts must be ordered by the conflicting shift's starts_at "
        f"ascending; expected [B1, B2] ids, got {result['conflicts']!r}."
    )
    assert _shift_count(client) == 15, (
        "A conflicting assign_shift must not persist anything."
    )


def test_assign_shift_rejects_unknown_worker(client, engine, seeded):
    result = engine.assign_shift(
        client, "w-nobody", "floor", "2025-03-03T14:00:00Z", "2025-03-03T15:00:00Z"
    )
    assert result == {"status": "invalid", "reason": "unknown_worker"}, (
        f"Expected an unknown_worker result, got {result!r}."
    )
    assert _shift_count(client) == 15


def test_assign_shift_rejects_empty_span(client, engine, seeded):
    equal = engine.assign_shift(
        client, "w-anna", "floor", "2025-03-20T14:00:00Z", "2025-03-20T14:00:00Z"
    )
    assert equal == {"status": "invalid", "reason": "empty_span"}, (
        f"Expected an empty_span result when ends_at == starts_at, got {equal!r}."
    )
    reversed_span = engine.assign_shift(
        client, "w-anna", "floor", "2025-03-20T15:00:00Z", "2025-03-20T14:00:00Z"
    )
    assert reversed_span == {"status": "invalid", "reason": "empty_span"}, (
        "Expected an empty_span result when ends_at is before starts_at, got "
        f"{reversed_span!r}."
    )
    assert _shift_count(client) == 15


def test_assign_shift_check_precedence(client, engine, seeded):
    result = engine.assign_shift(
        client, "w-nobody", "floor", "2025-03-20T15:00:00Z", "2025-03-20T14:00:00Z"
    )
    assert result == {"status": "invalid", "reason": "unknown_worker"}, (
        "An unknown worker must be reported before the empty-span check, got "
        f"{result!r}."
    )
    assert _shift_count(client) == 15


# --------------------------------------------------------------------------
# worker_week_report
# --------------------------------------------------------------------------


def _check_report_shape(report):
    assert isinstance(report, dict), f"Expected a dict, got {type(report)!r}."
    assert set(report.keys()) == REPORT_KEYS, (
        f"worker_week_report must return exactly the keys {sorted(REPORT_KEYS)}, "
        f"got {sorted(report.keys())}."
    )
    assert isinstance(report["shifts"], list), "`shifts` must be a list."
    for entry in report["shifts"]:
        assert set(entry.keys()) == SHIFT_ENTRY_KEYS, (
            "Every shift entry must have exactly the keys "
            f"{sorted(SHIFT_ENTRY_KEYS)}, got {sorted(entry.keys())}."
        )


def test_week_report_handles_dst_spring_forward(client, engine, seeded):
    report = engine.worker_week_report(client, "w-anna", 2025, 10)
    _check_report_shape(report)
    assert report["worker_code"] == "w-anna"
    assert report["iso_year"] == 2025 and report["iso_week"] == 10
    assert report["week_start_local"] == "2025-03-03T00:00:00", (
        f"Wrong week start, got {report['week_start_local']!r}."
    )
    assert report["week_end_local"] == "2025-03-10T00:00:00", (
        f"Wrong week end, got {report['week_end_local']!r}."
    )
    assert report["total_hours"] == pytest.approx(47.0, abs=1e-6), (
        "Week 10 for w-anna is five 8h day shifts plus one overnight shift that "
        "loses an hour to the spring-forward transition, i.e. 47.0 hours; got "
        f"{report['total_hours']!r}."
    )
    assert report["regular_hours"] == pytest.approx(40.0, abs=1e-6)
    assert report["overtime_hours"] == pytest.approx(7.0, abs=1e-6)

    ids = [str(entry["shift_id"]) for entry in report["shifts"]]
    assert ids == [seeded[t] for t in ("A1", "A2", "A3", "A4", "A5", "A6")], (
        f"Shifts must be ordered by starts_at ascending; got {ids}."
    )

    overnight = report["shifts"][-1]
    assert overnight["role"] == "night"
    assert overnight["starts_at_utc"] == "2025-03-09T03:00:00Z", (
        f"Wrong UTC start rendering, got {overnight['starts_at_utc']!r}."
    )
    assert overnight["ends_at_utc"] == "2025-03-09T10:00:00Z", (
        f"Wrong UTC end rendering, got {overnight['ends_at_utc']!r}."
    )
    assert overnight["starts_at_local"] == "2025-03-08T22:00:00", (
        f"Wrong local start, got {overnight['starts_at_local']!r}."
    )
    assert overnight["ends_at_local"] == "2025-03-09T06:00:00", (
        f"Wrong local end, got {overnight['ends_at_local']!r}."
    )
    assert overnight["hours"] == pytest.approx(7.0, abs=1e-6), (
        "22:00 -> 06:00 across the spring-forward transition is 7 elapsed hours, "
        f"not 8; got {overnight['hours']!r}."
    )


def test_week_report_overtime_boundary_is_exclusive(client, engine, seeded):
    report = engine.worker_week_report(client, "w-ben", 2025, 10)
    _check_report_shape(report)
    assert report["total_hours"] == pytest.approx(40.0, abs=1e-6), (
        f"w-ben works exactly 40.0 hours in week 10; got {report['total_hours']!r}."
    )
    assert report["regular_hours"] == pytest.approx(40.0, abs=1e-6)
    assert report["overtime_hours"] == pytest.approx(0.0, abs=1e-6), (
        "A week totalling exactly 40.0 hours must have zero overtime, got "
        f"{report['overtime_hours']!r}."
    )
    assert len(report["shifts"]) == 5, (
        f"Expected 5 shifts for w-ben in week 10, got {len(report['shifts'])}."
    )


def test_week_report_attributes_by_local_start(client, engine, seeded):
    week10 = engine.worker_week_report(client, "w-cleo", 2025, 10)
    _check_report_shape(week10)
    assert week10["total_hours"] == pytest.approx(8.0, abs=1e-6)
    assert len(week10["shifts"]) == 1, (
        "Only the Sunday-night shift that starts locally on 2025-03-09 belongs "
        f"to week 10; got {len(week10['shifts'])} shifts."
    )
    assert week10["shifts"][0]["starts_at_local"] == "2025-03-09T20:00:00", (
        f"Got {week10['shifts'][0]['starts_at_local']!r}."
    )
    assert week10["shifts"][0]["ends_at_local"] == "2025-03-10T04:00:00", (
        "A shift that ends in the following ISO week still counts entirely "
        f"toward the week of its local start; got {week10['shifts'][0]!r}."
    )

    week9 = engine.worker_week_report(client, "w-cleo", 2025, 9)
    _check_report_shape(week9)
    assert week9["week_start_local"] == "2025-02-24T00:00:00", (
        f"Got {week9['week_start_local']!r}."
    )
    assert week9["week_end_local"] == "2025-03-03T00:00:00", (
        f"Got {week9['week_end_local']!r}."
    )
    assert week9["total_hours"] == pytest.approx(8.0, abs=1e-6)
    assert len(week9["shifts"]) == 1
    assert week9["shifts"][0]["starts_at_local"] == "2025-03-02T21:00:00", (
        "The Sunday-evening shift starting 2025-03-02 belongs to ISO week 9; got "
        f"{week9['shifts'][0]['starts_at_local']!r}."
    )

    week11 = engine.worker_week_report(client, "w-cleo", 2025, 11)
    _check_report_shape(week11)
    assert week11["total_hours"] == pytest.approx(0.0, abs=1e-6)
    assert week11["regular_hours"] == pytest.approx(0.0, abs=1e-6)
    assert week11["overtime_hours"] == pytest.approx(0.0, abs=1e-6)
    assert week11["shifts"] == [], (
        f"Expected no shifts for w-cleo in week 11, got {week11['shifts']!r}."
    )


def test_week_report_handles_dst_fall_back(client, engine, seeded):
    report = engine.worker_week_report(client, "w-anna", 2025, 44)
    _check_report_shape(report)
    assert report["week_start_local"] == "2025-10-27T00:00:00", (
        f"Got {report['week_start_local']!r}."
    )
    assert report["week_end_local"] == "2025-11-03T00:00:00", (
        f"Got {report['week_end_local']!r}."
    )
    assert report["total_hours"] == pytest.approx(17.0, abs=1e-6), (
        "Week 44 for w-anna is an 8h Monday shift plus a fall-back overnight "
        f"shift worth 9 elapsed hours; got {report['total_hours']!r}."
    )
    assert report["overtime_hours"] == pytest.approx(0.0, abs=1e-6)
    fallback = report["shifts"][-1]
    assert fallback["starts_at_local"] == "2025-11-01T22:00:00", (
        f"Got {fallback['starts_at_local']!r}."
    )
    assert fallback["ends_at_local"] == "2025-11-02T06:00:00", (
        f"Got {fallback['ends_at_local']!r}."
    )
    assert fallback["hours"] == pytest.approx(9.0, abs=1e-6), (
        "22:00 -> 06:00 across the fall-back transition is 9 elapsed hours, not "
        f"8; got {fallback['hours']!r}."
    )


def test_unknown_worker_raises_lookup_error(client, engine, seeded):
    with pytest.raises(LookupError):
        engine.worker_week_report(client, "w-nobody", 2025, 10)
    with pytest.raises(LookupError):
        engine.free_slots(client, "w-nobody", "2025-03-03")


# --------------------------------------------------------------------------
# weekly_totals
# --------------------------------------------------------------------------


def _as_totals_tuple(row):
    assert set(row.keys()) == TOTALS_KEYS, (
        f"weekly_totals rows must have exactly the keys {sorted(TOTALS_KEYS)}, "
        f"got {sorted(row.keys())}."
    )
    return (
        row["worker_code"],
        round(float(row["total_hours"]), 2),
        round(float(row["regular_hours"]), 2),
        round(float(row["overtime_hours"]), 2),
        row["shift_count"],
    )


def test_weekly_totals_for_a_busy_week(client, engine, seeded):
    rows = engine.weekly_totals(client, 2025, 10)
    assert isinstance(rows, list), f"weekly_totals must return a list, got {type(rows)!r}."
    actual = [_as_totals_tuple(r) for r in rows]
    expected = [
        ("w-anna", 47.0, 40.0, 7.0, 6),
        ("w-ben", 40.0, 40.0, 0.0, 5),
        ("w-cleo", 8.0, 8.0, 0.0, 1),
    ]
    assert actual == expected, (
        f"weekly_totals(2025, 10) should be {expected}, got {actual}."
    )


def test_weekly_totals_for_sparse_and_empty_weeks(client, engine, seeded):
    week9 = [_as_totals_tuple(r) for r in engine.weekly_totals(client, 2025, 9)]
    assert week9 == [("w-cleo", 8.0, 8.0, 0.0, 1)], (
        f"weekly_totals(2025, 9) should list only w-cleo, got {week9}."
    )

    week44 = [_as_totals_tuple(r) for r in engine.weekly_totals(client, 2025, 44)]
    assert week44 == [("w-anna", 17.0, 17.0, 0.0, 2)], (
        f"weekly_totals(2025, 44) should list only w-anna, got {week44}."
    )

    week12 = engine.weekly_totals(client, 2025, 12)
    assert week12 == [], (
        f"weekly_totals(2025, 12) must be an empty list, got {week12!r}."
    )


# --------------------------------------------------------------------------
# free_slots
# --------------------------------------------------------------------------


def _as_slots(rows):
    assert isinstance(rows, list), f"free_slots must return a list, got {type(rows)!r}."
    out = []
    for row in rows:
        assert set(row.keys()) == SLOT_KEYS, (
            f"free_slots rows must have exactly the keys {sorted(SLOT_KEYS)}, got "
            f"{sorted(row.keys())}."
        )
        out.append((row["start_local"], row["end_local"], int(row["minutes"])))
    return out


def test_free_slots_around_a_day_shift(client, engine, seeded):
    monday = _as_slots(engine.free_slots(client, "w-anna", "2025-03-03"))
    assert monday == [
        ("08:00:00", "09:00:00", 60),
        ("17:00:00", "18:00:00", 60),
    ], f"Unexpected free slots for w-anna on 2025-03-03: {monday}."


def test_free_slots_fully_consumed_window(client, engine, seeded):
    tuesday = engine.free_slots(client, "w-anna", "2025-03-04")
    assert tuesday == [], (
        "The 09:00-12:00 Tuesday window is entirely covered by the 09:00-17:00 "
        f"shift, so no slots remain; got {tuesday!r}."
    )


def test_free_slots_with_two_windows_on_one_day(client, engine, seeded):
    wednesday = _as_slots(engine.free_slots(client, "w-anna", "2025-03-05"))
    assert wednesday == [("08:00:00", "09:00:00", 60)], (
        "Only the 08:00-09:00 remainder of the first Wednesday window is free; "
        f"got {wednesday}."
    )


def test_free_slots_without_availability(client, engine, seeded):
    thursday = engine.free_slots(client, "w-anna", "2025-03-06")
    assert thursday == [], (
        f"w-anna has no Thursday availability window; got {thursday!r}."
    )


def test_free_slots_touching_shift_consumes_nothing(client, engine, seeded):
    slots = _as_slots(engine.free_slots(client, "w-ben", "2025-03-06"))
    assert slots == [("16:00:00", "20:00:00", 240)], (
        "w-ben's shift ends exactly at local 16:00:00, so the whole 16:00-20:00 "
        f"window stays free; got {slots}."
    )


def test_free_slots_clips_shift_started_the_previous_evening(client, engine, seeded):
    march3 = _as_slots(engine.free_slots(client, "w-cleo", "2025-03-03"))
    assert march3 == [("05:00:00", "12:00:00", 420)], (
        "The overnight shift that began on 2025-03-02 blocks 00:00-05:00 of "
        f"2025-03-03; got {march3}."
    )
    march10 = _as_slots(engine.free_slots(client, "w-cleo", "2025-03-10"))
    assert march10 == [("04:00:00", "12:00:00", 480)], (
        f"Expected 04:00-12:00 free on 2025-03-10; got {march10}."
    )


def test_free_slots_untouched_window(client, engine, seeded):
    march17 = _as_slots(engine.free_slots(client, "w-cleo", "2025-03-17"))
    assert march17 == [("00:00:00", "12:00:00", 720)], (
        "w-cleo has no shifts on 2025-03-17, so the whole Monday window is free; "
        f"got {march17}."
    )


# --------------------------------------------------------------------------
# empty state, mutation, rounding, determinism
# --------------------------------------------------------------------------


def test_empty_database(client, engine):
    _wipe(client)
    _insert_worker(client, "w-solo", "Solo One")

    assert engine.weekly_totals(client, 2025, 10) == [], (
        "weekly_totals must be an empty list when no shifts exist."
    )
    report = engine.worker_week_report(client, "w-solo", 2025, 10)
    _check_report_shape(report)
    assert report["total_hours"] == pytest.approx(0.0, abs=1e-6)
    assert report["regular_hours"] == pytest.approx(0.0, abs=1e-6)
    assert report["overtime_hours"] == pytest.approx(0.0, abs=1e-6)
    assert report["shifts"] == []
    assert report["week_start_local"] == "2025-03-03T00:00:00"
    assert engine.free_slots(client, "w-solo", "2025-03-03") == [], (
        "A worker with no availability windows has no free slots."
    )


def test_mutation_then_recompute(client, engine, seeded):
    created = engine.assign_shift(
        client, "w-anna", "floor", "2025-03-03T22:00:00Z", "2025-03-04T00:00:00Z"
    )
    assert created.get("status") == "created", (
        "A shift starting exactly when another ends must be accepted; got "
        f"{created!r}."
    )
    assert _shift_count(client) == 16

    report = engine.worker_week_report(client, "w-anna", 2025, 10)
    assert report["total_hours"] == pytest.approx(49.0, abs=1e-6), (
        f"Expected 47.0 + 2.0 = 49.0 hours after the new shift, got "
        f"{report['total_hours']!r}."
    )
    assert report["regular_hours"] == pytest.approx(40.0, abs=1e-6)
    assert report["overtime_hours"] == pytest.approx(9.0, abs=1e-6)
    assert len(report["shifts"]) == 7, (
        f"Expected 7 shifts for w-anna in week 10, got {len(report['shifts'])}."
    )

    totals = [_as_totals_tuple(r) for r in engine.weekly_totals(client, 2025, 10)]
    assert totals[0] == ("w-anna", 49.0, 40.0, 9.0, 7), (
        f"weekly_totals must reflect the new shift, got {totals}."
    )


def test_fractional_hours_are_rounded_half_up(client, engine, seeded):
    created = engine.assign_shift(
        client, "w-cleo", "night", "2025-03-11T13:00:00Z", "2025-03-11T14:50:00Z"
    )
    assert created.get("status") == "created", (
        f"Expected the 1h50m shift to be created, got {created!r}."
    )
    report = engine.worker_week_report(client, "w-cleo", 2025, 11)
    assert len(report["shifts"]) == 1, (
        f"Expected exactly one shift for w-cleo in week 11, got {report['shifts']!r}."
    )
    assert report["shifts"][0]["hours"] == pytest.approx(1.83, abs=1e-6), (
        "110 minutes is 1.8333... hours, which rounds to 1.83; got "
        f"{report['shifts'][0]['hours']!r}."
    )
    assert report["total_hours"] == pytest.approx(1.83, abs=1e-6), (
        f"Expected total_hours 1.83, got {report['total_hours']!r}."
    )
    assert report["overtime_hours"] == pytest.approx(0.0, abs=1e-6)


def test_reports_are_deterministic(client, engine, seeded):
    first = engine.worker_week_report(client, "w-anna", 2025, 10)
    second = engine.worker_week_report(client, "w-anna", 2025, 10)
    assert first == second, (
        "Repeated calls with identical arguments must return identical payloads."
    )
    totals_first = engine.weekly_totals(client, 2025, 10)
    totals_second = engine.weekly_totals(client, 2025, 10)
    assert totals_first == totals_second, (
        "weekly_totals must be deterministic across repeated calls."
    )
    slots_first = engine.free_slots(client, "w-anna", "2025-03-03")
    slots_second = engine.free_slots(client, "w-anna", "2025-03-03")
    assert slots_first == slots_second, (
        "free_slots must be deterministic across repeated calls."
    )


def test_engine_imports_without_a_database_connection(gel_server):
    env = dict(os.environ)
    env["GEL_INSTANCE"] = "no_such_instance_zzz"
    env.pop("GEL_DSN", None)
    proc = subprocess.run(
        [sys.executable, "-c", "import shiftops_engine; print('IMPORT_OK')"],
        cwd=PROJECT_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0 and "IMPORT_OK" in proc.stdout, (
        "`shiftops_engine` must import cleanly without any usable database "
        f"connection. exit={proc.returncode} stdout={proc.stdout!r} "
        f"stderr={proc.stderr!r}"
    )
