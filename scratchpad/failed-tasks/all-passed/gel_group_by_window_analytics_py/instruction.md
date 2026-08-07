# Gel: Multi-Key `group` Analytics Rollups Behind an Async Python Client

## Background

`/home/user/analytics` is an existing **Gel 7** project for a small coffee-equipment retailer. A local Gel 7 server lives inside this container, the schema in `dbschema/default.gel` has already been migrated once, and the `main` branch of that instance is already populated with `Category` and `Sale` objects.

The existing schema (do not remove or rename anything in it) is:

```
module default {
  type Category {
    required name: str { constraint exclusive; }
    required region: str;
  }

  type Sale {
    required order_ref: str { constraint exclusive; }
    required occurred_at: datetime;
    required amount_cents: int64;
    required units: int64;
    required channel: str;
    required category: Category;
  }
}
```

`channel` is always one of `web`, `retail`, `partner`. All `datetime` values are UTC.

The finance team now needs refunds to be tracked, and needs a repeatable analytics report that slices revenue by month **and** sales channel, plus channel and category rollups with ranking and percentile figures. The report must be produced by the database using EdgeQL's top-level `group` statement — not by pulling rows into Python and aggregating there.

## Requirements

### 1. Schema and migration

Extend `dbschema/default.gel` so the `default` module also provides:

- An object type `default::Refund` with:
  - `external_id: str` — required, and no two `Refund` objects may share one.
  - `sale: Sale` — required single link to the refunded sale.
  - `amount_cents: int64` — required; the database itself must reject any value below `1`.
  - `refunded_at: datetime` — required.
- Two new read-only computed properties on `default::Sale`, both single-valued:
  - `refund_count: int64` — how many `Refund` objects point at this sale (`0` when there are none).
  - `net_cents: int64` — `amount_cents` minus the total `amount_cents` of the `Refund` objects pointing at this sale (equal to `amount_cents` when there are none).
- An index on `default::Sale` over the pair `(.channel, .occurred_at)`.

The change must be delivered as a **new migration file** in `dbschema/migrations/` and applied to the running branch, leaving the migration history in sync. Bare DDL applied outside the migration system is not acceptable.

### 2. Refund ingestion

`/home/user/analytics/data/refunds.json` is a JSON array of refund records. Every record is an object with exactly these keys:

```json
{
  "external_id": "RF-0001",
  "order_ref": "ORD-0007",
  "amount_cents": 1250,
  "refunded_at": "2024-02-11T09:30:00Z"
}
```

Ingestion must be an idempotent upsert keyed on `external_id`, and must never delete refunds that are absent from the file:

- A record whose `order_ref` matches no existing `Sale` is **skipped** (it is not an error, and nothing is written for it).
- A record whose `external_id` is not yet in the database is **inserted**.
- A record whose `external_id` is already in the database and whose linked sale, `amount_cents` and `refunded_at` all already match is **unchanged**.
- A record whose `external_id` is already in the database but which differs in any of those three values is **updated** so that the stored refund matches the file.

Validation happens before anything is written, and a file that fails validation must leave the database **byte-for-byte unchanged** (no partial ingestion):

- `amount_cents` must be an integer greater than or equal to `1`.
- No `external_id` may appear twice within the same file.

### 3. Analytics report

Produce a report over a set of *in-scope* sales. By default every `Sale` is in scope; when a month is supplied, only sales whose `occurred_at` falls inside that calendar month **in UTC** are in scope. A sale's `net_cents` always accounts for *all* of its refunds, regardless of when those refunds happened.

The report is a single JSON object whose top-level keys are exactly `window`, `grand_total`, `monthly_by_channel`, `channel_totals`, `category_rank` and `empty_categories`.

- `window` — object with exactly the key `month`: the requested month as a `"YYYY-MM"` string, or `null` when no month was requested.

- `grand_total` — object with exactly the keys `sale_count`, `unit_count`, `gross_cents`, `refund_cents`, `net_cents`, `month_count`, `channel_count`, `category_count` (all integers), computed over the in-scope sales. `gross_cents` sums `amount_cents`, `net_cents` sums `net_cents`, `refund_cents` is `gross_cents - net_cents`, `unit_count` sums `units`, and the three `*_count` fields are the number of distinct UTC months, distinct channels and distinct categories present among the in-scope sales. Every field is `0` when nothing is in scope.

- `monthly_by_channel` — array with one object per `(UTC month, channel)` pair that has at least one in-scope sale. Pairs with no sales must not appear at all. Each object has exactly the keys `month` (`"YYYY-MM"`), `channel`, `sale_count`, `unit_count`, `gross_cents`, `refund_cents`, `net_cents`, `mean_net_cents`, `min_net_cents`, `max_net_cents`, `stddev_net_cents` and `top_orders`, where:
  - `sale_count`, `unit_count`, `gross_cents`, `refund_cents`, `net_cents`, `min_net_cents`, `max_net_cents` are integers derived from the group's sales the same way as in `grand_total` (`min`/`max` are over the sales' `net_cents`).
  - `mean_net_cents` is the arithmetic mean of the group's `net_cents`, rounded to 2 decimal places.
  - `stddev_net_cents` is the **sample** standard deviation of the group's `net_cents`, rounded to 2 decimal places, and is `null` whenever `sale_count` is less than 2.
  - `top_orders` is an array of at most 3 objects with exactly the keys `order_ref` and `net_cents`, holding the group's highest-`net_cents` sales ordered by `net_cents` descending and then by `order_ref` ascending.
  - The array itself is ordered by `month` ascending, then `channel` ascending.

