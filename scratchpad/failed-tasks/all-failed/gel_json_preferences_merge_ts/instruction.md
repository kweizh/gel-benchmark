# Deep JSON Preference Merge Service (Gel 6 + TypeScript)

## Background

`/home/user/prefsvc` is a user-preferences service backed by a **local Gel 6 server** (Gel is the database formerly known as EdgeDB) running in this container. Branch `main` already has one migration applied. It defines a deliberately minimal object type `default::PrefUser` with just `email` (exclusive) and `preferences: json`, and the branch is seeded with nine users whose stored preference documents must survive everything you do.

The TypeScript half of the service is an unimplemented stub. Your job is to extend the schema and implement a preference service that applies **JSON merge patches (RFC 7386)** to the stored documents, validates them against a fixed set of namespaces and value types, records every applied change in a versioned history, and stays correct when several patches are applied concurrently.

## Requirements

### 1. Schema

Update `dbschema/default.gel`, then create and apply an additional migration. The nine seeded users and their stored `preferences` documents must still be there afterwards: do not wipe, re-create, dump/restore or re-seed the branch, and do not rename `default::PrefUser`.

`default::PrefUser` must additionally have:

* `version: int64`, required. It is `0` for every already-seeded user. **The database itself** must increase it by exactly 1 on every update of a `PrefUser` object - including updates issued by bare EdgeQL statements outside your TypeScript code, and including statements that explicitly try to set `version` to some other value (a client-supplied `version` must be ignored/overwritten).
* `updated_at: datetime`, required. It must be filled in automatically when an object is inserted and refreshed automatically on every update of the object, again including updates issued outside your TypeScript code.
* `history`: a computed multi link exposing that user's change records.

A new object type `default::PrefChange` must exist with a required link `user` pointing at `default::PrefUser`, required properties `version: int64`, `patch: json`, `previous: json`, `current: json`, and a required `applied_at: datetime` that is filled in automatically on insert. It must also carry an **object-level exclusive constraint over the pair (`user`, `version`)**, so the same version number can never be recorded twice for one user. Inserting a `PrefChange` must work when only `user`, `version`, `patch`, `previous` and `current` are supplied.

### 2. System defaults and namespaces

The service has one fixed system-defaults document. This exact document (key order irrelevant) is the source of truth both for the set of legal top-level namespaces and for the expected value types:

```json
{
  "ui": {
    "theme": "light",
    "density": "comfortable",
    "sidebar": { "visible": true, "width": 280 },
    "pinned": []
  },
  "notifications": {
    "email": { "digest": "daily", "marketing": false },
    "push": { "enabled": false, "quiet_hours": [] },
    "batch_size": 25
  },
  "privacy": { "analytics": true, "share_profile": false },
  "editor": { "tab_size": 4, "soft_wrap": true, "keymap": "default", "rulers": [80, 120] }
}
```

The legal top-level namespaces are therefore exactly `ui`, `notifications`, `privacy` and `editor`.

### 3. Merge-patch semantics

A patch document is applied to a stored document with **exactly** the JSON Merge Patch algorithm of RFC 7386: object values are merged recursively; a `null` value removes that key from the result (and is a no-op if the key is absent); every non-object patch value (including arrays) replaces the target value wholesale - arrays are never merged, appended to or de-duplicated; a patch object applied to a non-object target replaces that target with the merge of the patch into an empty object. Stored documents are always JSON objects, and after a patch they must never contain a `null` value.

### 4. Validation

A patch is validated **before** anything is written:

* Any top-level key of the patch that is not one of the four namespaces is rejected, whatever its value.
* Walk the patch: for every path in the patch that also exists in the system-defaults document, the patch value must either be `null` or have the same JSON type as the default value at that path, where the JSON types are `object`, `array`, `string`, `number` and `boolean`. Anything else is rejected.
* Paths below the top level that do **not** exist in the system-defaults document accept any JSON value, at any depth.

Rejections must be reported through the error taxonomy below and must leave the database completely untouched: no document change, no version change, no history entry.

When more than one problem applies to the same request, report the first applicable one in this order: `MALFORMED_PATCH`, `UNKNOWN_USER`, `UNKNOWN_NAMESPACE`, `TYPE_MISMATCH`, `STALE_VERSION`.

### 5. Versioning, history and concurrency

* A patch whose result is deeply equal to the currently stored document is a **no-op**: nothing is written, the version does not move and no history entry is created - but the request still succeeds.
* Every patch that does change the document bumps the user's `version` by exactly 1 and appends exactly one `PrefChange` row whose `version` is the new version, `previous` is the document as it was before, `current` is the document as it is now, and `patch` is the patch document that was applied (already parsed).
* A patch request may carry an expected version. If it does not match the user's current version the request is rejected as stale and nothing is written. The expected-version check happens before the no-op check, so a stale request is rejected even when it would not have changed the document.
* A patch request that does *not* carry an expected version must never fail because another patch was applied concurrently: after N such concurrent requests against the same user have all reported success, the user's version must have advanced by exactly the number of requests that changed the document, the recorded history versions must be gap-free, each entry's `previous` must be deeply equal to the `current` of the entry one version below it, and no change may have been lost or duplicated.
* A validation-only "dry run" mode must run all of the checks above and report the document that *would* be stored, without touching the database at all.

