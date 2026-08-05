# Ranked Full-Text Search over a Gel Knowledge Base

## Background

The Orbital Ledger platform team keeps its engineering handbook in **Gel 6.11** (the graph-relational
database formerly known as EdgeDB). A local Gel server, the `gel` CLI and a Python 3 environment with
the Gel client are already installed in this container, and a bare project skeleton together with the
article corpus is waiting for you at `/home/user/kbsearch`.

Right now nothing works: the schema is empty, the database holds no data, and there is no search code.
Turn the skeleton into a working ranked knowledge-base search: model the articles, migrate the
database, load the corpus, and expose an asynchronous Python search API plus a thin command line
front end on top of it.

## Requirements

### 1. Schema and migration

Declare the handbook model in `dbschema/default.gel` and move the database to that model through the
project's migration history. `gel migration status` must report that the database is up to date, and
the generated migration file(s) must be present in the project.

The model must contain an object type `default::Article` with exactly these properties:

- `slug` — required `str`, unique across all articles (a second insert reusing an existing slug must be
  rejected by the database).
- `title` — required `str`.
- `summary` — required `str`.
- `body` — required `str`.
- `status` — required, and typed by a custom enum scalar type `default::ArticleStatus` whose values are
  `draft`, `published` and `archived`, declared in exactly that order.
- `tags` — optional `multi` `str`.

`title`, `summary` and `body` must all be searchable, and the searchable definition must make `title`
more relevant than `summary`, and `summary` more relevant than `body`.

### 2. Corpus loader

`/home/user/kbsearch/seed.py`, executed as `python3 seed.py` from the project directory, must load every
record of `/home/user/kbsearch/seed_data.json` into the database. It has to be re-runnable: after any
number of runs the database must hold exactly one `Article` per `slug`, with the field values taken
from the file (`tags` is a set, its order in the file is not significant). Never edit `seed_data.json`.

### 3. Search service

`/home/user/kbsearch/search_service.py` must expose

```python
async def search_articles(
    query: str,
    *,
    status: str | None = None,
    tag: str | None = None,
    limit: int = 10,
    offset: int = 0,
) -> dict
```

It has to be usable on its own (`asyncio.run(search_articles("some words"))`) and must return exactly
this payload — no extra keys at either level:

```json
{
  "query": "<the query string, exactly as received>",
  "total": "<how many articles match the query under the given filters, ignoring limit and offset>",
  "limit": "<the limit that was applied>",
  "offset": "<the offset that was applied>",
  "results": [
    {
      "rank": "<1-based position in the full ordered match list; the first object of a page starting at offset N has rank N+1>",
      "slug": "<the article slug>",
      "title": "<the article title>",
      "status": "<draft | published | archived>",
      "tags": "<the article's tags as a list of strings, sorted ascending>",
      "score": "<the relevance score, a number strictly greater than 0>",
      "highlight": "<the article title with matched query words wrapped, see below>"
    }
  ]
}
```

Behaviour that must hold:

- **Matching.** The query is split on whitespace into terms. An article matches when at least one term
  matches text in its `title`, `summary` or `body`. Matching is case-insensitive and English language
  aware, so a term also matches morphological variants of the same word (a search for `policies` must
  find an article whose text only contains `policy`). Articles that do not match must not appear, and
  every returned object must carry a `score` greater than 0.
- **Ranking.** Results are ordered by `score` descending. The same term found in `title` must produce a
  strictly higher score than when it is found only in `summary`, which in turn must be strictly higher
  than when it is found only in `body`; and an article matching more of the query terms in a given
  field must score strictly higher than an article matching fewer of them in that same field.
- **Deterministic tie-break.** Articles with equal scores must be ordered by `slug` ascending, so
  repeated identical calls always return the same order.
- **Filters.** `status`, when given, restricts results to articles in that status. `tag`, when given,
  restricts results to articles carrying that exact tag; an unknown tag simply yields no matches. Both
  filters must be applied together with the search, and `total` must reflect the filtered match count.
- **Pagination.** `offset` skips that many leading matches, `limit` caps how many result objects are
  returned, and `total` never depends on either. A `limit` of 0 and an `offset` beyond the last match
  both yield an empty `results` list together with the correct `total`.
- **Empty query.** A query that is empty or only whitespace matches nothing: `total` 0 and an empty
  `results` list, without raising.
- **Rejected arguments.** `limit` and `offset` must be non-negative integers and `status`, when given,
  must be one of the three status values; anything else must raise `ValueError`.
- **Highlighting.** `highlight` is the article `title` in which every case-insensitive whole-word
  occurrence of a query term is wrapped in `<b>` and `</b>`, preserving the original characters of the
  title. An occurrence counts as whole-word when it is neither directly preceded nor directly followed
  by an ASCII letter or digit, and only literal occurrences are wrapped (morphological variants are
  not). Query terms for highlighting are the whitespace-separated pieces of the query with leading and
  trailing non-alphanumeric characters stripped. If nothing in the title matches, `highlight` is the
  unchanged title.
- **Live data.** Results must reflect the database contents at call time: an article inserted or
  deleted by another process must be reflected on the very next call, with no rebuild step.

### 4. Command line front end

`/home/user/kbsearch/search_cli.py`, executed from the project directory as

```
python3 search_cli.py --query <text> [--status <status>] [--tag <tag>] [--limit <n>] [--offset <n>]
```

must print the payload described above as a single JSON object on stdout and exit 0. When an argument
is invalid — an unknown status, a negative or non-integer `limit`/`offset`, or a missing `--query` — it
must print nothing on stdout, write a message to stderr, and exit with status 2. `--limit` defaults to
10 and `--offset` defaults to 0.

## Implementation Hints

- Project path: `/home/user/kbsearch` (it already contains `gel.toml`, an empty `dbschema/default.gel`
  and `seed_data.json`).
- The local Gel 6.11 server is not running when you start. Run `start-gel.sh` to bring it up; it is
  idempotent and only returns once the server answers queries. Do not run any other database server.
- `GEL_DSN` and `GEL_CLIENT_TLS_SECURITY` are already exported, so the `gel` CLI and the Gel Python
  client connect to the local instance without any extra arguments or credentials.
- The default `python3` already provides the Gel Python client and `pytest`. Everything must work fully
  offline: no hosted service, no cloud instance, no API key, and no embedding or AI provider.
- Schema changes must go through the migration history — a database that has drifted away from
  `dbschema/` counts as a failure.
- Keep the whole solution inside `/home/user/kbsearch`; `seed.py`, `search_service.py` and
  `search_cli.py` must be importable/runnable with that directory as the working directory.

