# Repair a drifted Gel migration history, then keep evolving the schema

## Background

`/home/user/inventory` is a small **Gel 6** project (a spare-parts inventory service). Its data lives on a local Gel 6 server that runs inside this container and listens on `127.0.0.1:5656`: the connection environment variables (`GEL_HOST`, `GEL_PORT`, `GEL_USER`, `GEL_BRANCH`, `GEL_CLIENT_TLS_SECURITY`) are exported for you, and the `gel` CLI is on `PATH`. There is no network access and no cloud instance involved.

A previous developer was in a hurry: some schema work was done straight against the running database instead of through the project's migration workflow, and the contents of `dbschema/` no longer agree with what the database actually has recorded. As a result the Gel tooling now refuses to create or apply migrations, and nobody can ship the schema change that is still pending. The branch `main` also holds live application data (warehouses, parts, stock levels and audit rows) that must survive the rescue operation completely untouched.

Your job is to bring the project back to a healthy, fully replayable state and then land the pending schema change.

## Requirements

1. **Get the project back in sync.** When you are done, `gel migration status`, executed inside `/home/user/inventory`, must exit with status `0` and report that the database is up to date.

2. **Recover the complete revision history onto disk.** The revision ids recorded in the database must be exactly the revision ids present in `dbschema/migrations/` — same ids, same order, one `.edgeql` file per revision, with contiguous index prefixes starting at `00001`. Every revision that the database has already recorded must be preserved exactly as recorded: do not squash, re-write, re-hash, renumber or discard any existing revision, and do not wipe, re-create or roll back the `main` branch.

3. **Lose nothing that already exists.** Every object type, property, link, constraint and cardinality that the live schema of branch `main` has right now must still be there afterwards, must be described by the SDL in `dbschema/`, and must be reproduced by the migration history on disk.

4. **Land the pending schema change as exactly one new revision**, which must end up being the newest revision in the history. It introduces the object type `default::ReorderRule` with:
   - `part`: a **required single link** to `default::Part`, carrying an **exclusive** constraint (a part can have at most one reorder rule);
   - `min_quantity`: a **required** `int64` property that rejects any value below `0`;
   - `reorder_batch`: a **required** `int64` property that rejects any value below `1`.

5. **Backfill the new type for the existing data.** In branch `main`, after your work, there must be exactly one `ReorderRule` object per existing `Part` object, where for each part:
   - `min_quantity` equals the sum of `quantity` over all `StockLevel` objects that link that part, integer-divided by `2` (rounded down); a part with no `StockLevel` object gets `0`;
   - `reorder_batch` equals `12`.

6. **Preserve the live data byte-for-byte.** Every `Warehouse`, `Part`, `StockLevel` and `AuditEvent` object that exists in branch `main` right now must still exist when you finish, with the **same `id`** and the same property values; no such objects may be added, removed or edited. The only new objects allowed are the `ReorderRule` objects required above.

7. **The history must be replayable from scratch.** Applying the contents of `dbschema/migrations/` to a brand-new *empty* branch of the same instance must succeed without manual intervention and must produce the same schema as branch `main`.

8. **No further bare-DDL revisions.** Exactly one revision currently recorded in the database was produced by a bare DDL statement rather than by the migration workflow. When you are done there must still be exactly **one** such revision recorded in branch `main` — your own work must not add another.

9. **Write a machine-readable report** to `/home/user/inventory/repair_report.json` (see below).

## Implementation Hints

- Project path: `/home/user/inventory` (contains `gel.toml` and `dbschema/`, with the schema SDL in `dbschema/default.gel` and the migration history in `dbschema/migrations/`).
- The Gel server for this project is started automatically in the background. If it is not reachable, start it with `/usr/local/bin/gel-start.sh` (idempotent) and give it a few seconds.
- Migration files are content-addressed: never assume a revision's file name, and never edit a recorded revision's script or id.
- `/home/user/inventory/repair_report.json` must be a single JSON object with exactly these five keys:
  - `history_length` (integer): the number of revisions in the repaired history.
  - `revisions` (array of strings): every revision id of the repaired history, ordered oldest first, newest last.
  - `recovered_revisions` (array of strings): the revision ids that the database had already recorded but that were *missing* from `dbschema/migrations/` before you started, ordered oldest first.
  - `ddl_revision` (string): the id of the revision that was produced by a bare DDL statement instead of the migration workflow.
  - `new_revision` (string): the id of the revision you added for `default::ReorderRule`.

  Use the revision ids exactly as the database records them (the `m1…` names), not file names.

