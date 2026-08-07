# Weighted Full-Text Search Ranking over a Gel Knowledge Base (CLI only)

## Background
A small documentation site keeps its articles in a JSON corpus and wants a relevance-ranked search endpoint backed by a local **Gel 7.1** instance. The project directory already contains an empty schema module and the corpus file; the database is empty. Everything must be done with the `gel` CLI, SDL and EdgeQL — no Gel client library is installed and no network access is available.

## Requirements
- Model the corpus in the `default` module as an object type `Article` with the required properties `slug` (identity of an article, no two articles may share one), `title`, `summary`, `body`, `section` (all `str`) and `published` (`bool`).
- Make `Article` searchable by natural-language English terms, where a term found in the `title` is more relevant than the same term found in the `summary`, which in turn is more relevant than the same term found in the `body`.
- Capture the schema in the project's migration history and apply it, so that the CLI reports the database as up to date afterwards.
- Provide `scripts/load.sh`, which loads the corpus into the database and can be run repeatedly without creating duplicates or leaving stale rows behind.
- Provide `scripts/search.sh`, a relevance-ranked, paginated search command over the loaded articles.

## Implementation Hints
- Project path: `/home/user/kb-search` (all commands below are run from this directory).
- The database is a local Gel 7.1 instance; connection details are already provided through the environment. The server is idempotently (re)started by running `gel-start`, which returns once the instance answers queries. Nothing in this task may reach the network.
- Corpus file: `/home/user/kb-search/data/corpus.json` — a JSON array of objects with the keys `slug`, `title`, `summary`, `body`, `section`, `published`. This file is an input and must not be edited, moved, or deleted.
- After `bash scripts/load.sh` exits (status `0`), every corpus record must exist as exactly one `Article` whose property values are copied verbatim from that record, and the database must contain no other `Article` objects. Re-running it must leave the same state.
- Search command: `bash scripts/search.sh QUERY [LIMIT] [OFFSET]`, with `LIMIT` defaulting to `5` and `OFFSET` defaulting to `0`. `QUERY` is arbitrary search text (it may contain spaces or apostrophes, and will never contain the two-character sequence `$$`).
- On success `scripts/search.sh` must exit `0` and print to stdout exactly one JSON object (and nothing else) with the keys `query`, `limit`, `offset`, `total`, `results`:
  - `query`: the `QUERY` argument, echoed verbatim.
  - `limit`, `offset`: the effective limit and offset as integers.
  - `total`: the number of matching articles before pagination.
  - `results`: an array of objects, each having exactly the keys `rank`, `slug`, `title`, `section`, `score`.
- Matching and ranking contract:
  - Only `published` articles may match, and an article only matches if its relevance score is greater than `0`.
  - Relevance must be full-text relevance for the English language, computed with custom category weights so that a hit in `title` counts with weight `1.0`, a hit in `summary` with weight `0.5`, and a hit in `body` with weight `0.1`.
  - Matching articles are ordered by score descending, and articles with an identical score are ordered by `slug` ascending.
  - `results` contains the page of that ordering selected by `OFFSET` and `LIMIT`, in ranking order.
  - `rank` is the 1-based position of the article in the full ordering (so the first item of a page is `OFFSET + 1`).
  - `score` is the relevance score rounded to 4 decimal places.
  - When nothing matches, `total` is `0` and `results` is an empty array (still exit status `0`).
- Argument errors — no `QUERY`, an empty `QUERY`, more than three arguments, a `LIMIT` or `OFFSET` that is not a non-negative decimal integer, or a `LIMIT` below `1` — must make `scripts/search.sh` exit with status `2`, print nothing on stdout, and print the text `usage: search.sh QUERY [LIMIT] [OFFSET]` on stderr.
- Both scripts must work when invoked as `bash scripts/<name>.sh` from the project directory, and must always read the current database state rather than any precomputed answer.

