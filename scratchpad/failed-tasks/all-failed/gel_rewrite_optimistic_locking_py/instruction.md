# Optimistic Locking for a Wiki Document Store (Gel 6 + async Python client)

## Background
You maintain the storage layer of a small internal wiki. Many editors save the same page at the same time, and the current "last write wins" behaviour silently destroys edits. The team decided that every write must be a compare-and-set operation against a document version number, that the version number and the modification bookkeeping must be enforced by the database schema itself (so that no client — not even a raw query typed into the shell — can forge or skip a version), and that every accepted change must leave an immutable entry in a revision-history table.

A local Gel 6 server is installed in this environment and the project directory is already registered against it. Nothing else is provided: the data model, the migration, the storage library and its command-line front end are yours to build.

## Requirements

### 1. Data model (module `default`)
Declare and migrate a schema containing exactly these two object types (extra helper types are allowed only if they are not required by the two below):

- `Document` with properties `slug` (`str`, unique across all documents), `title` (`str`), `body` (`str`), `revision` (`int64`), `created_at` (`datetime`), `modified_at` (`datetime`), `title_modified_at` (`datetime`) and `last_editor` (`str`). Inserting a `Document` while specifying only `slug`, `title`, `body` and `last_editor` must succeed.
- `DocumentRevision` with a link `document` pointing to `Document` and properties `revision` (`int64`), `title` (`str`), `body` (`str`), `author` (`str`) and `recorded_at` (`datetime`). Inserting a `DocumentRevision` while specifying only `document`, `revision`, `title`, `body` and `author` must succeed. Two rows with the same `document` and the same `revision` must be impossible: a second attempt must be rejected by the database as a constraint violation.

The following bookkeeping must be produced by the schema, for **every** insert/update reaching the database — including statements issued directly through the Gel shell or a raw client query, and including statements that try to assign these properties themselves:

- a freshly inserted `Document` has `revision = 1`;
- every `update` of a `Document` leaves `revision` exactly one greater than the value it had before that statement, even when the update explicitly assigns some other value to `revision`;
- `created_at` is stamped with the statement time at insert and is never altered by any later update;
- `modified_at` is stamped with the statement time at insert and re-stamped by every update;
- `title_modified_at` is stamped with the statement time at insert; on an update it is re-stamped **only when that update statement explicitly assigns `title`** (even if the assigned value is identical to the stored one) and otherwise keeps its previous value.

### 2. Storage library `docstore.py`
A module exposing this exact public surface (all coroutine functions take the client as the only positional argument, everything else keyword-only):

```python
class DocStoreError(Exception): ...
class DocumentNotFound(DocStoreError): ...     # attribute: slug
class SlugConflict(DocStoreError): ...          # attribute: slug
class StaleRevision(DocStoreError): ...         # attributes: slug, expected_revision, actual_revision

async def create_document(client, *, slug: str, title: str, body: str, author: str) -> dict
async def get_document(client, *, slug: str) -> dict
async def update_document(client, *, slug: str, expected_revision: int, author: str,
                         title: str | None = None, body: str | None = None) -> dict
async def append_line(client, *, slug: str, line: str, author: str, max_attempts: int = 16) -> dict
async def get_history(client, *, slug: str) -> list[dict]
```

`client` is an async Gel client created by the caller. Behaviour:

- `create_document` stores a new document authored by `author` and returns it. If the slug already exists it raises `SlugConflict` and leaves the stored data and the history untouched.
- `get_document` returns the stored document, or raises `DocumentNotFound`.
- `update_document` is a compare-and-set write: it applies the supplied `title` and/or `body` only if the document's current `revision` equals `expected_revision`. On a mismatch it raises `StaleRevision` carrying the slug, the `expected_revision` it was given and the revision actually stored at that moment, and it must not modify the document or the history. An unknown slug raises `DocumentNotFound`. A call that supplies neither `title` nor `body` raises `ValueError` without touching the database contents.
- `append_line` appends a new line to the document body — the new body is the old body, a single `"\n"`, then `line` — using the same compare-and-set discipline, re-reading and retrying when it loses a race, for at most `max_attempts` attempts before raising `StaleRevision`. Concurrent `append_line` coroutines on the same document must all be accepted: with N concurrent calls the document's `revision` must grow by exactly N, no appended line may be lost or duplicated, and exactly N history entries must be added.
- Every accepted change (the initial creation included) records exactly one history entry holding the resulting `revision`, the resulting `title` and `body`, and the `author` of that change. A rejected or failed change records nothing.
- `get_history` returns the document's history entries ordered by ascending `revision`, or raises `DocumentNotFound` for an unknown slug.

Return shapes are fixed. A document is a `dict` whose keys are exactly `slug`, `title`, `body`, `revision`, `last_editor`, `created_at`, `modified_at`, `title_modified_at`; `revision` is an `int`, the three timestamps are timezone-aware `datetime.datetime` values, the rest are `str`. `last_editor` is the author of the most recent accepted change. A history entry is a `dict` whose keys are exactly `revision`, `title`, `body`, `author`, `recorded_at`, with the same value types.

### 3. Command line front end `wikicli.py`
A script invoked as `python3 wikicli.py <subcommand> [options]` that writes exactly one JSON value to stdout and nothing else, then exits. Timestamps are rendered as ISO-8601 strings in the JSON output. Subcommands:

- `create --slug S --title T --body B --author A` — prints the created document object.
- `show --slug S` — prints the stored document object.
- `update --slug S --expected-revision N --author A [--title T] [--body B]` — prints the updated document object.
- `history --slug S` — prints a JSON array of the document's history entries, ordered by ascending revision.
- `race --slug S --count N --author A` — runs `N` concurrent line appends against the document, where the append performed by worker `i` (1-based, `i` from 1 to `N`) uses the line `A#i` (the value of `--author`, then `#`, then `i`). All `N` appends must be accepted. Prints an object whose keys are exactly `slug`, `requested`, `accepted`, `final_revision`, `history_length`, where `requested` is `N`, `accepted` is the number of accepted appends, `final_revision` is the document's revision afterwards and `history_length` is the number of history entries afterwards.

Failures are reported as a JSON object on stdout plus a non-zero exit status, and must not be accompanied by a traceback on stdout:

- a stale expected revision prints `{"error": "stale_revision", "slug": <slug>, "expected_revision": <int>, "actual_revision": <int>}` and exits with status 3;
- an unknown slug prints `{"error": "document_not_found", "slug": <slug>}` and exits with status 4;
- a duplicate slug on `create` prints `{"error": "slug_conflict", "slug": <slug>}` and exits with status 5.

Successful subcommands exit with status 0.

## Implementation Hints
- Project path: `/home/user/wikiapp` (already contains `gel.toml` and `dbschema/default.gel`, and is registered against the local instance; connection settings are preconfigured in the environment).
- Run `/usr/local/bin/start-gel.sh` to make sure the local Gel server is up; it is idempotent and returns only once the server accepts queries. The verification harness starts the server the same way.
- Files you must create: `/home/user/wikiapp/docstore.py` and `/home/user/wikiapp/wikicli.py`. `wikicli.py` is always executed with `/home/user/wikiapp` as the working directory.
- The schema must be delivered through the project's migration system: `dbschema/default.gel` holds the declarative schema, `dbschema/migrations/` holds the generated migration(s), and after your work `gel migration status` must report that the database is up to date with no pending changes.
- Do not seed any documents; the harness creates its own documents through your code.
- Concurrency stays modest (at most a couple of dozen simultaneous operations); no performance tuning is expected, but lost updates, duplicated history rows and gaps in the revision sequence are failures.