- `channel_totals` — array with one object per channel that has at least one in-scope sale, each with exactly the keys `channel`, `sale_count`, `net_cents` and `share_pct`. `share_pct` is that channel's `net_cents` as a percentage of `grand_total.net_cents`, rounded to 2 decimal places, and is `0.0` when `grand_total.net_cents` is `0`. Ordered by `net_cents` descending, then `channel` ascending.

- `category_rank` — array with one object per category that has at least one in-scope sale, each with exactly the keys `category` (the category name), `region`, `sale_count`, `net_cents`, `rank` and `percentile`. Ranking is over exactly the categories present in this array:
  - `rank` is `1` plus the number of those categories whose `net_cents` is strictly greater, so tied categories share a rank and subsequent ranks are skipped.
  - `percentile` is `100 * (number of those categories whose net_cents is less than or equal to this one) / (total number of those categories)`, rounded to 2 decimal places.
  - Ordered by `rank` ascending, then `category` ascending.

- `empty_categories` — array of the names of every `Category` with no in-scope sale, sorted ascending.

### 4. Python surface

The report and the ingestion must be reachable both as an importable async API and as a command-line tool.

## Implementation Hints

- Project path: `/home/user/analytics`. All commands are run from that directory.
- The local Gel server is started by `/usr/local/bin/gel-start`, which is idempotent and blocks until the server answers queries; run it whenever the server is not accepting connections. Do **not** run `gel project init`, do not create a second instance or branch, and do not launch a server any other way.
- Connection settings are already exported as the `GEL_DSN` and `GEL_CLIENT_SECURITY` environment variables, so the `gel` CLI and the `gel` Python package (both already installed) connect without extra flags. The project has a `gel.toml` and its schema directory is `dbschema/`.
- Do not insert, update or delete any `Category` or `Sale` object, and do not change their existing properties.
- Create the Python package directory `/home/user/analytics/analytics/` containing at least `__init__.py`, `rollups.py` and `cli.py`.
- `analytics/rollups.py` must define exactly these two coroutine functions, importable as `analytics.rollups.build_report` and `analytics.rollups.ingest_refunds`:
  - `async def build_report(client, month=None)` — `client` is a `gel.AsyncIOClient`; `month` is `None` or a `"YYYY-MM"` string. Returns the report described above as a plain Python `dict`/`list`/`str`/`int`/`float`/`None` structure (JSON-serialisable with `json.dumps`, no `Decimal`, no `datetime`, no Gel object wrappers). Raises `ValueError` if `month` does not match `^[0-9]{4}-(0[1-9]|1[0-2])$`.
  - `async def ingest_refunds(client, records)` — `client` is a `gel.AsyncIOClient`; `records` is a list of already-parsed refund dicts using the keys shown above. Returns a plain `dict` with exactly the keys `inserted`, `updated`, `unchanged`, `skipped`, `refund_total_count`, `refund_total_cents` (all integers), where the first four count the records of this call and the last two are the database-wide `Refund` count and summed `amount_cents` after the call. Raises `ValueError` on an invalid file, without having written anything.
- The EdgeQL text that `analytics/rollups.py` sends for the report must use EdgeQL's top-level `group` statement; graders check that the source of `analytics/rollups.py` contains a `group ... by ...` statement.
- Command: `python3 -m analytics.cli <subcommand> [options]`, with these two subcommands:
  - `python3 -m analytics.cli ingest-refunds --file <path>` — reads the JSON array at `<path>`, ingests it, and prints the ingestion summary object as JSON.
  - `python3 -m analytics.cli report [--month YYYY-MM]` — prints the report object as JSON.
- On success the command exits `0` and writes **only** the JSON document to stdout (a trailing newline is fine). On any failure stdout must be completely empty, and the exit code and a stderr message are:
  - `2` — missing or unrecognised subcommand, or an unrecognised option.
  - `3` — the `--file` path does not exist; stderr contains `refunds file not found`.
  - `4` — the refunds file is invalid; stderr contains `invalid refunds file`; the database is left unchanged.
  - `5` — `--month` is not a valid `YYYY-MM` value; stderr contains `invalid month`.
- Every count field and every `*_cents` field described above must be emitted as a JSON integer (no fractional part). The rounded fields `mean_net_cents`, `stddev_net_cents`, `share_pct` and `percentile` must be JSON numbers carrying at most 2 decimal places (or `null` where the specification says so). No numeric value may be emitted as a string.

