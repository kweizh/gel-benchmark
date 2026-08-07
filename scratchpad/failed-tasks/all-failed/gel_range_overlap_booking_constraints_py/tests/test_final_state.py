import asyncio
import glob
import json
import os
import subprocess
import sys
import time
import uuid

import gel
import pytest

PROJECT_DIR = "/home/user/booking"
SERVICE_FILE = os.path.join(PROJECT_DIR, "booking_service.py")
CLI_FILE = os.path.join(PROJECT_DIR, "booking_cli.py")
MIGRATIONS_DIR = os.path.join(PROJECT_DIR, "dbschema", "migrations")
START_SCRIPT = "/usr/local/bin/gel-start.sh"

RESOURCES = (
    ("alpha", "Alpha Room", 4),
    ("beta", "Beta Room", 10),
    ("gamma", "Gamma Lab", 2),
)

BOOK_KEYS = {"id", "resource", "start", "end", "booked_by", "duration_minutes"}
AVAIL_KEYS = {
    "resource",
    "window_start",
    "window_end",
    "booked",
    "free",
    "booked_minutes",
    "free_minutes",
}
UTIL_KEYS = {"code", "reservation_count", "booked_minutes", "utilization_pct"}

RAW_INSERT = """
insert Reservation {
    resource := assert_exists((select Resource filter .code = <str>$code)),
    period := <range<cal::local_datetime>>to_json(<str>$period),
    booked_by := <str>$by,
}
"""

