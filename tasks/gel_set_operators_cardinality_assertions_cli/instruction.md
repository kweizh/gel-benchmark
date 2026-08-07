# Gel — Set-Operator Reconciliation CLI (EdgeQL through the `gel` command line only)

## Background

`/home/user/reconcile` holds a **Gel 6** project used by a stock-audit team. Its schema
(`dbschema/default.gel`, `module default`) already declares four object types, a first migration has
already been created and applied, and the local Gel instance is already seeded with audit data:

- `Warehouse` — `required code: str` (exclusive), `required region: str`
- `Sku` — `required code: str` (exclusive), `required label: str`
- `ShelfCount` — one physical count line: `required warehouse: Warehouse`, `required sku: Sku`, `required quantity: int64`, `required tag: str`
- `LedgerLine` — one book-keeping line: the same four fields

Nothing guarantees that a `(warehouse, sku)` pair appears at most once in `ShelfCount` (or in
`LedgerLine`): re-counts, split pallets and correction lines all produce extra rows. Auditors need a
reconciliation tool that answers *set-shaped* questions about this data — what was counted but never
booked, what was booked but never counted, what overlaps, what is duplicated, and where a single
answer is guaranteed versus where it is not.

Build that tool.

## Requirements

### 1. Schema computeds

Extend `module default` in `dbschema/default.gel` with the computed pointers listed below, then create
and apply a new migration with the Gel CLI. When you are done, `dbschema/migrations/` must contain at
least two migration files and the instance's migration state must be in sync with `dbschema/`.

Each computed **must be declared with exactly the modifier keywords shown** (they are asserted through
`schema::ObjectType` introspection, i.e. the resulting pointer's `cardinality` and `required` values),
and each must be a real computed pointer (non-empty `expr`).

On `Warehouse`:

| pointer | declared modifiers | meaning |
| --- | --- | --- |
| `shelf_units` | `required single` (`std::int64`) | sum of `quantity` over every `ShelfCount` row whose `warehouse` is this warehouse; `0` when there are none |
| `ledger_units` | `required single` (`std::int64`) | same, over `LedgerLine` rows |
| `counted_skus` | `multi` (link to `default::Sku`) | the distinct `Sku` objects that have at least one `ShelfCount` row in this warehouse |
| `ledger_skus` | `multi` (link to `default::Sku`) | the distinct `Sku` objects that have at least one `LedgerLine` row in this warehouse |
| `unreconciled_skus` | `multi` (link to `default::Sku`) | the distinct symmetric difference of `counted_skus` and `ledger_skus` |
| `is_balanced` | `required single` (`std::bool`) | `true` if and only if `unreconciled_skus` is empty **and** `shelf_units` equals `ledger_units` |

On `Sku`:

| pointer | declared modifiers | meaning |
| --- | --- | --- |
| `sole_warehouse` | `single`, not required (link to `default::Warehouse`) | the one `Warehouse` in which this SKU has `ShelfCount` rows, **only** when that warehouse is unique; the empty set when the SKU has shelf counts in zero warehouses or in two or more warehouses. Reading this pointer must never raise an error for any SKU in the database. |

### 2. Reconciliation CLI

Create the executable entrypoint `/home/user/reconcile/reconcile.sh`. It is always invoked as

```
bash /home/user/reconcile/reconcile.sh <report> [flags]
```

and must behave identically no matter which directory it is invoked from. Every EdgeQL statement it
runs must live in a `.edgeql` file inside `/home/user/reconcile/queries/` (that directory must exist
and hold at least four non-empty `.edgeql` files when you are finished) and must be executed against
the live database through the `gel` command-line client on every invocation — results are read fresh
each time and must never be cached, snapshotted or hard-coded, because the database contents change
between invocations.

On success the command writes **exactly one JSON document to stdout and nothing else**, and exits `0`.
Every integer field is a JSON number, every boolean field is a JSON boolean. Whitespace/indentation of
the JSON is free. All arrays of code strings contain **distinct** values sorted in ascending
byte-order of the string, unless stated otherwise.

#### `balance`

```json
{
  "report": "balance",
  "warehouses": [
    {
      "code": "<warehouse code>",
      "shelf_units": 0,
      "ledger_units": 0,
      "counted_skus": ["<sku code>"],
      "ledger_skus": ["<sku code>"],
      "both": ["<sku code>"],
      "shelf_only": ["<sku code>"],
      "ledger_only": ["<sku code>"],
      "all_skus": ["<sku code>"],
      "unreconciled_skus": ["<sku code>"],
      "is_balanced": true
    }
  ]
}
```

One entry per `Warehouse` in the database, ordered by `code` ascending. `counted_skus`,
`ledger_skus`, `unreconciled_skus`, `shelf_units`, `ledger_units` and `is_balanced` are the SKU codes
/ values of the schema computeds of section 1. `both` is the intersection of `counted_skus` and
`ledger_skus`; `shelf_only` is `counted_skus` minus `ledger_skus`; `ledger_only` is `ledger_skus`
minus `counted_skus`; `all_skus` is their union. Empty arrays where nothing matches.

#### `sku <CODE>` and `sku <CODE> --strict`

```json
{
  "report": "sku",
  "code": "<CODE as given on the command line>",
  "exists": true,
  "sole_warehouse": "<warehouse code>",
  "shelf_warehouses": ["<warehouse code>"],
  "shelf_units": 0,
  "ledger_units": 0
}
```

- `exists` — whether a `Sku` with that `code` exists.
- `sole_warehouse` — the code of the `Sku.sole_warehouse` computed, or JSON `null` when it is empty.
- `shelf_warehouses` — codes of the distinct warehouses in which the SKU has `ShelfCount` rows.
- `shelf_units` / `ledger_units` — the SKU's total `quantity` across **all** warehouses in
  `ShelfCount` and in `LedgerLine` respectively; `0` when there are no rows.
- When the SKU code is unknown, and `--strict` was **not** passed: exit `0` and report
  `"exists": false`, `"sole_warehouse": null`, `"shelf_warehouses": []`, `"shelf_units": 0`,
  `"ledger_units": 0`.
- When the SKU code is unknown and `--strict` **was** passed: print nothing on stdout, write a line
  containing `error: sku not found: <CODE>` to stderr, and exit with status `3`.
- When the SKU code is known, `--strict` changes nothing: same JSON, exit `0`.

#### `duplicates` and `duplicates --assert`

```json
{
  "report": "duplicates",
  "clean": false,
  "pairs": [
    {"warehouse": "<warehouse code>", "sku": "<sku code>", "rows": 2, "quantities": [1, 2]}
  ]
}
```

`pairs` lists every `(warehouse, sku)` combination having **more than one** `ShelfCount` row, ordered
by `warehouse` ascending then `sku` ascending. `rows` is how many `ShelfCount` rows that combination
has; `quantities` lists those rows' `quantity` values sorted ascending (duplicated values are kept, so
`quantities` always has exactly `rows` elements). `clean` is `true` exactly when `pairs` is empty.
With `--assert`, if at least one such combination exists the command must print nothing on stdout,
write a line containing `error: duplicate shelf counts` to stderr and exit with status `4`; if there
are none it prints the same JSON as without the flag and exits `0`.

