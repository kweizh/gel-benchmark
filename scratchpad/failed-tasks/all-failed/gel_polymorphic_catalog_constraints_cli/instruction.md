# Evolve a Polymorphic Product Catalog on Gel 6 (CLI + SDL + migrations)

## Background

This container runs a local **Gel 6** server (the graph-relational database formerly known as EdgeDB) together with the `gel` CLI. An e-commerce catalog project already exists at `/home/user/catalog`: its schema lives in `/home/user/catalog/dbschema`, two migrations have already been created and applied to the `main` branch of the local instance, and the branch already holds **12 product rows** of real data.

The current model is far too weak: prices and stock are unconstrained, the product title is a single free-form string, physical goods and digital goods cannot be told apart, and nothing prevents duplicate listings. Your job is to redesign the model, migrate the **already-populated** database to it **without losing or corrupting a single existing row**, add the missing catalog data, and ship a deterministic reporting command that exercises the new polymorphic model.

## Requirements

### 1. Target schema (module `default`)

Two custom scalar types:

- `Sku`, derived from `str`, that accepts only values matching the regular expression `^[A-Z]{3}-[0-9]{4}$`.
- `ListingStatus`, an enumerated scalar whose values are exactly, in this order: `draft`, `active`, `archived`.

An **abstract** object type `Product` carrying:

- `sku`: required, of type `Sku`, unique across the whole catalog.
- `brand`: required `str`.
- `name`: required `str`.
- `title`: a computed single `str` equal to `brand`, then the exact three characters ` | ` (space, vertical bar, space), then `name`. It must be computed, not stored.
- `price_cents`: required `int64` that may never be smaller than 1.
- `discount_cents`: required `int64` that may never be negative and that defaults to 0 for newly inserted products.
- `final_price_cents`: a computed single `int64` equal to `price_cents` minus `discount_cents`.
- `units_in_stock`: required `int64` that may never be negative. This is the property currently called `stock`, and its stored values must survive the migration; afterwards no property named `stock` may remain anywhere in the model.
- `listing_status`: required `ListingStatus`, with **no** default value.
- A type-level rule that rejects any product whose `discount_cents` is greater than its `price_cents`.
- A type-level uniqueness rule over the pair (`brand`, `name`) that is enforced across the entire catalog, regardless of the concrete product type.
- `accessories`: a `multi` link to `Product` carrying a **required** link property `rank` of type `int16`, plus a rule that forbids one product from having two accessories with the same `rank`.
- `accessory_of`: a computed `multi` backlink exposing the products that list this product among their accessories.
- An index on the `brand` property, and a composite index on the pair (`listing_status`, `price_cents`) in that order. No index over the old `title` expression may survive.

Three concrete object types extending `Product`:

- `Book`: required `author: str`, optional `pages: int64`.
- `Apparel`: required `size_label: str`.
- `DigitalDownload` (new): required `file_size_kb: int64` that may never be smaller than 1.

### 2. Data-preserving evolution of the populated branch

- All 12 pre-existing products must still exist afterwards, each keeping the **same `id`** and the same `sku` as recorded in `/opt/task/initial_state.json`, the same concrete type, the same `price_cents`, the same subtype-specific values (`author`, `pages`, `size_label`), and a `units_in_stock` equal to its former `stock`.
- `brand` and `name` of each pre-existing product must be derived from its former `title`, split at the ` | ` separator, so that the new computed `title` is byte-identical to the former `title`.
- `listing_status` of a pre-existing product must be `active` when its stock was greater than 0 and `archived` otherwise. No pre-existing product may end up as `draft`.
- `discount_cents` must be 0 for every pre-existing product.
- The migration history must stay healthy: `gel migration status` must report that the branch is in sync with `/home/user/catalog/dbschema`; the two already-applied migrations must remain in the recorded history untouched (do not squash, rewrite, renumber or delete them); at least one new migration must be added; and the number of `.edgeql` files under `/home/user/catalog/dbschema/migrations` must equal the number of migrations recorded in the database.

### 3. Catalog data to add

Insert exactly two new products, both `DigitalDownload`, and no other new products:

| sku | brand | name | price_cents | discount_cents | units_in_stock | listing_status | file_size_kb |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `DLD-0001` | `Northwind` | `Trail Guide PDF` | 1200 | 200 | 999 | `active` | 8200 |
| `DLD-0002` | `Cobalt` | `Layering Course` | 4900 | 0 | 500 | `draft` | 152000 |

Wire up accessories exactly like this and nowhere else in the catalog:

- the product with sku `BOK-0001` has two accessories: `DLD-0001` with `rank` 1 and `APP-0005` with `rank` 2;
- the product with sku `APP-0001` has one accessory: `APP-0005` with `rank` 1.

### 4. Reporting command

Provide an executable command at `/home/user/catalog/bin/catalog-report` that takes no arguments, queries the **live** database every time it runs, prints a single JSON document to stdout, and exits with code 0. It must never modify the database, and two consecutive runs must produce byte-identical output. It must not embed precomputed results: if catalog rows change, its output must change accordingly.

## Implementation Hints

- Project path: `/home/user/catalog`. Schema directory: `/home/user/catalog/dbschema` (schema files and the `migrations/` folder live there).
- The local Gel server may not be running yet. Run `gel-start` to make sure it is up; it is idempotent and exits 0 once the server accepts queries. Connection settings (host, port, TLS, branch `main`) are already exported in the environment, so `gel` commands need no connection flags.
- Do **not** run `gel project init`, `gel instance create`, `gel instance link`, `gel branch create/switch/wipe`, or `gel database wipe`, and do not create a second instance, branch or data directory: the graded database is the `main` branch of the running local instance. There is no network access.
- `/opt/task/initial_state.json` is a read-only snapshot of the database as it was handed to you (recorded migration names, and for every product its `id`, `sku`, `title`, `price_cents`, `stock`, concrete type and subtype fields). You may read it.
- Keep the dataset small; do not add products beyond the two required ones.
- Output shape of `/home/user/catalog/bin/catalog-report`: a JSON object with exactly the keys `products`, `restock_alerts` and `totals`.
  - `products` is an array holding **every** product in the catalog, sorted by `sku` in ascending byte order. Each element is an object with exactly the keys `sku`, `brand`, `name`, `title`, `kind`, `listing_status`, `price_cents`, `discount_cents`, `final_price_cents`, `units_in_stock`, `accessories` and `detail`. `kind` is the concrete type name without its module prefix (`Book`, `Apparel` or `DigitalDownload`). `listing_status` is the enum value as a string. `accessories` is an array, sorted by `rank` ascending, whose elements are objects with exactly the keys `sku` (string) and `rank` (number); it is an empty array when the product has no accessories. `detail` is an object holding exactly the subtype-specific fields of that product: `author` and `pages` for a `Book` (with `pages` being JSON `null` when it has no value), `size_label` for an `Apparel`, `file_size_kb` for a `DigitalDownload`.
  - `restock_alerts` is an array of sku strings sorted in ascending order, containing exactly the products that are **not** digital downloads, whose `listing_status` is not `archived`, and whose `units_in_stock` is strictly less than 5.
  - `totals` is an object with exactly the keys `product_count`, `book_count`, `apparel_count`, `digital_count`, `active_inventory_value_cents` and `average_active_final_price`. The first five are JSON numbers: the counts of all products and of each concrete type, and the sum of `final_price_cents` multiplied by `units_in_stock` over the products whose `listing_status` is `active` (0 when there are none). `average_active_final_price` is a JSON **string**: the arithmetic mean of `final_price_cents` over the products whose `listing_status` is `active`, rounded half-up to two decimal places and rendered with exactly two decimals, for example `1234.50`; it must be `0.00` when no product is active.
  - Every value other than `average_active_final_price`, the sku/brand/name/title/kind/listing_status strings and the string subtype fields must be a JSON number (never a numeric string).
- Constraint violations must be enforced by the database itself, so that invalid writes attempted through the `gel` CLI or any client fail with a Gel constraint-violation error instead of being silently accepted or filtered in application code.