MAX_OVERLAP = """
select max((
    for r in Reservation union (
        count((
            select detached Reservation
            filter .resource = r.resource and overlaps(.period, r.period)
        ))
    )
))
"""


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def gel_server():
    proc = subprocess.run([START_SCRIPT], capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, (
        f"{START_SCRIPT} failed to start the local Gel instance: "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    return True


@pytest.fixture(scope="session")
def service(gel_server):
    if PROJECT_DIR not in sys.path:
        sys.path.insert(0, PROJECT_DIR)
    assert os.path.isfile(SERVICE_FILE), f"{SERVICE_FILE} does not exist."
    try:
        import booking_service  # type: ignore
    except Exception as exc:  # pragma: no cover - reported as a test failure
        pytest.fail(f"Failed to import booking_service from {PROJECT_DIR}: {exc!r}")
    for name in (
        "book",
        "availability",
        "utilization",
        "BookingError",
        "InvalidPeriodError",
        "UnknownResourceError",
        "OverlappingBookingError",
    ):
        assert hasattr(booking_service, name), (
            f"booking_service is missing the required attribute '{name}'."
        )
    return booking_service


def run_async(coro_factory):
    """Run a coroutine factory that receives a freshly created async client."""

    async def main():
        client = gel.create_async_client()
        try:
            return await coro_factory(client)
        finally:
            await client.aclose()

    return asyncio.run(main())


async def reset(client):
    await client.execute("delete Reservation; delete Resource;")
    for code, name, capacity in RESOURCES:
        await client.execute(
            "insert Resource { code := <str>$code, name := <str>$name, "
            "capacity := <int64>$capacity }",
            code=code,
            name=name,
            capacity=capacity,
        )


async def raw_book(client, code, start, end, by="seed"):
    payload = json.dumps(
        {"lower": start, "upper": end, "inc_lower": True, "inc_upper": False}
    )
    await client.execute(RAW_INSERT, code=code, period=payload, by=by)


async def count_reservations(client):
    return await client.query_single("select count(Reservation)")


def gel_cli(*args, timeout=180):
    return subprocess.run(
        ["gel", *args],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run_cli(args, timeout=180):
    return subprocess.run(
        [sys.executable, "booking_cli.py", *args],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def normalize_type(name):
    return (name or "").replace("std::", "")


def pointer_map(obj):
    return {p["name"]: p for p in obj["pointers"]}


# --------------------------------------------------------------------------- #
# 1. deliverables and migration state
# --------------------------------------------------------------------------- #
def test_solution_files_exist():
    assert os.path.isfile(SERVICE_FILE), f"Missing service module {SERVICE_FILE}."
    assert os.path.isfile(CLI_FILE), f"Missing CLI entrypoint {CLI_FILE}."


def test_migration_files_created():
    migrations = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql")))
    assert migrations, (
        f"No migration files found in {MIGRATIONS_DIR}; the schema migration must be "
        "created and committed to the project."
    )


def test_migration_status_is_up_to_date(gel_server):
    proc = gel_cli("migration", "status")
    combined = (proc.stdout + proc.stderr).lower()
    assert proc.returncode == 0, (
        f"'gel migration status' failed in {PROJECT_DIR}: "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert "up to date" in combined, (
        f"The database schema is not up to date with dbschema/: {proc.stdout!r} {proc.stderr!r}"
    )
    applied = gel_cli("query", "-F", "json", "select <str>count(schema::Migration)")
    assert applied.returncode == 0, (
        f"Failed to count applied migrations: stdout={applied.stdout!r} "
        f"stderr={applied.stderr!r}"
    )
    assert int(json.loads(applied.stdout)[0]) >= 1, (
        "No migration has been applied to the database; the schema migration must be "
        "created and applied."
    )


# --------------------------------------------------------------------------- #
# 2. schema introspection
# --------------------------------------------------------------------------- #
def test_schema_shape(gel_server):
    proc = gel_cli(
        "query",
        "-F",
        "json",
        "select schema::ObjectType { name, constraints: { name }, "
        "pointers: { name, required, target_name := .target.name, "
        "constraints: { name } } } "
        "filter .name in {'default::Resource', 'default::Reservation'}",
    )
    assert proc.returncode == 0, (
        f"Schema introspection query failed: stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    types = {obj["name"]: obj for obj in json.loads(proc.stdout)}
    assert "default::Resource" in types, "Object type 'default::Resource' is missing."
    assert "default::Reservation" in types, "Object type 'default::Reservation' is missing."

    resource = pointer_map(types["default::Resource"])
    for name, expected in (
        ("code", "str"),
        ("name", "str"),
        ("capacity", "int64"),
    ):
        assert name in resource, f"Resource is missing the required property '{name}'."
        assert resource[name]["required"], f"Resource.{name} must be required."
        assert normalize_type(resource[name]["target_name"]) == expected, (
            f"Resource.{name} must be of type {expected}, got "
            f"{resource[name]['target_name']!r}."
        )
    code_constraints = {c["name"] for c in resource["code"]["constraints"]}
    assert "std::exclusive" in code_constraints, (
        f"Resource.code must be unique (exclusive constraint), found {code_constraints}."
    )

    reservation = pointer_map(types["default::Reservation"])
    assert "resource" in reservation, "Reservation is missing the 'resource' link."
    assert reservation["resource"]["required"], "Reservation.resource must be required."
    assert normalize_type(reservation["resource"]["target_name"]) == "default::Resource", (
        "Reservation.resource must link to default::Resource, got "
        f"{reservation['resource']['target_name']!r}."
    )
    assert "period" in reservation, "Reservation is missing the 'period' property."
    assert reservation["period"]["required"], "Reservation.period must be required."
    assert normalize_type(reservation["period"]["target_name"]) == "range<cal::local_datetime>", (
        "Reservation.period must be a range<cal::local_datetime>, got "
        f"{reservation['period']['target_name']!r}."
    )
    assert "booked_by" in reservation, "Reservation is missing the 'booked_by' property."
    assert reservation["booked_by"]["required"], "Reservation.booked_by must be required."
    for name, expected in (
        ("starts_at", "cal::local_datetime"),
        ("ends_at", "cal::local_datetime"),
        ("duration_minutes", "int64"),
    ):
        assert name in reservation, f"Reservation is missing the computed property '{name}'."
        assert normalize_type(reservation[name]["target_name"]) == expected, (
            f"Reservation.{name} must be of type {expected}, got "
            f"{reservation[name]['target_name']!r}."
        )

    period_constraints = {c["name"] for c in reservation["period"]["constraints"]}
    type_constraints = {c["name"] for c in types["default::Reservation"]["constraints"]}
    assert period_constraints or type_constraints, (
        "Reservation must carry at least one schema-level constraint enforcing "
        "well-formed booking periods (found none on 'period' nor on the type)."
    )


# --------------------------------------------------------------------------- #
# 3. the database itself refuses malformed periods
# --------------------------------------------------------------------------- #
def test_database_rejects_malformed_periods(service):
    cases = {
        "empty period": {
            "lower": "2026-03-02T09:00:00",
            "upper": "2026-03-02T09:00:00",
            "inc_lower": True,
            "inc_upper": False,
        },
        "inverted period": {
            "lower": "2026-03-02T10:00:00",
            "upper": "2026-03-02T09:00:00",
            "inc_lower": True,
            "inc_upper": False,
        },
        "missing upper bound": {
            "lower": "2026-03-02T09:00:00",
            "inc_lower": True,
            "inc_upper": False,
        },
        "missing lower bound": {
            "upper": "2026-03-02T10:00:00",
            "inc_lower": False,
            "inc_upper": False,
        },
        "exclusive lower bound": {
            "lower": "2026-03-02T09:00:00",
            "upper": "2026-03-02T10:00:00",
            "inc_lower": False,
            "inc_upper": False,
        },
        "inclusive upper bound": {
            "lower": "2026-03-02T09:00:00",
            "upper": "2026-03-02T10:00:00",
            "inc_lower": True,
            "inc_upper": True,
        },
    }

    async def scenario(client):
        await reset(client)
        outcome = {}
        for label, payload in cases.items():
            try:
                await client.execute(
                    RAW_INSERT, code="alpha", period=json.dumps(payload), by="raw"
                )
                outcome[label] = None
            except (gel.ConstraintViolationError, gel.InvalidValueError) as exc:
                outcome[label] = type(exc).__name__
        stored = await count_reservations(client)
        await raw_book(client, "alpha", "2026-03-02T09:00:00", "2026-03-02T10:00:00")
        return outcome, stored, await count_reservations(client)

    outcome, stored_after_bad, stored_after_good = run_async(scenario)
    for label, err in outcome.items():
        assert err is not None, (
            f"The database accepted a Reservation with an {label}; the schema must "
            "reject it (raw EdgeQL insert succeeded)."
        )
    assert stored_after_bad == 0, (
        f"Refused inserts must not leave rows behind, found {stored_after_bad}."
    )
    assert stored_after_good == 1, (
        "A well-formed period [2026-03-02T09:00:00, 2026-03-02T10:00:00) must be "
        f"insertable, reservation count is {stored_after_good}."
    )


# --------------------------------------------------------------------------- #
# 4. booking happy path
# --------------------------------------------------------------------------- #
def test_book_returns_contract_and_stores_half_open_range(service):
    async def scenario(client):
        await reset(client)
        result = await service.book(
            client, "alpha", "2026-03-02T09:00:00", "2026-03-02T10:30:00", "ada"
        )
        stored = await client.query_single(
            """
            select assert_single((
                select Reservation {
                    starts_at,
                    ends_at,
                    duration_minutes,
                    code := .resource.code,
                    inc_lower := range_is_inclusive_lower(.period),
                    inc_upper := range_is_inclusive_upper(.period),
                    empty := range_is_empty(.period),
                }
            ))
            """
        )
        return result, stored

    result, stored = run_async(scenario)
    assert isinstance(result, dict), f"book() must return a dict, got {type(result)!r}."
    assert set(result) == BOOK_KEYS, (
        f"book() must return exactly the keys {sorted(BOOK_KEYS)}, got {sorted(result)}."
    )
    uuid.UUID(str(result["id"]))
    assert result["resource"] == "alpha", f"Unexpected resource: {result['resource']!r}."
    assert result["start"] == "2026-03-02T09:00:00", f"Unexpected start: {result['start']!r}."
    assert result["end"] == "2026-03-02T10:30:00", f"Unexpected end: {result['end']!r}."
    assert result["booked_by"] == "ada", f"Unexpected booked_by: {result['booked_by']!r}."
    assert result["duration_minutes"] == 90, (
        f"09:00-10:30 lasts 90 minutes, got {result['duration_minutes']!r}."
    )
    assert stored.code == "alpha", f"Reservation linked to the wrong resource: {stored.code!r}."
    assert stored.starts_at.isoformat() == "2026-03-02T09:00:00", (
        f"Stored starts_at is {stored.starts_at!r}."
    )
    assert stored.ends_at.isoformat() == "2026-03-02T10:30:00", (
        f"Stored ends_at is {stored.ends_at!r}."
    )
    assert stored.duration_minutes == 90, (
        f"Stored duration_minutes is {stored.duration_minutes!r}, expected 90."
    )
    assert stored.inc_lower is True, "The stored period must include its lower bound."
    assert stored.inc_upper is False, "The stored period must exclude its upper bound."
    assert stored.empty is False, "The stored period must not be empty."


# --------------------------------------------------------------------------- #
# 5. half-open boundary semantics
# --------------------------------------------------------------------------- #
def test_adjacent_bookings_allowed_overlapping_rejected(service):
    async def scenario(client):
        await reset(client)
        await service.book(
            client, "alpha", "2026-03-02T09:00:00", "2026-03-02T10:30:00", "ada"
        )
        accepted = []
        rejected = {}
        for label, code, start, end in (
            ("touch-after", "alpha", "2026-03-02T10:30:00", "2026-03-02T11:00:00"),
            ("touch-before", "alpha", "2026-03-02T08:00:00", "2026-03-02T09:00:00"),
            ("straddle", "alpha", "2026-03-02T10:29:00", "2026-03-02T10:31:00"),
            ("contained", "alpha", "2026-03-02T09:15:00", "2026-03-02T09:45:00"),
            ("containing", "alpha", "2026-03-02T08:30:00", "2026-03-02T12:00:00"),
            ("other-resource", "beta", "2026-03-02T09:15:00", "2026-03-02T09:45:00"),
        ):
            try:
                await service.book(client, code, start, end, "bob")
                accepted.append(label)
            except service.OverlappingBookingError:
                rejected[label] = "OverlappingBookingError"
            except Exception as exc:  # noqa: BLE001 - surfaced as an assertion below
                rejected[label] = f"{type(exc).__name__}: {exc}"
        return accepted, rejected, await count_reservations(client)

    accepted, rejected, stored = run_async(scenario)
    for label in ("touch-after", "touch-before", "other-resource"):
        assert label in accepted, (
            f"The '{label}' booking must be accepted (half-open periods only clash when "
            f"they share an instant); it failed with {rejected.get(label)!r}."
        )
    for label in ("straddle", "contained", "containing"):
        assert rejected.get(label) == "OverlappingBookingError", (
            f"The '{label}' booking must raise OverlappingBookingError, got "
            f"{rejected.get(label)!r}."
        )
    assert stored == 4, (
        f"Exactly 4 reservations should be stored (1 seed + 3 accepted), found {stored}."
    )


# --------------------------------------------------------------------------- #
# 6. validation order and exception hierarchy
# --------------------------------------------------------------------------- #
def test_exception_hierarchy(service):
    for name in ("InvalidPeriodError", "UnknownResourceError", "OverlappingBookingError"):
        exc_type = getattr(service, name)
        assert issubclass(exc_type, service.BookingError), (
            f"{name} must subclass BookingError."
        )
    assert issubclass(service.BookingError, Exception), (
        "BookingError must subclass Exception."
    )


def test_validation_order_and_error_types(service):
    async def scenario(client):
        await reset(client)
        results = {}
        cases = (
            ("equal-bounds", "alpha", "2026-03-02T09:00:00", "2026-03-02T09:00:00"),
            ("inverted", "alpha", "2026-03-02T10:00:00", "2026-03-02T09:00:00"),
            ("bad-format", "alpha", "2026-03-02 09:00:00", "2026-03-02T10:00:00"),
            ("unknown-resource", "nope", "2026-03-02T09:00:00", "2026-03-02T10:00:00"),
            ("unknown-and-inverted", "nope", "2026-03-02T10:00:00", "2026-03-02T09:00:00"),
        )
        for label, code, start, end in cases:
            try:
                await service.book(client, code, start, end, "ada")
                results[label] = None
            except Exception as exc:  # noqa: BLE001 - type asserted below
                results[label] = type(exc).__name__
        return results, await count_reservations(client)

    results, stored = run_async(scenario)
    for label in ("equal-bounds", "inverted", "bad-format", "unknown-and-inverted"):
        assert results[label] == "InvalidPeriodError", (
            f"Case '{label}' must raise InvalidPeriodError, got {results[label]!r}."
        )
    assert results["unknown-resource"] == "UnknownResourceError", (
        f"Booking an unknown resource code must raise UnknownResourceError, got "
        f"{results['unknown-resource']!r}."
    )
    assert stored == 0, f"Failed bookings must not store anything, found {stored}."


# --------------------------------------------------------------------------- #
# 7. overlap detection reads committed state written by other tooling
# --------------------------------------------------------------------------- #
def test_overlap_detection_uses_committed_state(service):
    async def seed(client):
        await reset(client)
        await raw_book(client, "gamma", "2026-03-05T13:00:00", "2026-03-05T14:00:00", "tool")
        return await count_reservations(client)

    assert run_async(seed) == 1, "Failed to seed the externally created reservation."

    clash = run_cli(
        [
            "book",
            "--resource",
            "gamma",
            "--start",
            "2026-03-05T13:30:00",
            "--end",
            "2026-03-05T13:45:00",
            "--by",
            "eve",
        ]
    )
    assert clash.returncode == 4, (
        "A fresh process must reject a period conflicting with a reservation written by "
        f"other tooling (expected exit 4, got {clash.returncode}; stdout={clash.stdout!r} "
        f"stderr={clash.stderr!r})."
    )
    assert json.loads(clash.stdout.strip())["error"] == "OverlappingBookingError", (
        f"Unexpected CLI error payload: {clash.stdout!r}"
    )

    okay = run_cli(
        [
            "book",
            "--resource",
            "gamma",
            "--start",
            "2026-03-05T14:00:00",
            "--end",
            "2026-03-05T15:00:00",
            "--by",
            "eve",
        ]
    )
    assert okay.returncode == 0, (
        "A booking that starts exactly when the previous one ends must succeed "
        f"(stdout={okay.stdout!r} stderr={okay.stderr!r})."
    )
    assert run_async(count_reservations) == 2, (
        "Expected exactly two reservations after the conflicting and the adjacent booking."
    )


# --------------------------------------------------------------------------- #
# 8./9. availability multirange arithmetic
# --------------------------------------------------------------------------- #
async def _seed_availability(client, service):
    await reset(client)
    for start, end in (
        ("2026-03-02T09:00:00", "2026-03-02T10:00:00"),
        ("2026-03-02T10:00:00", "2026-03-02T11:00:00"),
        ("2026-03-02T13:00:00", "2026-03-02T14:30:00"),
    ):
        await service.book(client, "alpha", start, end, "ada")


def test_availability_merges_and_reports_free_gaps(service):
    async def scenario(client):
        await _seed_availability(client, service)
        return await service.availability(
            client, "alpha", "2026-03-02T08:00:00", "2026-03-02T16:00:00"
        )

    result = run_async(scenario)
    assert set(result) == AVAIL_KEYS, (
        f"availability() must return exactly the keys {sorted(AVAIL_KEYS)}, got "
        f"{sorted(result)}."
    )
    assert result["resource"] == "alpha", f"Unexpected resource: {result['resource']!r}."
    assert result["window_start"] == "2026-03-02T08:00:00", (
        f"Unexpected window_start: {result['window_start']!r}."
    )
    assert result["window_end"] == "2026-03-02T16:00:00", (
        f"Unexpected window_end: {result['window_end']!r}."
    )
    assert result["booked"] == [
        {"start": "2026-03-02T09:00:00", "end": "2026-03-02T11:00:00"},
        {"start": "2026-03-02T13:00:00", "end": "2026-03-02T14:30:00"},
    ], f"Adjacent reservations must be merged into one interval, got {result['booked']!r}."
    assert result["free"] == [
        {"start": "2026-03-02T08:00:00", "end": "2026-03-02T09:00:00"},
        {"start": "2026-03-02T11:00:00", "end": "2026-03-02T13:00:00"},
        {"start": "2026-03-02T14:30:00", "end": "2026-03-02T16:00:00"},
    ], f"Unexpected free intervals: {result['free']!r}."
    assert result["booked_minutes"] == 210, (
        f"Expected 210 booked minutes, got {result['booked_minutes']!r}."
    )
    assert result["free_minutes"] == 270, (
        f"Expected 270 free minutes, got {result['free_minutes']!r}."
    )


def test_availability_clips_to_window_edges(service):
    async def scenario(client):
        await _seed_availability(client, service)
        partial = await service.availability(
            client, "alpha", "2026-03-02T09:30:00", "2026-03-02T13:30:00"
        )
        inside = await service.availability(
            client, "alpha", "2026-03-02T09:10:00", "2026-03-02T09:20:00"
        )
        untouched = await service.availability(
            client, "gamma", "2026-03-02T08:00:00", "2026-03-02T16:00:00"
        )
        return partial, inside, untouched

    partial, inside, untouched = run_async(scenario)
    assert partial["booked"] == [
        {"start": "2026-03-02T09:30:00", "end": "2026-03-02T11:00:00"},
        {"start": "2026-03-02T13:00:00", "end": "2026-03-02T13:30:00"},
    ], f"Booked intervals must be clipped to the window, got {partial['booked']!r}."
    assert partial["free"] == [
        {"start": "2026-03-02T11:00:00", "end": "2026-03-02T13:00:00"}
    ], f"Unexpected free intervals for the clipped window: {partial['free']!r}."
    assert (partial["booked_minutes"], partial["free_minutes"]) == (120, 120), (
        f"Expected 120/120 booked/free minutes, got "
        f"{partial['booked_minutes']}/{partial['free_minutes']}."
    )

    assert inside["booked"] == [
        {"start": "2026-03-02T09:10:00", "end": "2026-03-02T09:20:00"}
    ], f"A window inside one reservation must be fully booked, got {inside['booked']!r}."
    assert inside["free"] == [], f"Expected no free interval, got {inside['free']!r}."
    assert (inside["booked_minutes"], inside["free_minutes"]) == (10, 0), (
        f"Expected 10/0 booked/free minutes, got "
        f"{inside['booked_minutes']}/{inside['free_minutes']}."
    )

    assert untouched["booked"] == [], (
        f"An unbooked resource must report no booked intervals, got {untouched['booked']!r}."
    )
    assert untouched["free"] == [
        {"start": "2026-03-02T08:00:00", "end": "2026-03-02T16:00:00"}
    ], f"An unbooked resource must report the whole window as free, got {untouched['free']!r}."
    assert untouched["booked_minutes"] == 0, (
        f"Expected 0 booked minutes, got {untouched['booked_minutes']!r}."
    )
    assert untouched["free_minutes"] == 480, (
        f"Expected 480 free minutes, got {untouched['free_minutes']!r}."
    )


def test_availability_rejects_bad_window_and_unknown_resource(service):
    async def scenario(client):
        await reset(client)
        errors = {}
        try:
            await service.availability(
                client, "alpha", "2026-03-02T12:00:00", "2026-03-02T12:00:00"
            )
            errors["window"] = None
        except Exception as exc:  # noqa: BLE001 - type asserted below
            errors["window"] = type(exc).__name__
        try:
            await service.availability(
                client, "nope", "2026-03-02T08:00:00", "2026-03-02T16:00:00"
            )
            errors["resource"] = None
        except Exception as exc:  # noqa: BLE001 - type asserted below
            errors["resource"] = type(exc).__name__
        return errors

    errors = run_async(scenario)
    assert errors["window"] == "InvalidPeriodError", (
        f"A window whose end is not after its start must raise InvalidPeriodError, got "
        f"{errors['window']!r}."
    )
    assert errors["resource"] == "UnknownResourceError", (
        f"An unknown resource code must raise UnknownResourceError, got "
        f"{errors['resource']!r}."
    )


# --------------------------------------------------------------------------- #
# 10.-12. concurrency invariants
# --------------------------------------------------------------------------- #
def test_concurrent_identical_bookings_yield_single_reservation(service):
    async def scenario(client):
        await reset(client)
        results = await asyncio.gather(
            *[
                service.book(
                    client,
                    "alpha",
                    "2026-03-09T09:00:00",
                    "2026-03-09T10:00:00",
                    f"user{i}",
                )
                for i in range(12)
            ],
            return_exceptions=True,
        )
        return results, await count_reservations(client)

    started = time.monotonic()
    results, stored = run_async(scenario)
    elapsed = time.monotonic() - started
    assert elapsed < 120, f"12 concurrent bookings took too long ({elapsed:.1f}s)."

    successes = [r for r in results if isinstance(r, dict)]
    failures = [r for r in results if not isinstance(r, dict)]
    assert len(successes) == 1, (
        f"Exactly one of 12 identical concurrent bookings may succeed, got "
        f"{len(successes)} successes and failures {[repr(f) for f in failures]}."
    )
    for failure in failures:
        assert type(failure).__name__ == "OverlappingBookingError", (
            f"Losing concurrent bookings must raise OverlappingBookingError, got {failure!r}."
        )
    assert stored == 1, f"Exactly one reservation must be stored, found {stored}."


def test_concurrent_overlapping_bookings_never_double_book(service):
    slots = (
        ("2026-03-09T09:00:00", "2026-03-09T10:00:00"),
        ("2026-03-09T09:30:00", "2026-03-09T10:30:00"),
        ("2026-03-09T09:45:00", "2026-03-09T11:00:00"),
        ("2026-03-09T09:50:00", "2026-03-09T10:10:00"),
        ("2026-03-09T09:55:00", "2026-03-09T10:05:00"),
        ("2026-03-09T09:59:00", "2026-03-09T10:59:00"),
    )

    async def scenario(client):
        await reset(client)
        results = await asyncio.gather(
            *[service.book(client, "alpha", s, e, "user") for s, e in slots],
            return_exceptions=True,
        )
        stored = await count_reservations(client)
        worst = await client.query_single(MAX_OVERLAP)
        return results, stored, worst

    results, stored, worst = run_async(scenario)
    successes = [r for r in results if isinstance(r, dict)]
    failures = [r for r in results if not isinstance(r, dict)]
    assert successes, (
        f"At least one of the concurrent overlapping bookings must succeed, failures: "
        f"{[repr(f) for f in failures]}."
    )
    for failure in failures:
        assert type(failure).__name__ == "OverlappingBookingError", (
            f"Rejected concurrent bookings must raise OverlappingBookingError, got {failure!r}."
        )
    assert stored == len(successes), (
        f"{len(successes)} bookings reported success but {stored} reservations are stored."
    )
    assert worst == 1, (
        "No two stored reservations of the same resource may overlap; the database "
        f"reports a maximum overlap group size of {worst}."
    )


def test_concurrent_disjoint_bookings_all_succeed(service):
    slots = (
        ("2026-03-09T09:00:00", "2026-03-09T10:00:00"),
        ("2026-03-09T10:00:00", "2026-03-09T11:00:00"),
        ("2026-03-09T11:00:00", "2026-03-09T12:00:00"),
        ("2026-03-09T12:00:00", "2026-03-09T13:00:00"),
    )

    async def scenario(client):
        await reset(client)
        results = await asyncio.gather(
            *[service.book(client, "alpha", s, e, "user") for s, e in slots],
            return_exceptions=True,
        )
        return results, await count_reservations(client)

    results, stored = run_async(scenario)
    failures = [r for r in results if not isinstance(r, dict)]
    assert not failures, (
        f"All 4 concurrent non-overlapping bookings must succeed, got failures: "
        f"{[repr(f) for f in failures]}."
    )
    assert stored == 4, f"Expected 4 stored reservations, found {stored}."


# --------------------------------------------------------------------------- #
# 13. utilization aggregates
# --------------------------------------------------------------------------- #
def test_utilization_aggregates(service):
    async def scenario(client):
        await reset(client)
        await service.book(client, "alpha", "2026-03-02T09:00:00", "2026-03-02T10:00:00", "ada")
        await service.book(client, "alpha", "2026-03-02T11:30:00", "2026-03-02T12:00:00", "ada")
        await service.book(client, "beta", "2026-03-02T08:00:00", "2026-03-02T09:00:00", "bob")
        narrow = await service.utilization(
            client, "2026-03-02T09:00:00", "2026-03-02T13:00:00"
        )
        wide = await service.utilization(
            client, "2026-03-02T08:30:00", "2026-03-02T13:00:00"
        )
        bad = None
        try:
            await service.utilization(client, "2026-03-02T13:00:00", "2026-03-02T09:00:00")
        except Exception as exc:  # noqa: BLE001 - type asserted below
            bad = type(exc).__name__
        return narrow, wide, bad

    narrow, wide, bad = run_async(scenario)
    assert isinstance(narrow, list), f"utilization() must return a list, got {type(narrow)!r}."
    assert [row["code"] for row in narrow] == ["alpha", "beta", "gamma"], (
        f"utilization() must list every resource ordered by code, got {narrow!r}."
    )
    for row in narrow:
        assert set(row) == UTIL_KEYS, (
            f"Each utilization entry must have exactly the keys {sorted(UTIL_KEYS)}, got "
            f"{sorted(row)}."
        )
    by_code = {row["code"]: row for row in narrow}
    assert by_code["alpha"]["reservation_count"] == 2, (
        f"alpha has 2 reservations inside the window, got "
        f"{by_code['alpha']['reservation_count']!r}."
    )
    assert by_code["alpha"]["booked_minutes"] == 90, (
        f"alpha has 90 booked minutes in the window, got "
        f"{by_code['alpha']['booked_minutes']!r}."
    )
    assert by_code["alpha"]["utilization_pct"] == pytest.approx(37.5, abs=0.011), (
        f"alpha utilization must be 90/240 = 37.5%, got "
        f"{by_code['alpha']['utilization_pct']!r}."
    )
    assert by_code["beta"]["reservation_count"] == 0, (
        "A reservation ending exactly at the window start does not touch the window, got "
        f"{by_code['beta']['reservation_count']!r}."
    )
    assert by_code["beta"]["booked_minutes"] == 0, (
        f"beta must report 0 booked minutes, got {by_code['beta']['booked_minutes']!r}."
    )
    assert by_code["beta"]["utilization_pct"] == pytest.approx(0.0, abs=0.011), (
        f"beta utilization must be 0.0, got {by_code['beta']['utilization_pct']!r}."
    )
    assert by_code["gamma"]["reservation_count"] == 0, (
        f"gamma has no reservations, got {by_code['gamma']['reservation_count']!r}."
    )
    assert by_code["gamma"]["booked_minutes"] == 0, (
        f"gamma must report 0 booked minutes, got {by_code['gamma']['booked_minutes']!r}."
    )
    assert by_code["gamma"]["utilization_pct"] == pytest.approx(0.0, abs=0.011), (
        f"gamma utilization must be 0.0, got {by_code['gamma']['utilization_pct']!r}."
    )

    wide_by_code = {row["code"]: row for row in wide}
    assert wide_by_code["beta"]["reservation_count"] == 1, (
        "Widening the window to 08:30 must include beta's reservation, got "
        f"{wide_by_code['beta']['reservation_count']!r}."
    )
    assert wide_by_code["beta"]["booked_minutes"] == 30, (
        "Only the part of beta's reservation inside the window counts (30 minutes), got "
        f"{wide_by_code['beta']['booked_minutes']!r}."
    )
    assert wide_by_code["beta"]["utilization_pct"] == pytest.approx(11.11, abs=0.011), (
        f"beta utilization must be 30/270 = 11.11%, got "
        f"{wide_by_code['beta']['utilization_pct']!r}."
    )
    assert wide_by_code["alpha"]["booked_minutes"] == 90, (
        f"alpha still has 90 booked minutes, got {wide_by_code['alpha']['booked_minutes']!r}."
    )
    assert bad == "InvalidPeriodError", (
        f"A window whose end precedes its start must raise InvalidPeriodError, got {bad!r}."
    )


# --------------------------------------------------------------------------- #
# 14./15. CLI contract
# --------------------------------------------------------------------------- #
def test_cli_success_paths(service):
    run_async(reset)

    booked = run_cli(
        [
            "book",
            "--resource",
            "alpha",
            "--start",
            "2026-03-02T09:00:00",
            "--end",
            "2026-03-02T10:00:00",
            "--by",
            "ada",
        ]
    )
    assert booked.returncode == 0, (
        f"'book' must exit 0, got {booked.returncode} (stdout={booked.stdout!r} "
        f"stderr={booked.stderr!r})."
    )
    payload = json.loads(booked.stdout.strip())
    assert set(payload) == BOOK_KEYS, (
        f"CLI 'book' must print exactly the keys {sorted(BOOK_KEYS)}, got {sorted(payload)}."
    )
    assert payload["duration_minutes"] == 60, (
        f"09:00-10:00 lasts 60 minutes, got {payload['duration_minutes']!r}."
    )

    avail = run_cli(
        [
            "availability",
            "--resource",
            "alpha",
            "--window-start",
            "2026-03-02T08:00:00",
            "--window-end",
            "2026-03-02T12:00:00",
        ]
    )
    assert avail.returncode == 0, (
        f"'availability' must exit 0, got {avail.returncode} (stdout={avail.stdout!r} "
        f"stderr={avail.stderr!r})."
    )
    avail_payload = json.loads(avail.stdout.strip())
    assert avail_payload["booked_minutes"] == 60, (
        f"Expected 60 booked minutes, got {avail_payload['booked_minutes']!r}."
    )
    assert avail_payload["free"] == [
        {"start": "2026-03-02T08:00:00", "end": "2026-03-02T09:00:00"},
        {"start": "2026-03-02T10:00:00", "end": "2026-03-02T12:00:00"},
    ], f"Unexpected free intervals from the CLI: {avail_payload['free']!r}."

    util = run_cli(
        [
            "utilization",
            "--window-start",
            "2026-03-02T08:00:00",
            "--window-end",
            "2026-03-02T12:00:00",
        ]
    )
    assert util.returncode == 0, (
        f"'utilization' must exit 0, got {util.returncode} (stdout={util.stdout!r} "
        f"stderr={util.stderr!r})."
    )
    util_payload = json.loads(util.stdout.strip())
    assert isinstance(util_payload, list), (
        f"CLI 'utilization' must print a JSON array, got {type(util_payload)!r}."
    )
    assert [row["code"] for row in util_payload] == ["alpha", "beta", "gamma"], (
        f"Unexpected resource ordering from the CLI: {util_payload!r}."
    )
    assert util_payload[0]["booked_minutes"] == 60, (
        f"alpha must report 60 booked minutes, got {util_payload[0]['booked_minutes']!r}."
    )


def test_cli_error_paths_and_exit_codes(service):
    run_async(reset)
    first = run_cli(
        [
            "book",
            "--resource",
            "alpha",
            "--start",
            "2026-03-02T09:00:00",
            "--end",
            "2026-03-02T10:00:00",
            "--by",
            "ada",
        ]
    )
    assert first.returncode == 0, (
        f"The first booking must succeed (stdout={first.stdout!r} stderr={first.stderr!r})."
    )

    cases = (
        (
            4,
            "OverlappingBookingError",
            [
                "book",
                "--resource",
                "alpha",
                "--start",
                "2026-03-02T09:00:00",
                "--end",
                "2026-03-02T10:00:00",
                "--by",
                "ada",
            ],
        ),
        (
            2,
            "InvalidPeriodError",
            [
                "book",
                "--resource",
                "alpha",
                "--start",
                "2026-03-02T10:00:00",
                "--end",
                "2026-03-02T09:00:00",
                "--by",
                "ada",
            ],
        ),
        (
            3,
            "UnknownResourceError",
            [
                "book",
                "--resource",
                "nope",
                "--start",
                "2026-03-02T14:00:00",
                "--end",
                "2026-03-02T15:00:00",
                "--by",
                "ada",
            ],
        ),
    )
    for expected_code, expected_error, args in cases:
        proc = run_cli(args)
        assert proc.returncode == expected_code, (
            f"{args} must exit {expected_code} ({expected_error}), got {proc.returncode} "
            f"(stdout={proc.stdout!r} stderr={proc.stderr!r})."
        )
        payload = json.loads(proc.stdout.strip())
        assert set(payload) == {"error", "message"}, (
            f"CLI error payloads must have exactly the keys ['error', 'message'], got "
            f"{sorted(payload)}."
        )
        assert payload["error"] == expected_error, (
            f"Expected error {expected_error!r}, got {payload['error']!r}."
        )
        assert isinstance(payload["message"], str) and payload["message"].strip(), (
            f"CLI error payloads must carry a non-empty message, got {payload!r}."
        )

    assert run_async(count_reservations) == 1, (
        "Failed CLI bookings must not create reservations."
    )
