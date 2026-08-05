# Weighted Collaboration Graph with Gel 7.1

## Background

A research platform tracks how its members collaborate. Every collaboration edge is
*directed* and carries its own metadata: how strong the collaboration is, what role the
target plays for the source, when it was established, and whether it has been mutually
confirmed. The platform is backed by a **Gel 7.1** database that runs locally inside this
container, and the analytics team needs a re-runnable command line tool that both mutates
edges and answers graph questions about them.

Project path: `/home/user/socialgraph`

## Requirements

### 1. Schema contract

The database (branch `main`) must end up with a schema satisfying **all** of the following.
The verifier reads this straight out of the live database's own introspection data.

- Module `default` contains **exactly one** object type, named `Member`.
- `Member` declares **exactly two** properties besides the implicit `id`:
  - `handle` — `std::str`, single, required, and unique across all members;
  - `display_name` — `std::str`, single, required.
  No other property may exist on `Member`; in particular, no aggregate, denormalised,
  cached or computed value may be stored as a property of `Member`.
- `Member` has a link named `connections` whose target is `Member` and whose cardinality
  is many. A connection is directed: it goes from the object that declares it to the
  object it points at.
- The link `connections` itself declares **exactly four** properties of its own (besides
  the implicit `source` and `target`), each of single cardinality:
  - `weight` — `std::int64`
  - `role` — `std::str`
  - `established` — `std::datetime`
  - `confirmed` — `std::bool`
  All per-edge metadata must live there and nowhere else: no second object type, no join
  or side table, and no scalar copies of these four values on `Member`.
- Additional *computed links* on `Member` are allowed; additional object types, aliases,
  additional `Member` properties and additional modules are not.

### 2. Seed dataset

`/home/user/socialgraph/data/seed.json` holds the dataset to import:

```json
{
  "members":     [ { "handle": "<str>", "display_name": "<str>" } ],
  "connections": [ { "from": "<handle>", "to": "<handle>", "weight": <int>,
                     "role": "<str>", "established": "<ISO-8601 UTC timestamp>",
                     "confirmed": <bool> } ]
}
```

Each `connections` entry describes the single directed edge `from` → `to`; an ordered pair
appears at most once. The reverse edge, when it exists, is a separate entry with its own
independent metadata.

### 3. Command line tool

Command: `python3 graph.py <subcommand> [options]`, always invoked with
`/home/user/socialgraph` as the working directory.

Every successful invocation prints **exactly one** JSON document to stdout — nothing else
on stdout — and exits `0`. Each JSON object must contain exactly the keys listed for it,
and every value must reflect the state of the database at the moment of the invocation.

Two definitions used below:
- A directed edge is **confirmed** when its `confirmed` value is exactly `true`; an unset
  value counts as `false`, and the JSON output must always carry a real boolean.
- Members `a` and `b` are **mutually confirmed** when the edges `a` → `b` and `b` → `a`
  both exist and are both confirmed.

**`load --file <path>`** — brings the database in line with the given file: afterwards
every listed member exists with the listed `display_name`, and every listed connection
exists as a directed edge whose `weight`, `role`, `established` and `confirmed` are exactly
the listed values, replacing whatever those four values were before. Members and edges the
file does not mention are left untouched. Output keys: `members_total`,
`connections_total` (the number of entries of each kind in the file), `members_created`,
`connections_created` (how many of those entries did not exist in the database before this
invocation and were created by it) — all integers.

**`connect --from <handle> --to <handle> --weight <int> --role <str>`** — if the directed
edge does not exist it is created with the given `weight` and `role`, `confirmed` false and
`established` set to the current UTC time. If it already exists, only `weight` and `role`
are replaced: its `established` and `confirmed` values must survive unchanged. Output keys:
`from`, `to`, `weight`, `role`, `confirmed` (bool), `created` (bool — whether this
invocation created the edge).

**`confirm --from <a> --to <b>`** — permitted only when both directed edges `a` → `b` and
`b` → `a` exist. It marks both of them confirmed, leaving the `weight`, `role` and
`established` of both untouched. Output keys: `from`, `to`, `changed` (int: how many of
those two directed edges were not already confirmed before this invocation).

**`top --handle <h> --limit <n>`** — a JSON array of at most `n` of `h`'s outgoing edges,
ordered by `weight` descending and, for equal weights, by target `handle` ascending
(codepoint order). Element keys: `handle`, `display_name`, `weight`, `role`, `confirmed`.
An empty array when `h` has no outgoing edges.

**`mutual --handle <h>`** — a JSON array of the handles (plain strings) of every member
mutually confirmed with `h`, in ascending order; `[]` when there are none.

**`suggest --handle <h> --limit <n>`** — second-degree recommendations. A member `c` is a
candidate when there is at least one *bridge* member `m` such that the edge `h` → `m` is
confirmed and the edge `m` → `c` is confirmed, `c` is not `h`, and no edge `h` → `c`
exists. Its score is the sum, over every such bridge `m`, of
`weight(h → m) + weight(m → c)`. Return at most `n` candidates ordered by score descending
and, for equal scores, by `handle` ascending. Element keys: `handle`, `display_name`,
`score` (int), `via` (the array of that candidate's bridge handles, ascending). An empty
array when there are no candidates.

**`stats`** — a JSON object with keys `member_count`, `connection_count` (directed edges),
`confirmed_connection_count`, `mutual_pair_count` (the number of unordered pairs `{a, b}`
that are mutually confirmed) and `members`. `members` is an array with one entry per member
ordered by `handle` ascending, each with keys `handle`, `out_degree`, `in_degree`,
`out_weight_total`, `in_weight_total` (the summed weights of that member's outgoing and
incoming edges, `0` when it has none).

### 4. Failure behaviour

Exit codes, all of which must leave the database byte-for-byte unchanged, print nothing on
stdout, and print a non-empty diagnostic on stderr:

- `2` — unknown subcommand, missing or unparsable option, a `--limit` that is not an
  integer `>= 1`, a `--weight` that is not an integer in `1..100`, `--from` equal to
  `--to`, an unreadable or malformed seed file, or a seed file whose entries are missing
  required keys / carry a weight outside `1..100` / connect a handle to itself.
- `3` — a handle named on the command line does not exist in the database, or a `load`
  file contains a connection whose endpoint is neither listed in that file's `members` nor
  already present in the database. A rejected `load` must apply *none* of its entries.
- `4` — `confirm` was asked for a pair whose two directed edges are not both present.

When more than one of those conditions applies to the same invocation, the lowest of the
applicable exit codes is used.

## Implementation Hints

- Project path: `/home/user/socialgraph`. It already contains `gel.toml`, an empty
  `dbschema/` directory and `data/seed.json`. Put the entrypoint at
  `/home/user/socialgraph/graph.py`.
- Command: `python3 graph.py <subcommand> [options]`, run from the project directory.
- A Gel 7.1 server and the `gel` CLI are installed in this container. Running `gel-start`
  makes sure the server is up and ready to accept queries; it is idempotent, and the very
  first start may take a few minutes. Connection settings for both the CLI and the client
  library are already exported in the environment, and the target branch is `main`.
- Everything you need is already installed: Python 3, the Gel Python client and `pytest`.
  The Gel 7.1 server bundled in this container is the only database you may use — do not
  install or run a different server version, and do not depend on any external service.
- All schema and data changes must be persisted in the running database; the verifier
  inspects the live database and also mutates edges and their metadata directly in it
  between invocations of your tool, so every answer must be derived from the database at
  invocation time.
- Performance budget on the seeded dataset: `load` must finish within 240 seconds and every
  other subcommand within 45 seconds.

