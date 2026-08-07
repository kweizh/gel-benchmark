# Gel Schema Audit Tool (Python)

## Background

A **Gel 6** server runs locally inside this container and a Gel project lives at `/home/user/gel-audit`. Its schema (module `default`) has already been migrated to the database. Connection settings for both the `gel` CLI and the Python client are already present in the environment, so no connection arguments or credentials are ever needed. The server is not necessarily running when you start: `/usr/local/bin/gel-start.sh` starts it (or returns immediately if it is already up) and is safe to run repeatedly.

The Gel Python client (package `gel`) is already installed.

Your job is to build a **schema audit / linter tool**: it inspects the schema of the *live* database, emits a machine-readable audit document, and reports policy violations. The tool must be completely schema-agnostic: it will be re-run after the schema has been changed by a new migration, and it must then report the new schema and the new violations. Nothing about the current schema may be baked into the tool.

## Requirements

Implement the Python package `schema_audit` (a stub that raises `NotImplementedError` already exists at `/home/user/gel-audit/schema_audit/`).

### Public API

- `schema_audit.build_audit(client)` — an **async** function (a coroutine function). It receives an already-created asynchronous Gel client and returns the complete audit document as a plain `dict` (JSON-serialisable), with `ignored_rules` equal to `[]`.
- `schema_audit.main(argv)` — a normal (non-async) function. `argv` is the argument list **without** the program name. It performs the requested command and **returns** the process exit code as an `int`; it must never raise `SystemExit` and must never propagate an exception.
- `python3 -m schema_audit <args...>`, executed with the working directory `/home/user/gel-audit`, must terminate with exactly the exit code that `main(<args...>)` returns.

### Commands

- `audit --out <path> [--quiet] [--ignore-rule <rule-id>]...`
  Audits the live schema, writes the audit document as UTF-8 JSON to `<path>` (creating/overwriting it), prints the summary described below on stdout, and exits with the code from the exit-code table. `--ignore-rule` may be repeated; every listed rule is completely skipped (it produces no violations and is not counted anywhere). `--quiet` suppresses **all** stdout output but changes nothing else.
- `rules`
  Prints to stdout a JSON array of every rule the tool implements, each an object with exactly the keys `id` and `severity`, sorted ascending by `id`, and exits `0`.

### Audit document

The document is a single JSON object. Every object described below must contain **exactly** the listed keys — no extras, no omissions.

```json
{
  "audit_version": 1,
  "branch": "<str>",
  "migrations": ["<str>"],
  "object_types": [
    {
      "name": "<str>",
      "abstract": "<bool>",
      "bases": ["<str>"],
      "annotations": [{"name": "<str>", "value": "<str|null>"}],
      "pointers": [
        {
          "name": "<str>",
          "kind": "property|link",
          "target": "<str|null>",
          "required": "<bool>",
          "cardinality": "One|Many",
          "readonly": "<bool>",
          "computed": "<bool>",
          "constraints": ["<str>"],
          "link_properties": [
            {"name": "<str>", "target": "<str|null>", "required": "<bool>", "constraints": ["<str>"]}
          ]
        }
      ],
      "constraints": [{"name": "<str>", "subjectexpr": "<str|null>"}],
      "indexes": [{"expr": "<str>"}],
      "access_policies": [{"name": "<str>", "action": "<str>", "access_kinds": ["<str>"], "expr": "<str|null>"}],
      "triggers": [{"name": "<str>", "timing": "<str>", "scope": "<str>", "kinds": ["<str>"]}]
    }
  ],
  "globals": [{"name": "<str>", "target": "<str|null>", "required": "<bool>", "cardinality": "One|Many", "computed": "<bool>"}],
  "aliases": ["<str>"],
  "functions": [{"name": "<str>", "return_type": "<str|null>", "volatility": "<str>", "param_types": ["<str>"]}],
  "ignored_rules": ["<str>"],
  "violations": [{"rule": "<str>", "severity": "<str>", "target": "<str>", "message": "<str>"}],
  "summary": {"error": "<int>", "warning": "<int>", "info": "<int>", "total": "<int>"}
}
```

Semantics and exact conventions:

