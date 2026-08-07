# Overlap-Safe Resource Reservations with Gel Temporal Ranges

## Background

`/home/user/booking` is a half-finished Gel 6 project for a shared-resource reservation system (meeting rooms, lab benches, ...). The schema module is still empty and there is no application code: only `gel.toml`, an empty `dbschema/default.gel`, and a `README.md` sketch of the intended data model.

A local Gel 6 server is already installed in the image. Start (or restart) it with `/usr/local/bin/gel-start.sh`; the script is idempotent and returns only once the instance accepts queries. Connection settings for both the `gel` CLI and the Python client are already provided by the environment, so never hardcode host/port/credentials anywhere in your code. Python 3 with the official `gel` client is preinstalled; the finished solution must not need network access.

Reservation periods are *temporal intervals*, and the whole point of the exercise is that the database — not a Python cache — is the authority on which intervals are taken.

## Requirements

### 1. Schema (`dbschema/default.gel`) plus a created and applied migration

Declare exactly these types in module `default`:

- `Resource` with required properties `code: str` (unique across all resources), `name: str`, and `capacity: int64`. These three properties must be the only required pointers, so external tooling can insert a resource with just those values.
- `Reservation` with:
  - a required single link `resource` to `Resource`,
  - a required property `period` whose type is `range<cal::local_datetime>`,
  - a required property `booked_by: str`,
  - a computed property `starts_at: cal::local_datetime` — the start of `period`,
  - a computed property `ends_at: cal::local_datetime` — the end of `period`,
  - a computed property `duration_minutes: int64` — the whole number of minutes covered by `period`.

  `resource`, `period` and `booked_by` must be the only required non-computed pointers.

Every stored `Reservation.period` must be a well-formed booking interval, and this must be enforced **inside the schema** so that a raw EdgeQL `insert` that violates it fails with a constraint violation (not merely rejected by Python code). A well-formed booking interval is: non-empty, bounded below *and* above, with the lower boundary inclusive and the upper boundary exclusive. In other words a period is the half-open interval `[start, end)`: two reservations that merely touch (`... , T)` followed by `[T, ...`) do **not** conflict, while sharing any instant does.

The migration(s) for this schema must be created and applied: in `/home/user/booking`, `gel migration status` must report that the database schema is up to date.

Do not seed, create, modify or delete `Resource` objects from your application code — resource records are provisioned by other tooling.

### 2. Booking service `/home/user/booking/booking_service.py`

Implement exactly these exception classes:

- `BookingError(Exception)`
- `InvalidPeriodError(BookingError)`
- `UnknownResourceError(BookingError)`
- `OverlappingBookingError(BookingError)`

and exactly these module-level coroutine functions, whose first argument is an existing `gel` async client (they must not create their own connection):

```python
async def book(client, resource_code, start, end, booked_by) -> dict
async def availability(client, resource_code, window_start, window_end) -> dict
async def utilization(client, window_start, window_end) -> list
```

All timestamps crossing the API boundary — arguments and returned values — are naive local-time strings in exactly the format `YYYY-MM-DDTHH:MM:SS` (no timezone, no fractional seconds). All test data is aligned to whole minutes.

`book` stores one reservation and returns a dict with exactly the keys `id` (the new object's UUID as a string), `resource` (the resource code), `start`, `end`, `booked_by` and `duration_minutes` (int).

Validation is applied strictly in this order, so a call that is wrong in several ways reports the first applicable failure:

1. `InvalidPeriodError` — a timestamp is not in the required format, or `end` is not strictly after `start`.
2. `UnknownResourceError` — no `Resource` has that `code`.
3. `OverlappingBookingError` — the requested interval shares at least one instant with an already-committed reservation of the same resource. Reservations of *different* resources never conflict.

When `book` raises, it must leave no reservation behind.

`availability` reports what is taken and what is free for one resource inside the requested window, and returns a dict with exactly the keys:

- `resource` — the resource code,
- `window_start`, `window_end` — the requested window as timestamp strings,
- `booked` — a JSON array of intervals,
- `free` — a JSON array of intervals,
- `booked_minutes` — int,
- `free_minutes` — int.

Each interval in `booked` and `free` is a dict with exactly the keys `start` and `end` (timestamp strings, half-open `[start, end)`). Both arrays must be clipped to the window, ordered ascending by `start`, and fully normalized: intervals that overlap or merely touch are merged into a single interval, and no returned interval is empty. `booked` covers the portion of the window taken by reservations of that resource; `free` is exactly the window minus `booked`, so the two arrays never overlap, and `booked_minutes + free_minutes` always equals the total number of minutes in the window. An entirely free resource yields an empty `booked` array and a single-element `free` array; a fully booked window yields an empty `free` array. `availability` raises `InvalidPeriodError` for a window whose end is not strictly after its start or whose timestamps are malformed, and `UnknownResourceError` for an unknown code.

`utilization` returns a JSON array with one entry per existing `Resource` — including resources with no reservations — ordered ascending by `code`. Each entry is a dict with exactly the keys:

- `code` — the resource code,
- `reservation_count` — int, the number of that resource's reservations sharing at least one instant with the window,
- `booked_minutes` — int, minutes of the window covered by those reservations, counting only the part that falls inside the window,
- `utilization_pct` — float, `booked_minutes` as a percentage of the window's total minutes, rounded to two decimals.

`utilization` raises `InvalidPeriodError` for a malformed or non-positive window.

### 3. Concurrency invariants

The service is used from a single asyncio process that fires up to 12 booking coroutines at once against the same client, and it must stay correct without any in-process locking or caching of taken intervals:

- When several concurrent `book` calls target the same resource with identical or otherwise overlapping periods, exactly one of them succeeds and the database ends up with exactly one new reservation; every loser raises `OverlappingBookingError`. No two stored reservations of a resource may ever share an instant.
- Concurrent `book` calls for non-overlapping periods of the same resource all succeed.
- Callers never see a database transaction-conflict / serialization failure: the only exceptions ever escaping `book` are the three declared ones, however often the database internally refuses a concurrent attempt.
- Reservations written by any other process or tool are honoured immediately — a freshly started Python process must reject a period that conflicts with them.

### 4. CLI `/home/user/booking/booking_cli.py`

Three subcommands, invoked from `/home/user/booking`:

```
python3 booking_cli.py book --resource CODE --start TS --end TS --by NAME
python3 booking_cli.py availability --resource CODE --window-start TS --window-end TS
python3 booking_cli.py utilization --window-start TS --window-end TS
```

On success the command prints the corresponding return value as a single line of JSON on stdout and exits `0`.

On failure it prints a single line of JSON on stdout with exactly the keys `error` (the exception class name, e.g. `InvalidPeriodError`) and `message` (a non-empty explanation), and exits with `2` for `InvalidPeriodError`, `3` for `UnknownResourceError`, and `4` for `OverlappingBookingError`.

## Implementation Hints

- Project path: `/home/user/booking`. Keep `gel.toml` and `dbschema/` where they are; `dbschema/migrations/` must contain the applied migration(s).
- Files that must exist: `/home/user/booking/booking_service.py` and `/home/user/booking/booking_cli.py`.
- The verifier imports `booking_service` with `/home/user/booking` on `sys.path`, creates its own async client, provisions `Resource` rows directly with EdgeQL, and calls the three coroutines with positional arguments in the documented order.
- The verifier also runs raw EdgeQL inserts against `Reservation` to check that malformed periods are refused by the database itself.
- `/usr/local/bin/gel-start.sh` may already have been run; running it again must remain harmless.
- Do not add third-party Python dependencies; only the preinstalled `gel` client and the standard library are available.

