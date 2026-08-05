# Self-documenting Gel schema: live introspection docs + lint generator

## Background
The museum IT team keeps its data model in a **Gel 6** database and is tired of hand-written, always-stale schema documentation. They want a generator that reads the *live* database and emits a machine-readable schema description plus a human-readable reference, together with a small set of schema hygiene findings ("lint").

A Gel 6 server runs locally inside this container and the project at `/home/user/museum` is already initialised and linked to it (there is a `gel.toml`, a `dbschema/` directory and an empty starting schema). Run `start-gel` if you need to make sure the server is up; it is idempotent.

## Requirements

### 1. The schema
Author the schema below in the project and apply it with Gel's migration workflow, so that `gel migration status` reports the database is up to date. Names, spellings, types, modifiers, annotation texts and deletion policies must match **exactly**; nothing else may exist in these two modules.

**Module `default`**

* A user-defined abstract annotation called `doc`. Every documentation string below is attached with this annotation.
* Scalar `AccessionCode`, extending `str`, restricted to the regular expression `^[A-Z]{3}-[0-9]{4}$`.
* Scalar `ConditionGrade`, an enum with the values `Pristine`, `Good`, `Fair`, `Poor`, in that order.
* Scalar `Rating`, extending `int64`, with no constraints.
* Abstract type `Documented`
  * `doc`: `Entities that carry a curator-facing summary.`
  * optional single property `summary: str`
* Abstract type `Tracked`
  * `doc`: `Entities that record their creation time.`
  * required single property `created_at: datetime`, defaulting to the time of the statement
* Abstract type `Artifact`, extending **both** `Documented` and `Tracked`
  * `doc`: `Any physical item held by the museum.`
  * required single property `accession: AccessionCode`, exclusive
  * required single property `title: str`
  * optional single property `condition: ConditionGrade`
  * optional single link `gallery: Gallery`, with **no** deletion policy declared
  * computed, required, single property `display_label: str` — the title followed by the accession code in square brackets (e.g. `Sunflowers [PNT-0042]`)
  * an index on the `title` property
* Type `Painting`, extending `Artifact`
  * `doc`: `A framed two-dimensional work.`
  * required single property `medium: str`
  * optional single properties `width_cm: float64` and `height_cm: float64`
  * a type-level `expression` constraint enforcing that both `width_cm` and `height_cm` are greater than zero
* Type `Sculpture`, extending `Artifact`
  * `doc`: `A three-dimensional work.`
  * required single property `material: str`
  * optional single property `weight_kg: float64`
* Type `Curator`
  * `doc`: `A staff member responsible for artifacts.`
  * required single property `email: str`, exclusive, itself annotated with `doc`: `Primary contact address.`
  * required single property `name: str`
  * multi property `specialties: str`
* Type `Gallery` — deliberately **undocumented** (no `doc` annotation)
  * required single property `name: str`, exclusive
  * optional single property `floor: int64`
  * computed multi link `artifacts`, the reverse of `Artifact.gallery`
* Type `Exhibition`
  * `doc`: `A temporary show assembled from artifacts.`
  * required single properties `title: str`, `opens_on: datetime`, `closes_on: datetime`
  * a type-level `expression` constraint enforcing `closes_on` is later than `opens_on`
  * required single link `lead_curator: Curator`, target deletion policy `delete source`
  * multi link `exhibits: Artifact`, target deletion policy `allow`, annotated with `doc`: `Artifacts on display, in curated order.`, and carrying the link properties `display_order: int64` and `insured_value: int64`
* Type `LoanRecord` — deliberately **undocumented**
  * required single link `artifact: Artifact`, target deletion policy `delete source`
  * required single property `borrower: str`
  * optional single property `condition_rating: Rating`

**Module `archive`**

* Type `StorageBox`
  * `default::doc`: `A crate in climate-controlled storage.`
  * required single property `code: str`, exclusive
  * multi link `contents` targeting `default::Artifact`, with **no** deletion policy declared

### 2. The generator
Write a Python CLI at `/home/user/museum/tools/schema_docs.py` that documents **one module of the live database** per run. It must obtain everything it reports by querying the running Gel instance; it must never read, parse or import the `.gel` source files or the migration scripts (it has to keep working when `dbschema/` is missing, and it has to reflect schema changes that were applied to the database after it was written).

### 3. Lint rules
The generator also reports schema hygiene findings, using exactly these rule ids and message texts:

* `L001` — a **non-abstract** object type where neither the type itself nor any of its reported properties carries an `std::exclusive` constraint. Message: `type '<Type>' has no exclusive constraint`
* `L002` — an object type (abstract or not) with an empty documentation string. Message: `type '<Type>' has no doc annotation`
* `L003` — a non-computed link, on any reported object type, whose resolved target deletion policy is `restrict`. Message: `link '<Type>.<link>' uses the default restrict deletion policy`
* `L004` — a reported scalar type with no constraints and no enum values. Message: `scalar type '<Scalar>' has no constraints`

## Implementation Hints

* Project path: `/home/user/museum`. The generator lives at `/home/user/museum/tools/schema_docs.py` and is always invoked with `/home/user/museum` as the working directory.
* Command: `python3 tools/schema_docs.py --module <MODULE> --out-dir <DIR>`. Both options are mandatory.
* On success it must create `<DIR>` if needed, write `<DIR>/schema.json` and `<DIR>/SCHEMA.md`, print the absolute path of the JSON file on the first stdout line and the absolute path of the Markdown file on the second, and exit `0`.
* If `<MODULE>` is not the name of a module that exists in the database, it must write nothing at all (no `<DIR>`, no files), print a line matching `error: unknown module: <MODULE>` on stderr, and exit `3`.
* Output must be byte-stable: two consecutive runs against an unchanged database must produce byte-identical `schema.json` and `SCHEMA.md`. No timestamps, no host names, no run counters, no unordered iteration leaking into the files.
* Only the requested module is documented. Standard-library and system schema objects (`std`, `schema`, `cfg`, `sys`, `ext`, ...), types produced by aliases, union/compound types and internal types must never appear as documented types. The implicit `id` property and the `__type__` link must never be reported, and the implicit `source`/`target` pointers of a link must never be reported as link properties.
* Name qualification: a schema name that belongs to the module being documented is written **without** its module prefix (`Artifact`); every other name keeps its full database name (`std::str`, `default::Artifact`, `std::exclusive`).
* `schema.json` must be UTF-8, must be valid JSON, and must be an object with exactly the keys `module`, `object_types`, `scalar_types`, `lint`:
  * `module`: the module name as passed on the command line.
  * `object_types`: array, sorted by `name` ascending, of objects with exactly the keys `name`, `abstract`, `doc`, `bases`, `ancestors`, `constraints`, `indexes`, `properties`, `links`.
    * `abstract`: boolean. `doc`: the value of the type's `default::doc` annotation, or `""` when it has none.
    * `bases`: the type's direct bases, `ancestors`: all of its ancestors; both restricted to types of the documented module and both sorted ascending.
    * `constraints`: the fully qualified names of the type-level constraints, sorted ascending. `indexes`: the index expressions as stored by the database, sorted ascending.
    * `properties`: array, sorted by `name` ascending, of objects with exactly the keys `name`, `target`, `required`, `cardinality`, `computed`, `constraints`, `doc`. Inherited properties are reported on every type that has them.
    * `links`: array, sorted by `name` ascending, of objects with exactly the keys `name`, `target`, `required`, `cardinality`, `computed`, `on_target_delete`, `link_properties`, `constraints`, `doc`. Inherited links are reported on every type that has them.
    * `required` and `computed` are booleans; `cardinality` is the string `single` or `multi`; `constraints` are fully qualified constraint names sorted ascending; `doc` follows the same rule as for types.
    * `on_target_delete` is `null` for computed links, and otherwise one of the strings `restrict`, `delete source`, `allow`, `deferred restrict`.
    * `link_properties`: array, sorted by `name` ascending, of objects with exactly the keys `name` and `target`.
  * `scalar_types`: array, sorted by `name` ascending, of objects with exactly the keys `name`, `doc`, `bases`, `enum_values`, `constraints`. `bases` holds the direct bases sorted ascending (qualified by the rule above), `enum_values` holds the enum members **in declaration order** and is `[]` for non-enum scalars, `constraints` holds the fully qualified constraint names sorted ascending, and `doc` follows the same rule as for object types.
  * `lint`: array of objects with exactly the keys `rule`, `subject`, `message`, sorted by `rule` ascending and then by `subject` ascending (plain ASCII ordering). The `subject` of `L001`/`L002` is the type name, of `L003` the string `<Type>.<link>`, and of `L004` the scalar name. `message` is exactly the text given in the lint rule list above.
* `SCHEMA.md` must start with the line `# Schema Reference: <MODULE>` and must then contain, in this order, the headings `## Object Types`, `## Scalar Types` and `## Lint Findings`. Under `## Object Types` every documented object type must have its own `### <Name>` heading, under `## Scalar Types` every documented scalar type must have its own `### <Name>` heading, and the `## Lint Findings` section must list every finding as a line that contains the rule id followed by that finding's message. Anything else you add is up to you.
