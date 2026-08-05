# Layered JSON Settings Resolver on Gel 7.1

## Background

A **Gel 7.1** database runs locally inside this container and already holds a seeded settings
catalog. Each settings *record* owns a chain of *layers*; every layer stores one nested JSON
document (defaults, group overrides, per-user overrides). Your job is to build the command-line
engine that collapses a record's chain into a single effective document and that safely patches
a layer of that chain.

## Environment

- Project path: `/home/user/settings-engine` (it already contains `gel.toml` and `dbschema/`
  with the migration for the schema below already applied).
- The Gel 7.1 server runs inside this container. `/usr/local/bin/gel-start.sh` starts it if it is
  not running, blocks until it is ready, and is safe to run repeatedly.
- Schema, in module `default` on branch `main`:
  - `SettingsLayer` with properties `key: str` (exclusive), `tier: str`, `doc: json`,
    `active: bool`, `revision: int64`.
  - `SettingsRecord` with properties `slug: str` (exclusive), `label: str`, and
    `multi layers: SettingsLayer` carrying the link property `precedence: int64`.
- Guaranteed about the data: every `doc` is a JSON object; every layer link has a `precedence`
  value; the `precedence` values inside one record are distinct; a layer may be shared by
  several records; `precedence` is unrelated to `tier`, to `key` ordering and to insertion order.

## Requirements

Implement a re-runnable CLI whose entrypoint is exactly
`python3 /home/user/settings-engine/settings_engine.py`. It is always invoked with
`/home/user/settings-engine` as the working directory and supports exactly two subcommands.

### `resolve`

```
python3 /home/user/settings-engine/settings_engine.py resolve --slug <slug>
```

Resolves the record identified by `<slug>` and prints the result envelope described below.

### `patch`

```
python3 /home/user/settings-engine/settings_engine.py patch --slug <slug> --layer <layer_key> --file <path>
```

Reads a JSON document from `<path>`, folds it into the stored `doc` of the layer whose `key` is
`<layer_key>` (which must be one of that record's own layers), increases that layer's `revision`
by exactly 1, and then prints the result envelope for `<slug>` computed from the state *after*
the patch. Nothing else in the database may change: no other layer, no other property, no link.

### Resolution rules

The effective document starts as an empty JSON object and is built by folding in the `doc` of
each **active** layer of the record, in ascending `precedence` order. Layers whose `active` is
false are skipped entirely. Folding an override object `O` into an accumulator object `A` is
defined per key `k` of `O`:

1. if `O[k]` is JSON `null`: remove `k` from `A` if it is present; `k` is absent from the result.
2. otherwise, if `O[k]` is a JSON object: let `B` be `A[k]` when `A[k]` exists and is itself a
   JSON object, and the empty object otherwise; `A[k]` becomes the result of folding `O[k]`
   into `B`.
3. otherwise: `A[k]` becomes `O[k]`. Arrays and scalars are never merged or combined
   element-wise; they replace whatever was there.

Consequently the effective document never contains a JSON `null`.

Every removal that rule 1 actually performed (i.e. the key was present) is recorded as a *path*:
the array of key names leading from the root of the document to the removed key. A rule-1 key
that was not present is not recorded. Only the removed key's own path is recorded, never the
paths of its descendants. A recorded removal stays recorded even if a later layer re-creates the
key.

### Patch rules

Folding the patch document `P` into the target layer's stored `doc` uses the same recursive
object merge, with exactly one difference: a JSON `null` in `P` is **stored**. That is, per key
`k` of `P`: `null` sets `doc[k]` to JSON `null`, creating `k` if absent and discarding whatever
value or subtree was there; a JSON object folds recursively into `doc[k]` when `doc[k]` exists
and is an object, and into the empty object otherwise; anything else replaces `doc[k]`.
Keys of the stored document that the patch does not mention keep their previous values.

### Result envelope

Exactly one line on stdout: a JSON object with exactly these five keys.

- `applied_layers`: array of the `key` values of the layers that were folded in, in the order
  they were folded.
- `deleted_paths`: array of the recorded removal paths (each path is an array of strings),
  duplicates removed, sorted lexicographically by segment sequence: compare segments pairwise by
  Unicode code point, and a path that is a proper prefix of another sorts first.
- `document`: the effective document.
- `revision`: the sum of the `revision` values of the layers listed in `applied_layers`.
- `slug`: the resolved record's `slug`.

Serialization is strict: every JSON object in the output — the envelope and every nested object,
at any depth — has its keys ordered ascending by Unicode code point; there is no whitespace
between JSON tokens (in particular no space after `:` or `,`); non-ASCII characters are written
literally as UTF-8 and never as `\uXXXX` escapes; booleans and numbers keep their JSON types; the
line is terminated by a single newline and nothing else is written to stdout.

### Errors and exit codes

- `0`: success.
- `3`: no record with the requested `slug` exists.
- `4`: (`patch` only) the requested layer is not one of that record's layers, including the case
  where no layer with that `key` exists at all.
- `5`: (`patch` only) the patch file cannot be read, does not contain valid JSON, or its
  top-level JSON value is not an object.

If more than one of these applies, the smallest applicable code is used. On every non-zero exit,
stdout must stay empty, a diagnostic must be written to stderr, and the database must be left
completely unchanged.

### Concurrency

Up to 8 `patch` invocations may run simultaneously against the same layer of the same record.
Every one of them must exit `0`, each of them must increase that layer's `revision` by exactly 1
(so the final revision equals the initial revision plus the number of invocations), and no key
written by any of them may be missing or stale in the stored document afterwards.

### Additional constraints

- `resolve` never writes to the database, and two identical invocations produce byte-identical
  stdout.
- Every invocation must reflect the live contents of the database: records, layers, documents,
  `active` flags, `revision` values and chain memberships can be added or changed by other tools
  between two invocations, and the very next invocation must already account for that.
- Do not change the seeded schema, and do not change seeded rows other than through the patch
  behaviour specified above.