#### `matrix` and `matrix --sku <CODE>`

```json
{
  "report": "matrix",
  "cells": [
    {"warehouse": "<warehouse code>", "sku": "<sku code>", "shelf": 0, "ledger": 0, "delta": 0}
  ],
  "total_delta": 0
}
```

`cells` covers the **full cross product** of every `Warehouse` with every `Sku` in the database —
including combinations that have no `ShelfCount` and no `LedgerLine` row at all, which report
`"shelf": 0, "ledger": 0, "delta": 0`. `shelf` is the sum of `ShelfCount.quantity` for that exact
`(warehouse, sku)` pair, `ledger` the same for `LedgerLine`, and `delta` is `shelf - ledger`. Cells
are ordered by `warehouse` ascending then `sku` ascending. `total_delta` is the sum of `delta` over
the emitted cells. With `--sku <CODE>` only the cells for that one SKU are emitted (still one per
warehouse, still ordered by warehouse); if the code matches no `Sku`, `cells` is `[]` and
`total_delta` is `0`, exit `0`.

#### Argument errors

- An unrecognised report name: nothing on stdout, a line containing `error: unknown report: <name>`
  on stderr, exit status `2`.
- `sku` invoked with no code argument: nothing on stdout, a line containing `error: missing sku code`
  on stderr, exit status `2`.

## Implementation Hints

- Project path: `/home/user/reconcile` (contains `gel.toml`, `dbschema/default.gel` and
  `dbschema/migrations/`). Do not move or rename it.
- Command: `bash /home/user/reconcile/reconcile.sh <report> [flags]`, with the exact reports, flags,
  JSON keys, ordering, exit statuses and stderr markers specified above.
- The local Gel server is started with `gel-ctl start` (idempotent; it returns only once the server
  accepts connections) and its status can be checked with `gel-ctl status`. Connection settings are
  already exported as `GEL_*` environment variables, so plain `gel ...` commands connect to the
  seeded instance without extra connection flags. Run `gel-ctl start` before using the CLI.
- **Runtime restriction:** the solution must consist only of shell scripts and EdgeQL. No Python,
  Node/TypeScript, Ruby or Perl program may be added: after your work, searching `/home/user/reconcile`
  for files named `*.py`, `*.js`, `*.mjs`, `*.cjs`, `*.ts`, `*.rb` or `*.pl` must return nothing, and
  `reconcile.sh` must not invoke `python`, `python3`, `node`, `deno`, `bun`, `ruby` or `perl`. Shell
  utilities available in the image (including `jq`) are allowed.
- The database is mutated between invocations by the auditors' harness (rows and even whole
  warehouses and SKUs are added and removed); every report must reflect the database state at the
  moment it runs.
- Seed rows carry `tag` values that have no meaning for any report; never filter on `tag`.

