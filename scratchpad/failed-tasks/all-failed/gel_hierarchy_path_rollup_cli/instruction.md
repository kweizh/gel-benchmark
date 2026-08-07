# Self-Maintaining Category Ancestry and Subtree Roll-Ups in Gel

## Background
A product catalog is stored in a local **Gel 7.1** database (formerly EdgeDB). The project at `/home/user/catalog` already contains `gel.toml`, `dbschema/default.gel`, one applied migration in `dbschema/migrations/`, and seeded data: 26 `Category` objects forming a tree through an optional `parent` link (root categories have no parent), and 112 `Product` objects each linked to exactly one `Category`.

Today nothing in the database knows how deep a category sits or what its full path is, and every report that needs "this category plus everything below it" has to be written by hand in the application. Your job is to move all of that into the database itself and to ship a small set of reporting queries that the operations team runs through the `gel` CLI.

## Requirements

### 1. Ancestry data stored on every category
Every `Category` must carry two **stored** (non-computed) required properties:
- `depth` (`int64`): the number of ancestors the category has, so root categories have `depth` 0.
- `path` (`str`): `/` followed by the slugs from the root down to the category itself, joined with `/`. A root category with slug `tools` has path `/tools`; its child with slug `power` has path `/tools/power`; that child's child with slug `saws` has path `/tools/power/saws`.

Both values must be produced by the database, never by the client: a statement such as `insert Category { slug := ..., name := ..., parent := ... }` that supplies no `depth` and no `path` must still store the correct values, at any nesting level (the catalog is used up to 6 levels deep, i.e. `depth` 0 through 5). Introspecting the schema must show `depth` and `path` as ordinary stored properties (they must not be computed) that each carry a mutation rewrite for `insert`.

All 26 categories that already exist must end up with correct `depth` and `path` values too.

### 2. Navigation links
`Category` must expose two **computed** multi links:
- `children`: the categories whose `parent` is this category.
- `ancestors`: every strict ancestor of this category (its parent, its parent's parent, and so on up to the root); empty for root categories.

### 3. Uniqueness
Two categories must never share the same `path`. Enforce it with a `std::exclusive` constraint on `Category` whose subject expression is the path, so that inserting a second child with an already-used slug under the same parent — or a second root with an already-used root slug — fails with a `ConstraintViolationError` and changes nothing.

### 4. Re-parenting through a `Relocation` object
Add an object type `Relocation` used to move a category (with its whole subtree) somewhere else in the tree. It has:
- `category`: required single link to the `Category` being moved.
- `new_parent`: optional single link to the `Category` that must become its parent; when it is omitted the moved category becomes a root category.
- `from_path` and `to_path`: required `str` properties that the database fills in automatically through mutation rewrites on `insert` — `from_path` is the moved category's `path` before the move and `to_path` is its `path` after the move. Clients never supply them.

A single `insert Relocation { ... }` statement, with no follow-up statement of any kind, must atomically re-point the moved category's `parent` **and** leave `depth` and `path` correct for the moved category *and* for every one of its descendants, however deep the subtree is. `Relocation` must declare two triggers, named exactly `apply_relocation` and `reject_cycles`, that both fire after `insert`.

Relocations that would corrupt the tree must be refused, aborting the whole statement so that no `Relocation` row is stored and no category changes at all:
- If `new_parent` is the moved category itself or any of its descendants, the statement must fail with an error message that contains the exact text `CATEGORY_CYCLE`.
- If the move would give the moved category the same `path` as an existing category, the statement must fail with a `ConstraintViolationError`.

The only supported way to re-parent a category is inserting a `Relocation`; nothing will ever assign `Category.parent` directly.

### 5. Reporting queries
Ship three EdgeQL files that the operations team executes from the project root with `gel query -F json -f <file>`. Each file must hold a single, parameter-free statement, and its JSON result must match the shape below exactly — no extra keys, no missing keys.

`queries/tree.edgeql` — the whole tree flattened, one element per category, ordered by `path` ascending:
```json
[{"path": "/tools", "slug": "tools", "name": "Tools", "depth": 0, "parent_path": null, "child_count": 4}]
```
`parent_path` is the parent's `path` and is `null` for root categories; `child_count` counts direct children only.

`queries/subtree_rollup.edgeql` — one element per category, ordered by `path` ascending, aggregating the products of the category itself **plus all of its descendants**:
```json
[{"path": "/tools", "slug": "tools", "depth": 0, "product_count": 32, "total_stock": 630, "min_price": 7.47, "max_price": 89.49, "avg_price": 48.15}]
```
`total_stock` is the sum of `stock`; `min_price`/`max_price` are the extreme `price` values; `avg_price` is the mean `price` rounded to 2 decimal places. When a category's subtree holds no product at all, `product_count` and `total_stock` must be `0` and `min_price`, `max_price` and `avg_price` must all be `null`.

`queries/root_totals.edgeql` — per-root totals, which **must** be produced with EdgeQL's top-level `group` statement, one element per root category that has at least one product in its subtree, ordered by `root_slug` ascending:
```json
[{"root_slug": "tools", "product_count": 32, "total_stock": 630, "avg_price": 48.15}]
```
`avg_price` is again the mean `price` rounded to 2 decimal places.

### 6. Migrations
Every schema change must live in migration files under `dbschema/migrations/` and be applied to the running database, so that `gel migration status` reports the database up to date with no pending changes. Do not remove or rewrite the existing migration, and do not delete the seeded categories or products.

## Implementation Hints
- Project path: `/home/user/catalog`
- Gel version: 7.1, running locally in this container. Start command: `gel-start` (idempotent; starts the local server in the background and returns once it accepts queries). The `gel` CLI is already configured with the connection details through environment variables, so `gel` commands work from any directory once the server is up.
- Commands are executed from the project root, e.g. `gel query -F json -f queries/tree.edgeql`.
- The numbers shown in the JSON examples above are illustrative shapes, not expected values.
- Prices are `decimal` and stock is `int64`; keep `product_count`, `total_stock`, `depth` and `child_count` as JSON integers.
- The verifier inserts additional categories, inserts `Relocation` objects (including invalid ones), and re-runs the three query files afterwards, so every mechanism must keep working on data that did not exist when you built the schema.