- `audit_version` is always the integer `1`. `branch` is the name of the branch that was audited.
- `migrations` lists the names of all migrations applied to that branch, ordered from the oldest (the one without a parent) to the newest by following the parent chain. The history is guaranteed to be linear.
- An object type is **audited** if and only if all of the following hold: it is not a built-in/standard-library type, it is not internal, it was not generated from an alias, and it is not a compound (union/intersection) type. Abstract types **are** audited. All other object types must be absent from `object_types`. The same "not built-in and not internal" filter selects the reported `globals`, `aliases` (reported as a plain list of names) and `functions`.
- All names of schema items that live in a module (object types, globals, aliases, functions, constraints, annotations) are reported **module-qualified**, exactly as the server reports them (e.g. `std::exclusive`). Pointer, link-property, access-policy and trigger names are reported as their short declared names.
- `bases` lists the names of the type's **direct** bases only (nothing is filtered out).
- `pointers` covers every property and link that is visible on the type, including the ones it inherits, with two exceptions: the implicit `id` and `__type__` pointers must never be reported. `kind` distinguishes properties from links. `link_properties` reports the properties that belong to a link itself, excluding the implicit `source` and `target` ones; for a pointer of kind `property` it is always `[]`.
- `computed` is `true` if and only if the pointer (or global) is computed. `required` and `readonly` are `false` whenever the server reports no value for them.
- Enum-valued fields (`cardinality`, `action`, `access_kinds`, `timing`, `scope`, `kinds`, `volatility`) are reported as the plain enum value name as the server reports it (for example `One`, `Many`).
- `param_types` lists the type names of a function's parameters in declaration order.
- Ordering is mandatory and must use plain string comparison: `object_types` by `name`; `pointers`, `link_properties`, `annotations`, `access_policies`, `triggers`, `globals` by `name`; `aliases`, all `constraints` string lists, `access_kinds`, `kinds` and `ignored_rules` by their own value; `indexes` by `expr`; `functions` by `name` and then by `param_types` joined with `,`; type-level `constraints` by `name` and then by `subjectexpr` (a null `subjectexpr` compares as the empty string); `violations` by `rule` and then by `target`.
- `summary` counts the reported violations per severity plus the total. `message` is a non-empty human-readable string; it is not otherwise constrained, but the whole file must be byte-identical when the tool is re-run against an unchanged schema.

### Lint rules

Every rule is evaluated against the audited schema exactly as it is reported in the document. A given `(rule, target)` pair must appear **at most once**.

| rule id | severity | violation condition | `target` |
| --- | --- | --- | --- |
| `type-missing-exclusive` | error | A non-abstract audited type has neither a type-level constraint named `std::exclusive` nor any reported pointer carrying a constraint named `std::exclusive`. | the type name |
| `type-name-not-pascal-case` | warning | The part of an audited type's name after the last `::` does not fully match `^[A-Z][A-Za-z0-9]*$`. | the type name |
| `pointer-name-not-snake-case` | warning | A reported pointer's name does not fully match `^[a-z][a-z0-9_]*$`. | `<type name>.<pointer name>` |
| `multi-link-required` | warning | A reported pointer of kind `link` has cardinality `Many` and is required. | `<type name>.<pointer name>` |
| `link-property-not-required` | error | A reported link property is not required. | `<type name>.<link name>@<link property name>` |
| `policy-without-tenant-id` | error | An audited type has at least one access policy but has no reported pointer named `tenant_id`. | the type name |
| `deprecated-type` | warning | An audited type carries an annotation named `default::deprecated`. | the type name |
| `index-duplicates-exclusive` | info | For an audited type, an index whose expression — after removing every whitespace character — equals either the same-normalised `subjectexpr` of one of that type's type-level `std::exclusive` constraints, or the normalised string `.` + the name of one of that type's reported pointers that carries a `std::exclusive` constraint. | `<type name>:<index expr>` (the raw, un-normalised expression) |
| `global-name-not-snake-case` | warning | The part of a reported global's name after the last `::` does not fully match `^[a-z][a-z0-9_]*$`. | the global name |

### stdout summary

Without `--quiet`, the `audit` command prints exactly these lines (each terminated by a newline, no other output on stdout), where `<pointers>` is the total number of pointer entries across all reported object types:

```
object_types=<count>
pointers=<count>
violations=<total>
error=<count>
warning=<count>
info=<count>
```

followed by one line per reported violation, in document order, formatted as `<severity> <rule> <target>` (single spaces), and finally a line `exit=<exit code>`.

### Exit codes

| condition | exit code |
| --- | --- |
| `rules` succeeded | 0 |
| audit reported no violations | 0 |
| worst reported severity is `info` | 10 |
| worst reported severity is `warning` | 20 |
| worst reported severity is `error` | 30 |
| usage error: no arguments, unknown command, unknown/malformed option, missing `--out`, or an unknown rule id passed to `--ignore-rule` | 64 |
| the audit could not be completed (for example the database is unreachable) | 65 |

For exit codes 64 and 65 nothing may be written to the output path and stdout must stay empty; diagnostics go to stderr.

## Implementation Hints

- Project path: `/home/user/gel-audit`
- Package to implement: `/home/user/gel-audit/schema_audit/` (importable as `schema_audit` with `/home/user/gel-audit` as the working directory).
- Command: `python3 -m schema_audit audit --out <path>` (run from `/home/user/gel-audit`).
- The schema files under `/home/user/gel-audit/dbschema/` and the migration history will be modified and re-applied while your tool is exercised; the tool must report whatever the database contains at the moment it runs, for any schema, without any hardcoded knowledge of type, pointer, global, alias or function names.
- Do not change the existing schema, the migration history or the environment's connection settings.