### 6. Effective preferences

An "effective" document layers the system defaults *under* the user's stored values: recurse only where the default value and the stored value are both JSON objects; otherwise the stored value replaces the default value entirely (so arrays are never merged); keys that exist only in the defaults or only in the stored document are kept as they are.

### 7. Module API

`/home/user/prefsvc/src/prefs.ts` must export exactly these names, and must have **no side effects at import time** (the verifier loads this module in-process through the project's dev runner and calls the pure functions directly, so it must not connect to the database, read stdin or exit on import):

* `SYSTEM_DEFAULTS` - the system-defaults document of section 2.
* `mergePatch(target, patch)` - the pure RFC 7386 operation of section 3; it must not mutate either argument.
* `effectivePreferences(stored)` - the pure layering operation of section 6.
* `PreferenceError` - base error class, extends `Error`, constructible from a single message string, and exposing a readonly `code` string property.
* `MalformedPatchError`, `UnknownUserError`, `UnknownNamespaceError`, `TypeMismatchError`, `StaleVersionError` - all extending `PreferenceError`, all constructible from a single message string, with `code` values `MALFORMED_PATCH`, `UNKNOWN_USER`, `UNKNOWN_NAMESPACE`, `TYPE_MISMATCH` and `STALE_VERSION` respectively.
* `applyPatch`, `readPreferences`, `readHistory` - async functions implementing sections 3 to 6 against the database, rejecting with the error classes above.

You may add further modules under `src/`, but `src/prefs.ts` and `src/cli.ts` must keep their paths and roles.

### 8. Command-line contract

The verifier drives the service through `src/cli.ts` only. From `/home/user/prefsvc`, the exact command is:

```
node_modules/.bin/tsx src/cli.ts
```

It takes no arguments, reads **JSON Lines** from stdin until EOF (blank lines are ignored), and for each request line writes exactly one line of compact JSON to stdout, in the same order. Nothing else may be written to stdout; diagnostics belong on stderr. A request that fails must not abort the process: later request lines must still be answered. The process must exit on its own with status `0` once every request has been answered.

Request objects have `op` (`"read"`, `"patch"` or `"history"`) and `email`. A `"patch"` request additionally carries **exactly one** of `patch` (the patch document as JSON) or `patch_text` (a string that must be parsed as JSON), plus the optional keys `expected_version` (an integer >= 0, or `null` meaning "no check") and `dry_run` (boolean, default `false`). A request whose `op` is missing or unrecognised, whose `email` is missing or not a string, that supplies neither or both of `patch`/`patch_text`, whose `patch_text` does not parse, whose patch document is not a JSON object, or whose `expected_version` is neither `null` nor a non-negative integer, is a `MALFORMED_PATCH` failure.

Response objects for successful requests:

* `read` -> keys exactly `ok` (`true`), `op`, `email`, `version`, `preferences` (the stored document), `effective` (section 6).
* `patch` -> keys exactly `ok` (`true`), `op`, `email`, `version`, `changed` (boolean: did the stored document change, or for a dry run would it change), `preferences` (the document now stored, or for a dry run the document that would be stored). For a dry run `version` is the unchanged current version; otherwise it is the version after the request.
* `history` -> keys exactly `ok` (`true`), `op`, `email`, `entries`, where `entries` is an array of objects with keys exactly `version`, `patch`, `previous`, `current`, ordered by ascending `version`.

Response object for a failed request: keys exactly `ok` (`false`), `op` (the received value, or `null` if there was none), `email` (the received value, or `null` if there was none) and `error`, an object with keys exactly `code` (one of the five codes), `name` (the class name of the corresponding exported error class) and `message` (a non-empty string).

## Implementation Hints

* Project path: `/home/user/prefsvc`. Schema in `dbschema/default.gel`, migrations in `dbschema/migrations/`.
* The local Gel 6 server is installed in this container but is not necessarily running. `bash /usr/local/bin/gel-start.sh` starts it and returns once it accepts queries; it is safe to call when the server is already up. Connection settings for both the `gel` CLI and the client library come from the environment (`GEL_DSN`, `GEL_CLIENT_TLS_SECURITY`), so neither needs extra flags. Branch: `main`.
* There is **no network access**. Everything you need is already installed: a Gel 6.11 server, the `gel` CLI, Node.js 20 and, in `/home/user/prefsvc/node_modules`, `gel@2.2.0` (the TypeScript/JS client), `tsx`, `typescript` and `@types/node`. Do not add, remove or upgrade dependencies.
* `npm run typecheck` (which runs `tsc --noEmit`) must exit 0 when you are done. Leave `package.json` and `tsconfig.json` exactly as they are - in particular the project stays CommonJS (no `"type": "module"`), `strict` stays `true`, and the `typecheck` script keeps running `tsc --noEmit`.
* Both `dbschema/migrations/` and the applied migration log must end up consistent: `gel migration status` must succeed, and every migration file must correspond to an applied migration.
* Emails of the nine seeded users all end in `@example.com`; treat the stored `preferences` of any user you were not asked to change as untouchable.
* Numbers in responses are plain JSON numbers; `version` values are integers.

