"""Final-state verification for the `gel_datetime_duration_timezone_reporting_cli` task.

Everything is verified by executing the real tooling against the real local Gel
server: the `gel` CLI is used for schema/migration/data introspection, and the
shipped shell entrypoints are invoked as a user would invoke them.
"""

import glob
import json
import os
import re
import shutil
import subprocess

import pytest

PROJECT_DIR = "/home/user/usage-report"
SCRIPTS_DIR = os.path.join(PROJECT_DIR, "scripts")
QUERIES_DIR = os.path.join(PROJECT_DIR, "queries")
MIGRATIONS_DIR = os.path.join(PROJECT_DIR, "dbschema", "migrations")
VERIFY_DIR = os.path.join(PROJECT_DIR, "out", "verify")

SEED_SH = os.path.join(SCRIPTS_DIR, "seed.sh")
REPORT_SH = os.path.join(SCRIPTS_DIR, "report.sh")
CALENDAR_SH = os.path.join(SCRIPTS_DIR, "calendar.sh")

# Neutral working directory: the entrypoints must not depend on the caller's cwd.
NEUTRAL_CWD = "/tmp"

LOCAL_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")
UTC_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

BANNED_SUFFIXES = (".py", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl", ".php")
BANNED_INTERPRETERS = ("python3", "python", "node", "deno", "bun", "ruby", "perl", "php")


# --------------------------------------------------------------------------- #
# helpers / fixtures
# --------------------------------------------------------------------------- #
def _run(args, cwd=NEUTRAL_CWD, timeout=300):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def _gel_json(query, timeout=300):
    """Run an EdgeQL query through the CLI and return the parsed JSON result."""
    proc = _run(["gel", "query", "-F", "json", query], cwd=PROJECT_DIR, timeout=timeout)
    assert proc.returncode == 0, (
        f"EdgeQL query failed ({query!r}):\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    return json.loads(proc.stdout)


def _gel_raw(query, timeout=300):
    """Run an EdgeQL query and return the CompletedProcess (for failure checks)."""
    return _run(["gel", "query", query], cwd=PROJECT_DIR, timeout=timeout)


def _num(value, label):
    assert isinstance(value, (int, float)) and not isinstance(value, bool), (
        f"{label} must be a JSON number, got {value!r} ({type(value).__name__})."
    )
    return value


@pytest.fixture(scope="session")
def gel_server():
    """Ensure the local Gel server is running before any CLI-dependent test."""
    starter = "/usr/local/bin/gel-up"
    if os.path.isfile(starter) and os.access(starter, os.X_OK):
        subprocess.run([starter], capture_output=True, text=True, timeout=600)
    proc = _run(["gel", "query", "-F", "json", "select 1"], cwd=PROJECT_DIR)
    assert proc.returncode == 0, (
        "Local Gel server is not reachable: "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    return True


@pytest.fixture(scope="session", autouse=True)
def clean_verify_dir():
    shutil.rmtree(VERIFY_DIR, ignore_errors=True)
    yield


@pytest.fixture(scope="session")
def schema_introspection(gel_server):
    rows = _gel_json(
        "select schema::ObjectType { "
        "  name, "
        "  type_constraints := .constraints.name, "
        "  pointers: { "
        "    name, computed := exists .expr, "
        "    target: { name }, required, "
        "    pointer_constraints := .constraints.name "
        "  } "
        "} filter .name in {'default::Tenant', 'default::UsageSession'}"
    )
    return {row["name"]: row for row in rows}


def _pointer(introspection, type_name, pointer_name):
    obj = introspection.get(type_name)
    assert obj is not None, f"Object type `{type_name}` does not exist in the database."
    for ptr in obj["pointers"]:
        if ptr["name"] == pointer_name:
            return ptr
    pytest.fail(
        f"`{type_name}` has no pointer named `{pointer_name}`. "
        f"Found: {sorted(p['name'] for p in obj['pointers'])}"
    )


def _target(ptr):
    return (ptr.get("target") or {}).get("name")


def _assert_target(ptr, type_name, pointer_name, accepted):
    actual = _target(ptr)
    assert actual in accepted, (
        f"`{type_name}.{pointer_name}` must target one of {accepted}, got {actual!r}."
    )


def _report(tenant, date_from, date_to, filename, expect_rc=0):
    """Invoke report.sh; returns (CompletedProcess, out_path)."""
    out_path = os.path.join(VERIFY_DIR, filename)
    if os.path.exists(out_path):
        os.remove(out_path)
    proc = _run(
        [
            "bash", REPORT_SH,
            "--tenant", tenant,
            "--from", date_from,
            "--to", date_to,
            "--out", out_path,
        ]
    )
    assert proc.returncode == expect_rc, (
        f"`report.sh --tenant {tenant} --from {date_from} --to {date_to}` exited "
        f"{proc.returncode}, expected {expect_rc}.\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    return proc, out_path


def _report_json(tenant, date_from, date_to, filename):
    _, out_path = _report(tenant, date_from, date_to, filename)
    assert os.path.isfile(out_path), f"report.sh did not create {out_path}."
    with open(out_path, encoding="utf-8") as fh:
        return json.load(fh)


def _calendar(date, months, days, filename, expect_rc=0):
    """Invoke calendar.sh; returns (CompletedProcess, out_path)."""
    out_path = os.path.join(VERIFY_DIR, filename)
    if os.path.exists(out_path):
        os.remove(out_path)
    proc = _run(
        [
            "bash", CALENDAR_SH,
            "--date", date,
            "--months", str(months),
            "--days", str(days),
            "--out", out_path,
        ]
    )
    assert proc.returncode == expect_rc, (
        f"`calendar.sh --date {date} --months {months} --days {days}` exited "
        f"{proc.returncode}, expected {expect_rc}.\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    return proc, out_path


def _calendar_json(date, months, days, filename):
    _, out_path = _calendar(date, months, days, filename)
    assert os.path.isfile(out_path), f"calendar.sh did not create {out_path}."
    with open(out_path, encoding="utf-8") as fh:
        return json.load(fh)


def _assert_day(day, local_date, hours, count, seconds, first_local, last_local):
    assert day["local_date"] == local_date, (
        f"Expected day entry {local_date}, got {day['local_date']!r}."
    )
    assert _num(day["hours_in_day"], f"days[{local_date}].hours_in_day") == hours, (
        f"{local_date}: hours_in_day should be {hours}, got {day['hours_in_day']!r}."
    )
    assert _num(day["session_count"], f"days[{local_date}].session_count") == count, (
        f"{local_date}: session_count should be {count}, got {day['session_count']!r}."
    )
    assert (
        _num(day["billable_seconds"], f"days[{local_date}].billable_seconds") == seconds
    ), f"{local_date}: billable_seconds should be {seconds}, got {day['billable_seconds']!r}."
    assert day["first_start_local"] == first_local, (
        f"{local_date}: first_start_local should be {first_local!r}, "
        f"got {day['first_start_local']!r}."
    )
    assert day["last_end_local"] == last_local, (
        f"{local_date}: last_end_local should be {last_local!r}, "
        f"got {day['last_end_local']!r}."
    )
    for key in ("first_start_local", "last_end_local"):
        value = day[key]
        if value is not None:
            assert LOCAL_TS_RE.match(value), (
                f"{local_date}: {key}={value!r} does not match YYYY-MM-DDTHH:MM:SS."
            )


def _assert_totals(report, count, seconds, span_hours):
    totals = report["totals"]
    assert _num(totals["session_count"], "totals.session_count") == count, (
        f"totals.session_count should be {count}, got {totals['session_count']!r}."
    )
    assert _num(totals["billable_seconds"], "totals.billable_seconds") == seconds, (
        f"totals.billable_seconds should be {seconds}, got {totals['billable_seconds']!r}."
    )
    assert _num(totals["span_hours"], "totals.span_hours") == span_hours, (
        f"totals.span_hours should be {span_hours}, got {totals['span_hours']!r}."
    )


def _assert_probe(report, anchor_utc, plus_168, plus_7d, drift):
    probe = report["probe"]
    assert probe["anchor_utc"] == anchor_utc, (
        f"probe.anchor_utc should be {anchor_utc!r}, got {probe['anchor_utc']!r}."
    )
    assert UTC_TS_RE.match(probe["anchor_utc"]), (
        f"probe.anchor_utc={probe['anchor_utc']!r} does not match YYYY-MM-DDTHH:MM:SSZ."
    )
    assert probe["plus_168_hours_local"] == plus_168, (
        f"probe.plus_168_hours_local should be {plus_168!r}, "
        f"got {probe['plus_168_hours_local']!r}."
    )
    assert probe["plus_7_days_local"] == plus_7d, (
        f"probe.plus_7_days_local should be {plus_7d!r}, got {probe['plus_7_days_local']!r}."
    )
    for key in ("plus_168_hours_local", "plus_7_days_local"):
        assert LOCAL_TS_RE.match(probe[key]), (
            f"probe.{key}={probe[key]!r} does not match YYYY-MM-DDTHH:MM:SS."
        )
    assert _num(probe["drift_seconds"], "probe.drift_seconds") == drift, (
        f"probe.drift_seconds should be {drift}, got {probe['drift_seconds']!r}."
    )


# --------------------------------------------------------------------------- #
# 1. schema shape
# --------------------------------------------------------------------------- #
def test_tenant_has_timezone_and_billing_anchor(schema_introspection):
    tz = _pointer(schema_introspection, "default::Tenant", "tz")
    assert tz["required"] is True, "`Tenant.tz` must be required."
    _assert_target(tz, "default::Tenant", "tz", {"std::str"})

    anchor = _pointer(schema_introspection, "default::Tenant", "billing_anchor")
    assert anchor["required"] is True, "`Tenant.billing_anchor` must be required."
    _assert_target(
        anchor,
        "default::Tenant",
        "billing_anchor",
        {"cal::local_date", "std::cal::local_date"},
    )


def test_tenant_keeps_starter_pointers(schema_introspection):
    code = _pointer(schema_introspection, "default::Tenant", "code")
    assert code["required"] is True, "`Tenant.code` must remain required."
    _assert_target(code, "default::Tenant", "code", {"std::str"})
    assert "std::exclusive" in code["pointer_constraints"], (
        "`Tenant.code` must keep its exclusive constraint, "
        f"found {code['pointer_constraints']!r}."
    )
    display = _pointer(schema_introspection, "default::Tenant", "display_name")
    assert display["required"] is True, "`Tenant.display_name` must remain required."
    _assert_target(display, "default::Tenant", "display_name", {"std::str"})


def test_usage_session_scalar_pointers(schema_introspection):
    key = _pointer(schema_introspection, "default::UsageSession", "session_key")
    assert key["required"] is True, "`UsageSession.session_key` must be required."
    _assert_target(key, "default::UsageSession", "session_key", {"std::str"})
    assert "std::exclusive" in key["pointer_constraints"], (
        "`UsageSession.session_key` must carry an exclusive constraint, "
        f"found {key['pointer_constraints']!r}."
    )

    for name in ("started_at", "ended_at"):
        ptr = _pointer(schema_introspection, "default::UsageSession", name)
        assert ptr["required"] is True, f"`UsageSession.{name}` must be required."
        _assert_target(ptr, "default::UsageSession", name, {"std::datetime"})


def test_usage_session_tenant_link(schema_introspection):
    link = _pointer(schema_introspection, "default::UsageSession", "tenant")
    assert link["required"] is True, "`UsageSession.tenant` must be a required link."
    _assert_target(link, "default::UsageSession", "tenant", {"default::Tenant"})


def test_usage_session_computed_pointers(schema_introspection):
    wall = _pointer(schema_introspection, "default::UsageSession", "wall_time")
    assert wall["computed"] is True, "`UsageSession.wall_time` must be a computed."
    _assert_target(wall, "default::UsageSession", "wall_time", {"std::duration"})

    started_on = _pointer(schema_introspection, "default::UsageSession", "started_on_utc")
    assert started_on["computed"] is True, (
        "`UsageSession.started_on_utc` must be a computed."
    )
    _assert_target(
        started_on,
        "default::UsageSession",
        "started_on_utc",
        {"cal::local_date", "std::cal::local_date"},
    )


# --------------------------------------------------------------------------- #
# 2. migrations
# --------------------------------------------------------------------------- #
def test_new_migration_files_exist(gel_server):
    files = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql")))
    assert len(files) >= 2, (
        f"Expected at least 2 migration files in {MIGRATIONS_DIR}, found {files}."
    )


def test_migration_status_up_to_date(gel_server):
    proc = _run(["gel", "migration", "status"], cwd=PROJECT_DIR)
    combined = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, f"`gel migration status` failed: {combined}"
    assert "up to date" in combined.lower(), (
        f"Migration history is not in sync with the database: {combined}"
    )


# --------------------------------------------------------------------------- #
# 3./4. constraints
# --------------------------------------------------------------------------- #
def _insert_session(session_key, started, ended):
    return _gel_raw(
        "insert UsageSession { "
        f"session_key := '{session_key}', "
        "tenant := assert_single((select Tenant filter .code = 'acme-us')), "
        f"started_at := <datetime>'{started}', "
        f"ended_at := <datetime>'{ended}' "
        "}"
    )


def test_reversed_timestamps_are_rejected(gel_server):
    proc = _insert_session("bad-reversed", "2024-01-02T00:00:00Z", "2024-01-01T00:00:00Z")
    assert proc.returncode != 0, (
        "A UsageSession with ended_at earlier than started_at was accepted; "
        "the ordering constraint is missing."
    )
    combined = (proc.stdout or "") + (proc.stderr or "")
    assert "ConstraintViolationError" in combined, (
        f"Expected a ConstraintViolationError, got: {combined}"
    )
    assert _gel_json(
        "select count(UsageSession filter .session_key = 'bad-reversed')"
    ) == [0], "The rejected session `bad-reversed` was persisted anyway."


def test_equal_timestamps_are_rejected(gel_server):
    proc = _insert_session("bad-equal", "2024-01-01T00:00:00Z", "2024-01-01T00:00:00Z")
    assert proc.returncode != 0, (
        "A UsageSession with ended_at equal to started_at was accepted; the ordering "
        "constraint must be strict."
    )
    combined = (proc.stdout or "") + (proc.stderr or "")
    assert "ConstraintViolationError" in combined, (
        f"Expected a ConstraintViolationError, got: {combined}"
    )
    assert _gel_json(
        "select count(UsageSession filter .session_key = 'bad-equal')"
    ) == [0], "The rejected session `bad-equal` was persisted anyway."


def test_duplicate_session_key_is_rejected(gel_server):
    proc = _insert_session("s-us-01", "2024-01-01T00:00:00Z", "2024-01-02T00:00:00Z")
    assert proc.returncode != 0, (
        "A duplicate session_key was accepted; the exclusive constraint is missing."
    )
    combined = (proc.stdout or "") + (proc.stderr or "")
    assert "ConstraintViolationError" in combined, (
        f"Expected a ConstraintViolationError, got: {combined}"
    )


# --------------------------------------------------------------------------- #
# 5. seeded data / idempotency
# --------------------------------------------------------------------------- #
def test_seeded_object_counts(gel_server):
    assert _gel_json("select count(Tenant)") == [3], (
        "Expected exactly 3 Tenant objects after seeding."
    )
    assert _gel_json("select count(UsageSession)") == [18], (
        "Expected exactly 18 UsageSession objects after seeding."
    )


def test_seeded_tenant_rows(gel_server):
    rows = _gel_json("select Tenant { code, tz } order by .code")
    assert rows == [
        {"code": "acme-us", "tz": "America/New_York"},
        {"code": "globex-de", "tz": "Europe/Berlin"},
        {"code": "initech-in", "tz": "Asia/Kolkata"},
    ], f"Unexpected tenant rows in the database: {rows!r}"


def test_seed_script_is_idempotent(gel_server):
    proc = _run(["bash", SEED_SH])
    assert proc.returncode == 0, (
        f"Re-running seed.sh failed: stdout={proc.stdout} stderr={proc.stderr}"
    )
    assert _gel_json("select count(Tenant)") == [3], (
        "Re-running seed.sh changed the Tenant count; it must be idempotent."
    )
    assert _gel_json("select count(UsageSession)") == [18], (
        "Re-running seed.sh changed the UsageSession count; it must be idempotent."
    )


# --------------------------------------------------------------------------- #
# 6. computed values
# --------------------------------------------------------------------------- #
def test_started_on_utc_uses_utc_calendar_date(gel_server):
    rows = _gel_json(
        "select UsageSession { session_key, d := <str>.started_on_utc } "
        "filter .session_key = 's-us-02'"
    )
    assert rows and rows[0]["d"] == "2024-03-09", (
        "`started_on_utc` for s-us-02 (2024-03-09T04:45:00Z) must be the UTC date "
        f"2024-03-09, got {rows!r}."
    )


def test_wall_time_holds_real_elapsed_time(gel_server):
    rows = _gel_json(
        "select UsageSession { session_key, s := <int64>duration_to_seconds(.wall_time) } "
        "filter .session_key = 's-us-04'"
    )
    assert rows and rows[0]["s"] == 16200, (
        f"`wall_time` for s-us-04 must be 16200 seconds, got {rows!r}."
    )


# --------------------------------------------------------------------------- #
# 7.-11. reports
# --------------------------------------------------------------------------- #
def test_report_dst_spring_forward_window(gel_server):
    report = _report_json("acme-us", "2024-03-08", "2024-03-12", "us.json")
    assert report["tenant"] == "acme-us", f"tenant should be 'acme-us', got {report['tenant']!r}."
    assert report["timezone"] == "America/New_York", (
        f"timezone should be 'America/New_York', got {report['timezone']!r}."
    )
    assert report["from"] == "2024-03-08", f"from should be '2024-03-08', got {report['from']!r}."
    assert report["to"] == "2024-03-12", f"to should be '2024-03-12', got {report['to']!r}."

    days = report["days"]
    assert len(days) == 5, f"Expected 5 day entries, got {len(days)}: {days!r}"
    _assert_day(days[0], "2024-03-08", 24, 2, 9000, "2024-03-08T01:30:00", "2024-03-09T01:15:00")
    _assert_day(days[1], "2024-03-09", 24, 0, 0, None, None)
    _assert_day(days[2], "2024-03-10", 23, 1, 3600, "2024-03-10T01:30:00", "2024-03-10T03:30:00")
    _assert_day(days[3], "2024-03-11", 24, 2, 23400, "2024-03-11T09:00:00", "2024-03-12T01:00:00")
    _assert_day(days[4], "2024-03-12", 24, 1, 5400, "2024-03-12T22:00:00", "2024-03-12T23:30:00")

    _assert_totals(report, 6, 41400, 119)
    _assert_probe(
        report, "2024-03-08T05:00:00Z", "2024-03-15T01:00:00", "2024-03-15T00:00:00", -3600
    )


def test_report_dst_fall_back_window(gel_server):
    report = _report_json("globex-de", "2024-10-25", "2024-10-29", "de.json")
    assert report["timezone"] == "Europe/Berlin", (
        f"timezone should be 'Europe/Berlin', got {report['timezone']!r}."
    )
    days = report["days"]
    assert len(days) == 5, f"Expected 5 day entries, got {len(days)}: {days!r}"
    _assert_day(days[0], "2024-10-25", 24, 1, 5400, "2024-10-25T10:00:00", "2024-10-25T11:30:00")
    _assert_day(days[1], "2024-10-26", 24, 1, 3600, "2024-10-26T00:30:00", "2024-10-26T01:30:00")
    _assert_day(days[2], "2024-10-27", 25, 1, 3600, "2024-10-27T02:30:00", "2024-10-27T02:30:00")
    _assert_day(days[3], "2024-10-28", 24, 1, 14400, "2024-10-28T08:00:00", "2024-10-28T12:00:00")
    _assert_day(days[4], "2024-10-29", 24, 1, 2700, "2024-10-29T06:00:00", "2024-10-29T06:45:00")

    _assert_totals(report, 5, 29700, 121)
    _assert_probe(
        report, "2024-10-24T22:00:00Z", "2024-10-31T23:00:00", "2024-11-01T00:00:00", 3600
    )


def test_report_half_hour_offset_and_leap_day(gel_server):
    report = _report_json("initech-in", "2024-02-27", "2024-03-01", "in.json")
    assert report["timezone"] == "Asia/Kolkata", (
        f"timezone should be 'Asia/Kolkata', got {report['timezone']!r}."
    )
    days = report["days"]
    assert len(days) == 4, f"Expected 4 day entries, got {len(days)}: {days!r}"
    _assert_day(days[0], "2024-02-27", 24, 1, 3600, "2024-02-27T01:30:00", "2024-02-27T02:30:00")
    _assert_day(days[1], "2024-02-28", 24, 1, 3600, "2024-02-28T00:15:00", "2024-02-28T01:15:00")
    _assert_day(days[2], "2024-02-29", 24, 2, 14400, "2024-02-29T00:10:00", "2024-02-29T18:00:00")
    _assert_day(days[3], "2024-03-01", 24, 1, 3600, "2024-03-01T00:30:00", "2024-03-01T01:30:00")

    _assert_totals(report, 5, 25200, 96)
    _assert_probe(
        report, "2024-02-26T18:30:00Z", "2024-03-05T00:00:00", "2024-03-05T00:00:00", 0
    )


def test_report_single_twenty_three_hour_day(gel_server):
    report = _report_json("acme-us", "2024-03-10", "2024-03-10", "us1.json")
    days = report["days"]
    assert len(days) == 1, f"Expected exactly 1 day entry, got {len(days)}: {days!r}"
    _assert_day(days[0], "2024-03-10", 23, 1, 3600, "2024-03-10T01:30:00", "2024-03-10T03:30:00")
    _assert_totals(report, 1, 3600, 23)
    _assert_probe(
        report, "2024-03-10T05:00:00Z", "2024-03-17T01:00:00", "2024-03-17T00:00:00", -3600
    )


def test_report_empty_window(gel_server):
    report = _report_json("globex-de", "2024-12-01", "2024-12-02", "empty.json")
    days = report["days"]
    assert len(days) == 2, f"Expected 2 day entries, got {len(days)}: {days!r}"
    _assert_day(days[0], "2024-12-01", 24, 0, 0, None, None)
    _assert_day(days[1], "2024-12-02", 24, 0, 0, None, None)
    _assert_totals(report, 0, 0, 48)
    _assert_probe(
        report, "2024-11-30T23:00:00Z", "2024-12-08T00:00:00", "2024-12-08T00:00:00", 0
    )


# --------------------------------------------------------------------------- #
# 12. anti-hardcoding: the report must reflect live database content
# --------------------------------------------------------------------------- #
def test_report_reflects_live_database_content(gel_server):
    insert = _gel_raw(
        "insert UsageSession { "
        "session_key := 'probe-x', "
        "tenant := assert_single((select Tenant filter .code = 'acme-us')), "
        "started_at := <datetime>'2024-03-09T13:00:00Z', "
        "ended_at := <datetime>'2024-03-09T14:00:00Z' "
        "}"
    )
    assert insert.returncode == 0, (
        f"Could not insert the probe session: {insert.stdout} {insert.stderr}"
    )
    try:
        report = _report_json("acme-us", "2024-03-08", "2024-03-12", "us_probe.json")
        days = report["days"]
        assert len(days) == 5, f"Expected 5 day entries, got {len(days)}: {days!r}"
        _assert_day(
            days[1], "2024-03-09", 24, 1, 3600, "2024-03-09T08:00:00", "2024-03-09T09:00:00"
        )
        _assert_totals(report, 7, 45000, 119)
    finally:
        _gel_raw("delete UsageSession filter .session_key = 'probe-x'")
    assert _gel_json("select count(UsageSession)") == [18], (
        "Could not restore the database to 18 UsageSession objects after the probe."
    )


# --------------------------------------------------------------------------- #
# 13. report error handling
# --------------------------------------------------------------------------- #
def test_report_unknown_tenant_exits_2(gel_server):
    proc, out_path = _report("nope-zz", "2024-03-08", "2024-03-12", "bad1.json", expect_rc=2)
    assert not os.path.exists(out_path), (
        f"report.sh must not create {out_path} when the tenant is unknown."
    )
    assert (proc.stderr or "").strip(), (
        "report.sh must write a diagnostic to stderr when the tenant is unknown."
    )


def test_report_inverted_window_exits_3(gel_server):
    proc, out_path = _report("acme-us", "2024-03-12", "2024-03-08", "bad2.json", expect_rc=3)
    assert not os.path.exists(out_path), (
        f"report.sh must not create {out_path} when --from is later than --to."
    )
    assert (proc.stderr or "").strip(), (
        "report.sh must write a diagnostic to stderr when --from is later than --to."
    )


# --------------------------------------------------------------------------- #
# 14.-18. calendar probes
# --------------------------------------------------------------------------- #
def _assert_calendar(probe, date, months, days, mtd, dtm, combined, round_trip, delta):
    assert probe["date"] == date, f"date should be {date!r}, got {probe['date']!r}."
    assert _num(probe["months"], "months") == months, (
        f"months should be {months}, got {probe['months']!r}."
    )
    assert _num(probe["days"], "days") == days, (
        f"days should be {days}, got {probe['days']!r}."
    )
    assert probe["months_then_days"] == mtd, (
        f"months_then_days should be {mtd!r}, got {probe['months_then_days']!r}."
    )
    assert probe["days_then_months"] == dtm, (
        f"days_then_months should be {dtm!r}, got {probe['days_then_months']!r}."
    )
    assert probe["combined"] == combined, (
        f"combined should be {combined!r}, got {probe['combined']!r}."
    )
    assert probe["round_trip"] == round_trip, (
        f"round_trip should be {round_trip!r}, got {probe['round_trip']!r}."
    )
    assert _num(probe["day_delta"], "day_delta") == delta, (
        f"day_delta should be {delta}, got {probe['day_delta']!r}."
    )


def test_calendar_month_end_into_leap_february(gel_server):
    probe = _calendar_json("2024-01-31", 1, 0, "c1.json")
    _assert_calendar(
        probe, "2024-01-31", 1, 0,
        "2024-02-29", "2024-02-29", "2024-02-29", "2024-01-29", 29,
    )


def test_calendar_order_sensitivity(gel_server):
    probe = _calendar_json("2024-04-30", 1, 1, "c2.json")
    _assert_calendar(
        probe, "2024-04-30", 1, 1,
        "2024-05-31", "2024-06-01", "2024-05-31", "2024-04-29", 31,
    )


def test_calendar_negative_span(gel_server):
    probe = _calendar_json("2024-03-31", -1, 0, "c3.json")
    _assert_calendar(
        probe, "2024-03-31", -1, 0,
        "2024-02-29", "2024-02-29", "2024-02-29", "2024-03-29", -31,
    )


def test_calendar_non_leap_month_end(gel_server):
    probe = _calendar_json("2023-01-31", 1, 0, "c4.json")
    _assert_calendar(
        probe, "2023-01-31", 1, 0,
        "2023-02-28", "2023-02-28", "2023-02-28", "2023-01-28", 28,
    )


def test_calendar_leap_day_plus_twelve_months(gel_server):
    probe = _calendar_json("2024-02-29", 12, 0, "c5.json")
    _assert_calendar(
        probe, "2024-02-29", 12, 0,
        "2025-02-28", "2025-02-28", "2025-02-28", "2024-02-28", 365,
    )


# --------------------------------------------------------------------------- #
# 19. calendar error handling
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "bad_date, filename",
    [("2023-02-30", "c6.json"), ("not-a-date", "c7.json")],
)
def test_calendar_invalid_date_exits_4(gel_server, bad_date, filename):
    proc, out_path = _calendar(bad_date, 1, 0, filename, expect_rc=4)
    assert not os.path.exists(out_path), (
        f"calendar.sh must not create {out_path} for the invalid date {bad_date!r}."
    )
    assert (proc.stderr or "").strip(), (
        f"calendar.sh must write a diagnostic to stderr for the invalid date {bad_date!r}."
    )


# --------------------------------------------------------------------------- #
# 20. deliverables and language restriction
# --------------------------------------------------------------------------- #
def test_required_scripts_exist():
    for path in (SEED_SH, REPORT_SH, CALENDAR_SH):
        assert os.path.isfile(path), f"Required script {path} is missing."


def test_required_edgeql_files_exist():
    for name in ("report.edgeql", "calendar.edgeql"):
        path = os.path.join(QUERIES_DIR, name)
        assert os.path.isfile(path), f"Required EdgeQL file {path} is missing."
        assert os.path.getsize(path) > 0, f"Required EdgeQL file {path} is empty."


def test_no_foreign_language_sources_in_project():
    offenders = []
    for root, dirs, files in os.walk(PROJECT_DIR):
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "out"}]
        for name in files:
            if name.endswith(BANNED_SUFFIXES):
                offenders.append(os.path.join(root, name))
    assert not offenders, (
        "The solution must be shell + EdgeQL only, but these source files were found: "
        f"{offenders}"
    )


def test_scripts_do_not_invoke_other_interpreters():
    offenders = []
    for path in glob.glob(os.path.join(SCRIPTS_DIR, "*")):
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                raw = fh.read()
        except OSError:
            continue
        # Ignore whole-line comments so that a harmless remark cannot fail a
        # perfectly good shell-only solution.
        content = "\n".join(
            line for line in raw.splitlines() if not line.lstrip().startswith("#")
        )
        for interpreter in BANNED_INTERPRETERS:
            if re.search(rf"(?<![\w.-]){re.escape(interpreter)}(?![\w.-])", content):
                offenders.append((path, interpreter))
    assert not offenders, (
        "The shell entrypoints must not invoke another language runtime, found: "
        f"{offenders}"
    )
