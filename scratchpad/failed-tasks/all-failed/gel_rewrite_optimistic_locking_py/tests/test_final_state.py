"""Final state verification for the Gel optimistic-locking wiki document store."""

import asyncio
import datetime
import glob
import importlib
import json
import os
import subprocess
import sys
import time
import uuid

import gel
import pytest

PROJECT_DIR = "/home/user/wikiapp"
SCHEMA_FILE = os.path.join(PROJECT_DIR, "dbschema", "default.gel")
MIGRATIONS_DIR = os.path.join(PROJECT_DIR, "dbschema", "migrations")
DOCSTORE_FILE = os.path.join(PROJECT_DIR, "docstore.py")
CLI_FILE = os.path.join(PROJECT_DIR, "wikicli.py")
START_SCRIPT = "/usr/local/bin/start-gel.sh"

DOCUMENT_KEYS = {
    "slug",
    "title",
    "body",
    "revision",
    "last_editor",
    "created_at",
    "modified_at",
    "title_modified_at",
}
HISTORY_KEYS = {"revision", "title", "body", "author", "recorded_at"}
RACE_KEYS = {"slug", "requested", "accepted", "final_revision", "history_length"}

os.environ.setdefault("GEL_DSN", "gel://admin@127.0.0.1:5656")
os.environ.setdefault("GEL_CLIENT_TLS_SECURITY", "insecure")

INTROSPECT_QUERY = """
select schema::ObjectType {
    name,
    pointers: {
        name,
        kind := .__type__.name,
        target_name := .target.name,
        default,
        rewrites: { kind },
    }
}
filter .name = <str>$name
"""


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def gel_server():
    """Start the local Gel server (idempotent) and wait until it answers."""
    proc = subprocess.run(
        [START_SCRIPT], capture_output=True, text=True, timeout=900
    )
    print("=== start-gel.sh stdout ===")
    print(proc.stdout)
    print("=== start-gel.sh stderr ===")
    print(proc.stderr)
    assert proc.returncode == 0, (
        f"{START_SCRIPT} failed to start the local Gel server "
        f"(exit {proc.returncode}): stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    return True


@pytest.fixture(scope="session")
def docstore(gel_server):
    """Import the executor's storage library."""
    assert os.path.isfile(DOCSTORE_FILE), f"{DOCSTORE_FILE} does not exist."
    if PROJECT_DIR not in sys.path:
        sys.path.insert(0, PROJECT_DIR)
    module = importlib.import_module("docstore")
    for name in (
        "DocStoreError",
        "DocumentNotFound",
        "SlugConflict",
        "StaleRevision",
        "create_document",
        "get_document",
        "update_document",
        "append_line",
        "get_history",
    ):
        assert hasattr(module, name), (
            f"docstore.py does not expose the required public name {name!r}."
        )
    return module


def run_async(coro_func):
    """Run `coro_func(client)` with a fresh async Gel client."""

    async def runner():
        client = gel.create_async_client()
        try:
            return await coro_func(client)
        finally:
            await client.aclose()

    return asyncio.run(runner())


def unique_slug(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def run_cli(*args, timeout=600):
    return subprocess.run(
        ["python3", "wikicli.py", *args],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def cli_json(proc):
    text = proc.stdout.strip()
    assert "Traceback" not in proc.stdout, (
        f"wikicli.py printed a traceback on stdout: {proc.stdout!r}"
    )
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"wikicli.py stdout is not a single JSON value: {proc.stdout!r} "
            f"(stderr={proc.stderr!r}) -> {exc}"
        ) from exc


def assert_document_shape(doc, where):
    assert isinstance(doc, dict), f"{where}: expected a dict, got {type(doc)!r}."
    assert set(doc) == DOCUMENT_KEYS, (
        f"{where}: document keys must be exactly {sorted(DOCUMENT_KEYS)}, "
        f"got {sorted(doc)}."
    )
    assert isinstance(doc["revision"], int), (
        f"{where}: revision must be an int, got {type(doc['revision'])!r}."
    )
    for key in ("slug", "title", "body", "last_editor"):
        assert isinstance(doc[key], str), (
            f"{where}: {key} must be a str, got {type(doc[key])!r}."
        )
    for key in ("created_at", "modified_at", "title_modified_at"):
        value = doc[key]
        assert isinstance(value, datetime.datetime), (
            f"{where}: {key} must be a datetime.datetime, got {type(value)!r}."
        )
        assert value.tzinfo is not None, (
            f"{where}: {key} must be timezone-aware, got {value!r}."
        )


def assert_history_shape(entry, where):
    assert set(entry) == HISTORY_KEYS, (
        f"{where}: history entry keys must be exactly {sorted(HISTORY_KEYS)}, "
        f"got {sorted(entry)}."
    )
    assert isinstance(entry["revision"], int), (
        f"{where}: history revision must be an int, got {type(entry['revision'])!r}."
    )
    for key in ("title", "body", "author"):
        assert isinstance(entry[key], str), (
            f"{where}: history {key} must be a str, got {type(entry[key])!r}."
        )
    assert isinstance(entry["recorded_at"], datetime.datetime), (
        f"{where}: history recorded_at must be a datetime, "
        f"got {type(entry['recorded_at'])!r}."
    )
    assert entry["recorded_at"].tzinfo is not None, (
        f"{where}: history recorded_at must be timezone-aware."
    )


def introspect(type_name):
    async def query(client):
        rows = json.loads(await client.query_json(INTROSPECT_QUERY, name=type_name))
        return rows[0] if rows else None

    result = run_async(query)
    assert result is not None, (
        f"Object type {type_name} does not exist in the database schema."
    )
    return {p["name"]: p for p in result["pointers"]}


# --------------------------------------------------------------------------- #
# 1-2: project layout and migrations
# --------------------------------------------------------------------------- #
def test_project_files_exist():
    for path in (SCHEMA_FILE, DOCSTORE_FILE, CLI_FILE):
        assert os.path.isfile(path), f"Required file {path} does not exist."


def test_migration_files_exist():
    assert os.path.isdir(MIGRATIONS_DIR), (
        f"Migration directory {MIGRATIONS_DIR} does not exist; the schema must be "
        "delivered through the project's migration system."
    )
    migrations = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql")))
    assert migrations, (
        f"No *.edgeql migration file found in {MIGRATIONS_DIR}."
    )


def test_migration_status_in_sync(gel_server):
    proc = subprocess.run(
        ["gel", "migration", "status"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=600,
    )
    combined = (proc.stdout + proc.stderr).lower()
    assert proc.returncode == 0, (
        "`gel migration status` reported a problem "
        f"(exit {proc.returncode}): stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert "up to date" in combined, (
        "`gel migration status` did not report the database as up to date: "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )


# --------------------------------------------------------------------------- #
# 3-4: schema shape and rewrite rules
# --------------------------------------------------------------------------- #
def test_document_schema_shape(gel_server):
    pointers = introspect("default::Document")
    expected = {
        "slug": "std::str",
        "title": "std::str",
        "body": "std::str",
        "last_editor": "std::str",
        "revision": "std::int64",
        "created_at": "std::datetime",
        "modified_at": "std::datetime",
        "title_modified_at": "std::datetime",
    }
    for name, target in expected.items():
        assert name in pointers, (
            f"default::Document has no pointer named {name!r}; "
            f"found {sorted(pointers)}."
        )
        assert pointers[name]["kind"] == "schema::Property", (
            f"default::Document.{name} must be a property, "
            f"got {pointers[name]['kind']}."
        )
        assert pointers[name]["target_name"] == target, (
            f"default::Document.{name} must target {target}, "
            f"got {pointers[name]['target_name']}."
        )


def test_document_revision_schema_shape(gel_server):
    pointers = introspect("default::DocumentRevision")
    assert "document" in pointers, (
        f"default::DocumentRevision has no `document` pointer; found {sorted(pointers)}."
    )
    assert pointers["document"]["kind"] == "schema::Link", (
        "default::DocumentRevision.document must be a link, "
        f"got {pointers['document']['kind']}."
    )
    assert pointers["document"]["target_name"] == "default::Document", (
        "default::DocumentRevision.document must target default::Document, "
        f"got {pointers['document']['target_name']}."
    )
    expected = {
        "revision": "std::int64",
        "title": "std::str",
        "body": "std::str",
        "author": "std::str",
        "recorded_at": "std::datetime",
    }
    for name, target in expected.items():
        assert name in pointers, (
            f"default::DocumentRevision has no property {name!r}; "
            f"found {sorted(pointers)}."
        )
        assert pointers[name]["target_name"] == target, (
            f"default::DocumentRevision.{name} must target {target}, "
            f"got {pointers[name]['target_name']}."
        )


def test_document_rewrites_declared(gel_server):
    pointers = introspect("default::Document")

    def kinds(name):
        return {rw["kind"] for rw in pointers[name]["rewrites"]}

    assert "Update" in kinds("revision"), (
        "default::Document.revision must carry an update rewrite rule, "
        f"found rewrites {sorted(kinds('revision'))}."
    )
    assert {"Insert", "Update"} <= kinds("modified_at"), (
        "default::Document.modified_at must carry insert and update rewrite rules, "
        f"found {sorted(kinds('modified_at'))}."
    )
    assert {"Insert", "Update"} <= kinds("title_modified_at"), (
        "default::Document.title_modified_at must carry insert and update rewrite "
        f"rules, found {sorted(kinds('title_modified_at'))}."
    )
    created_kinds = kinds("created_at")
    assert "Update" not in created_kinds, (
        "default::Document.created_at must not be rewritten on update; "
        f"found {sorted(created_kinds)}."
    )
    assert "Insert" in created_kinds or pointers["created_at"]["default"], (
        "default::Document.created_at must be stamped at insert time (insert "
        "rewrite or insert default), found neither."
    )


# --------------------------------------------------------------------------- #
# 5: database level uniqueness guarantees
# --------------------------------------------------------------------------- #
def test_history_revision_uniqueness_enforced(docstore):
    slug = unique_slug("uniq")

    async def scenario(client):
        await docstore.create_document(
            client, slug=slug, title="Uniq", body="body", author="alice"
        )
        with pytest.raises(gel.ConstraintViolationError):
            await client.execute(
                """
                insert DocumentRevision {
                    document := assert_exists((
                        select Document filter .slug = <str>$slug
                    )),
                    revision := 1,
                    title := "duplicate",
                    body := "duplicate",
                    author := "duplicate",
                }
                """,
                slug=slug,
            )
        rows = await client.query(
            "select DocumentRevision filter .document.slug = <str>$slug",
            slug=slug,
        )
        return len(rows)

    count = run_async(scenario)
    assert count == 1, (
        "A duplicate (document, revision) history row must be rejected, leaving "
        f"exactly one entry, found {count}."
    )


def test_slug_uniqueness_enforced_by_database(docstore):
    slug = unique_slug("dupslug")

    async def scenario(client):
        await docstore.create_document(
            client, slug=slug, title="Dup", body="body", author="alice"
        )
        with pytest.raises(gel.ConstraintViolationError):
            await client.execute(
                """
                insert Document {
                    slug := <str>$slug,
                    title := "other",
                    body := "other",
                    last_editor := "other",
                }
                """,
                slug=slug,
            )
        return await client.query_required_single(
            "select count(Document filter .slug = <str>$slug)", slug=slug
        )

    count = run_async(scenario)
    assert count == 1, (
        f"Exactly one Document must exist for slug {slug!r}, found {count}."
    )


# --------------------------------------------------------------------------- #
# 6-8: happy path, compare-and-set and stale rejection
# --------------------------------------------------------------------------- #
def test_create_document_happy_path(docstore):
    slug = unique_slug("create")

    async def scenario(client):
        created = await docstore.create_document(
            client, slug=slug, title="Alpha", body="first line", author="alice"
        )
        history = await docstore.get_history(client, slug=slug)
        fetched = await docstore.get_document(client, slug=slug)
        return created, history, fetched

    created, history, fetched = run_async(scenario)

    assert_document_shape(created, "create_document result")
    assert created["revision"] == 1, (
        f"A freshly created document must have revision 1, got {created['revision']}."
    )
    assert created["title"] == "Alpha", f"Unexpected title: {created['title']!r}."
    assert created["body"] == "first line", f"Unexpected body: {created['body']!r}."
    assert created["slug"] == slug, f"Unexpected slug: {created['slug']!r}."
    assert created["last_editor"] == "alice", (
        f"last_editor must be the creating author, got {created['last_editor']!r}."
    )
    assert fetched == created, (
        "get_document must return the same document that create_document returned: "
        f"{fetched!r} != {created!r}."
    )

    assert len(history) == 1, (
        f"Creating a document must record exactly one history entry, got {len(history)}."
    )
    assert_history_shape(history[0], "history[0]")
    assert history[0]["revision"] == 1, (
        f"First history entry must have revision 1, got {history[0]['revision']}."
    )
    assert history[0]["title"] == "Alpha", "First history entry has a wrong title."
    assert history[0]["body"] == "first line", "First history entry has a wrong body."
    assert history[0]["author"] == "alice", "First history entry has a wrong author."


def test_compare_and_set_update(docstore):
    slug = unique_slug("cas")

    async def scenario(client):
        created = await docstore.create_document(
            client, slug=slug, title="Alpha", body="first line", author="alice"
        )
        updated = await docstore.update_document(
            client,
            slug=slug,
            expected_revision=1,
            author="bob",
            body="second body",
        )
        history = await docstore.get_history(client, slug=slug)
        return created, updated, history

    created, updated, history = run_async(scenario)

    assert_document_shape(updated, "update_document result")
    assert updated["revision"] == 2, (
        f"An accepted update must bump revision to 2, got {updated['revision']}."
    )
    assert updated["body"] == "second body", f"Unexpected body: {updated['body']!r}."
    assert updated["title"] == "Alpha", (
        f"An update that only sets body must keep the title, got {updated['title']!r}."
    )
    assert updated["last_editor"] == "bob", (
        f"last_editor must be the updating author, got {updated['last_editor']!r}."
    )
    assert updated["created_at"] == created["created_at"], (
        "created_at must never change on update: "
        f"{updated['created_at']!r} != {created['created_at']!r}."
    )
    assert updated["modified_at"] >= created["modified_at"], (
        "modified_at must be re-stamped on update: "
        f"{updated['modified_at']!r} < {created['modified_at']!r}."
    )

    assert [entry["revision"] for entry in history] == [1, 2], (
        "History must contain revisions [1, 2] in ascending order, got "
        f"{[entry['revision'] for entry in history]}."
    )
    assert history[1]["body"] == "second body", (
        f"Second history entry has a wrong body: {history[1]['body']!r}."
    )
    assert history[1]["author"] == "bob", (
        f"Second history entry has a wrong author: {history[1]['author']!r}."
    )


def test_stale_revision_rejected(docstore):
    slug = unique_slug("stale")

    async def scenario(client):
        await docstore.create_document(
            client, slug=slug, title="Alpha", body="first line", author="alice"
        )
        await docstore.update_document(
            client, slug=slug, expected_revision=1, author="bob", body="second body"
        )
        error = None
        try:
            await docstore.update_document(
                client,
                slug=slug,
                expected_revision=1,
                author="carol",
                body="clobbered",
            )
        except docstore.StaleRevision as exc:
            error = exc
        document = await docstore.get_document(client, slug=slug)
        history = await docstore.get_history(client, slug=slug)
        return error, document, history

    error, document, history = run_async(scenario)

    assert error is not None, (
        "Updating with a stale expected_revision must raise docstore.StaleRevision."
    )
    assert error.slug == slug, f"StaleRevision.slug is wrong: {error.slug!r}."
    assert error.expected_revision == 1, (
        f"StaleRevision.expected_revision must be 1, got {error.expected_revision!r}."
    )
    assert error.actual_revision == 2, (
        f"StaleRevision.actual_revision must be 2, got {error.actual_revision!r}."
    )
    assert isinstance(error, docstore.DocStoreError), (
        "StaleRevision must derive from DocStoreError."
    )
    assert document["revision"] == 2, (
        f"A rejected update must not change the revision, got {document['revision']}."
    )
    assert document["body"] == "second body", (
        f"A rejected update must not change the body, got {document['body']!r}."
    )
    assert len(history) == 2, (
        f"A rejected update must not record history, found {len(history)} entries."
    )


# --------------------------------------------------------------------------- #
# 9-11: negative cases
# --------------------------------------------------------------------------- #
def test_unknown_slug_raises_document_not_found(docstore):
    async def scenario(client):
        errors = {}
        for name, coro in (
            ("get_document", docstore.get_document(client, slug="no-such-page")),
            (
                "update_document",
                docstore.update_document(
                    client,
                    slug="no-such-page",
                    expected_revision=1,
                    author="alice",
                    body="x",
                ),
            ),
            ("get_history", docstore.get_history(client, slug="no-such-page")),
        ):
            try:
                await coro
                errors[name] = None
            except docstore.DocumentNotFound as exc:
                errors[name] = exc
        return errors

    errors = run_async(scenario)
    for name, error in errors.items():
        assert error is not None, (
            f"{name} on an unknown slug must raise docstore.DocumentNotFound."
        )
        assert error.slug == "no-such-page", (
            f"{name}: DocumentNotFound.slug must be 'no-such-page', got {error.slug!r}."
        )


def test_empty_update_raises_value_error(docstore):
    slug = unique_slug("empty")

    async def scenario(client):
        await docstore.create_document(
            client, slug=slug, title="Alpha", body="first line", author="alice"
        )
        raised = None
        try:
            await docstore.update_document(
                client, slug=slug, expected_revision=1, author="carol"
            )
        except ValueError as exc:
            raised = exc
        document = await docstore.get_document(client, slug=slug)
        history = await docstore.get_history(client, slug=slug)
        return raised, document, history

    raised, document, history = run_async(scenario)
    assert raised is not None, (
        "update_document without title and body must raise ValueError."
    )
    assert document["revision"] == 1, (
        f"A rejected empty update must not bump the revision, got {document['revision']}."
    )
    assert len(history) == 1, (
        f"A rejected empty update must not record history, found {len(history)}."
    )


def test_slug_conflict_leaves_state_untouched(docstore):
    slug = unique_slug("conflict")

    async def scenario(client):
        await docstore.create_document(
            client, slug=slug, title="Alpha", body="first line", author="alice"
        )
        raised = None
        try:
            await docstore.create_document(
                client, slug=slug, title="Other", body="other", author="mallory"
            )
        except docstore.SlugConflict as exc:
            raised = exc
        document = await docstore.get_document(client, slug=slug)
        history = await docstore.get_history(client, slug=slug)
        return raised, document, history

    raised, document, history = run_async(scenario)
    assert raised is not None, (
        "Creating a document with an existing slug must raise docstore.SlugConflict."
    )
    assert raised.slug == slug, f"SlugConflict.slug is wrong: {raised.slug!r}."
    assert document["revision"] == 1, (
        f"A rejected create must not bump the revision, got {document['revision']}."
    )
    assert document["title"] == "Alpha", (
        f"A rejected create must not modify the document, got {document['title']!r}."
    )
    assert len(history) == 1, (
        f"A rejected create must not record history, found {len(history)}."
    )


# --------------------------------------------------------------------------- #
# 12-14: schema-enforced bookkeeping (raw client, bypassing docstore)
# --------------------------------------------------------------------------- #
def test_revision_is_enforced_by_the_database(docstore):
    slug = unique_slug("rewrite")

    async def scenario(client):
        created = await docstore.create_document(
            client, slug=slug, title="Alpha", body="first line", author="alice"
        )
        raw = await client.query_required_single(
            """
            select (
                update Document
                filter .slug = <str>$slug
                set { title := "Alpha", revision := 999 }
            ) { revision, created_at, modified_at, title_modified_at }
            """,
            slug=slug,
        )
        return created, raw

    created, raw = run_async(scenario)
    assert raw.revision == created["revision"] + 1, (
        "A raw update that assigns revision := 999 must still leave revision one "
        f"greater than before ({created['revision'] + 1}), got {raw.revision}."
    )
    assert raw.created_at == created["created_at"], (
        f"created_at must not change on update: {raw.created_at!r}."
    )
    assert raw.modified_at > created["modified_at"], (
        f"modified_at must be re-stamped on update: {raw.modified_at!r}."
    )
    assert raw.title_modified_at > created["title_modified_at"], (
        "title_modified_at must be re-stamped when the update specifies title: "
        f"{raw.title_modified_at!r}."
    )


def test_title_modified_at_tracks_specified_title(docstore):
    slug = unique_slug("specified")

    async def scenario(client):
        created = await docstore.create_document(
            client, slug=slug, title="Alpha", body="first line", author="alice"
        )
        body_only = await client.query_required_single(
            """
            select (
                update Document
                filter .slug = <str>$slug
                set { body := "raw body" }
            ) { revision, modified_at, title_modified_at }
            """,
            slug=slug,
        )
        same_title = await client.query_required_single(
            """
            select (
                update Document
                filter .slug = <str>$slug
                set { title := "Alpha" }
            ) { revision, modified_at, title_modified_at }
            """,
            slug=slug,
        )
        return created, body_only, same_title

    created, body_only, same_title = run_async(scenario)

    assert body_only.revision == 2, (
        f"A raw body-only update must bump revision to 2, got {body_only.revision}."
    )
    assert body_only.title_modified_at == created["title_modified_at"], (
        "title_modified_at must stay unchanged when the update does not specify "
        f"title: {body_only.title_modified_at!r} != {created['title_modified_at']!r}."
    )
    assert body_only.modified_at > created["modified_at"], (
        f"modified_at must be re-stamped on any update: {body_only.modified_at!r}."
    )
    assert same_title.revision == 3, (
        f"A second raw update must bump revision to 3, got {same_title.revision}."
    )
    assert same_title.title_modified_at > body_only.title_modified_at, (
        "title_modified_at must be re-stamped when the update specifies title, even "
        f"with an unchanged value: {same_title.title_modified_at!r}."
    )


def test_raw_insert_with_minimal_properties(gel_server):
    slug = unique_slug("rawinsert")

    async def scenario(client):
        return await client.query_required_single(
            """
            select (
                insert Document {
                    slug := <str>$slug,
                    title := "Raw",
                    body := "raw body",
                    last_editor := "raw",
                }
            ) { revision, created_at, modified_at, title_modified_at }
            """,
            slug=slug,
        )

    inserted = run_async(scenario)
    assert inserted.revision == 1, (
        f"A raw insert must yield revision 1, got {inserted.revision}."
    )
    for name in ("created_at", "modified_at", "title_modified_at"):
        value = getattr(inserted, name)
        assert isinstance(value, datetime.datetime), (
            f"{name} must be stamped at insert time, got {value!r}."
        )


# --------------------------------------------------------------------------- #
# 15: concurrency invariants
# --------------------------------------------------------------------------- #
def test_concurrent_appends_converge(docstore):
    slug = unique_slug("race")
    workers = 12
    lines = [f"w{i}" for i in range(1, workers + 1)]

    async def scenario(client):
        await docstore.create_document(
            client, slug=slug, title="Race", body="base", author="alice"
        )
        results = await asyncio.gather(
            *(
                docstore.append_line(
                    client, slug=slug, line=line, author="racer", max_attempts=50
                )
                for line in lines
            ),
            return_exceptions=True,
        )
        document = await docstore.get_document(client, slug=slug)
        history = await docstore.get_history(client, slug=slug)
        return results, document, history

    started = time.time()
    results, document, history = run_async(scenario)
    elapsed = time.time() - started
    assert elapsed < 180, (
        f"{workers} concurrent append_line calls took {elapsed:.1f}s, which suggests "
        "a deadlock or livelock."
    )

    failures = [r for r in results if isinstance(r, BaseException)]
    assert not failures, (
        f"All {workers} concurrent append_line calls must be accepted, but "
        f"{len(failures)} raised: {failures!r}"
    )

    assert document["revision"] == workers + 1, (
        f"After {workers} accepted appends the revision must be {workers + 1}, "
        f"got {document['revision']}."
    )
    body_lines = document["body"].split("\n")
    assert body_lines[0] == "base", (
        f"The original body line must survive, got {body_lines[:1]!r}."
    )
    assert sorted(body_lines[1:]) == sorted(lines), (
        "The body must contain every appended line exactly once, got "
        f"{body_lines[1:]!r}."
    )
    assert [entry["revision"] for entry in history] == list(range(1, workers + 2)), (
        "History revisions must be a gap-free ascending sequence "
        f"1..{workers + 1}, got {[e['revision'] for e in history]}."
    )
    assert all(entry["author"] == "racer" for entry in history[1:]), (
        "Every history entry recorded by append_line must carry the author 'racer': "
        f"{[e['author'] for e in history[1:]]}."
    )
    assert history[-1]["body"] == document["body"], (
        "The newest history entry must hold the final body: "
        f"{history[-1]['body']!r} != {document['body']!r}."
    )


# --------------------------------------------------------------------------- #
# 16-19: command line front end
# --------------------------------------------------------------------------- #
def test_cli_create_update_show_history(gel_server):
    slug = unique_slug("cli")

    created_proc = run_cli(
        "create",
        "--slug",
        slug,
        "--title",
        "CLI page",
        "--body",
        "line one",
        "--author",
        "cli",
    )
    assert created_proc.returncode == 0, (
        f"`wikicli.py create` failed: stdout={created_proc.stdout!r} "
        f"stderr={created_proc.stderr!r}"
    )
    created = cli_json(created_proc)
    assert set(created) == DOCUMENT_KEYS, (
        f"CLI document keys must be exactly {sorted(DOCUMENT_KEYS)}, "
        f"got {sorted(created)}."
    )
    assert created["revision"] == 1, (
        f"CLI create must report revision 1, got {created['revision']}."
    )
    assert created["title"] == "CLI page", f"Unexpected title {created['title']!r}."
    assert created["body"] == "line one", f"Unexpected body {created['body']!r}."
    assert created["last_editor"] == "cli", (
        f"Unexpected last_editor {created['last_editor']!r}."
    )
    for key in ("created_at", "modified_at", "title_modified_at"):
        value = created[key]
        assert isinstance(value, str), (
            f"CLI {key} must be an ISO-8601 string, got {value!r}."
        )
        datetime.datetime.fromisoformat(value)

    updated_proc = run_cli(
        "update",
        "--slug",
        slug,
        "--expected-revision",
        "1",
        "--author",
        "cli2",
        "--body",
        "line two",
    )
    assert updated_proc.returncode == 0, (
        f"`wikicli.py update` failed: stdout={updated_proc.stdout!r} "
        f"stderr={updated_proc.stderr!r}"
    )
    updated = cli_json(updated_proc)
    assert updated["revision"] == 2, (
        f"CLI update must report revision 2, got {updated['revision']}."
    )
    assert updated["last_editor"] == "cli2", (
        f"CLI update must record the author, got {updated['last_editor']!r}."
    )

    show_proc = run_cli("show", "--slug", slug)
    assert show_proc.returncode == 0, (
        f"`wikicli.py show` failed: stdout={show_proc.stdout!r} "
        f"stderr={show_proc.stderr!r}"
    )
    shown = cli_json(show_proc)
    assert shown["revision"] == 2, (
        f"CLI show must report revision 2, got {shown['revision']}."
    )
    assert shown["body"] == "line two", (
        f"CLI show must report the updated body, got {shown['body']!r}."
    )

    history_proc = run_cli("history", "--slug", slug)
    assert history_proc.returncode == 0, (
        f"`wikicli.py history` failed: stdout={history_proc.stdout!r} "
        f"stderr={history_proc.stderr!r}"
    )
    history = cli_json(history_proc)
    assert isinstance(history, list), (
        f"CLI history must print a JSON array, got {type(history)!r}."
    )
    assert [entry["revision"] for entry in history] == [1, 2], (
        "CLI history must list revisions [1, 2] in ascending order, got "
        f"{[e.get('revision') for e in history]}."
    )
    assert set(history[0]) == HISTORY_KEYS, (
        f"CLI history entry keys must be exactly {sorted(HISTORY_KEYS)}, "
        f"got {sorted(history[0])}."
    )


def test_cli_stale_revision_exit_code(gel_server):
    slug = unique_slug("clistale")

    create_proc = run_cli(
        "create",
        "--slug",
        slug,
        "--title",
        "CLI page",
        "--body",
        "line one",
        "--author",
        "cli",
    )
    assert create_proc.returncode == 0, (
        f"`wikicli.py create` failed: stdout={create_proc.stdout!r} "
        f"stderr={create_proc.stderr!r}"
    )
    ok_proc = run_cli(
        "update",
        "--slug",
        slug,
        "--expected-revision",
        "1",
        "--author",
        "cli2",
        "--body",
        "line two",
    )
    assert ok_proc.returncode == 0, (
        f"`wikicli.py update` failed: stdout={ok_proc.stdout!r} "
        f"stderr={ok_proc.stderr!r}"
    )

    stale_proc = run_cli(
        "update",
        "--slug",
        slug,
        "--expected-revision",
        "1",
        "--author",
        "cli3",
        "--body",
        "nope",
    )
    assert stale_proc.returncode == 3, (
        "A stale expected revision must exit with status 3, got "
        f"{stale_proc.returncode} (stdout={stale_proc.stdout!r}, "
        f"stderr={stale_proc.stderr!r})."
    )
    payload = cli_json(stale_proc)
    assert payload == {
        "error": "stale_revision",
        "slug": slug,
        "expected_revision": 1,
        "actual_revision": 2,
    }, f"Unexpected stale-revision payload: {payload!r}."

    shown = cli_json(run_cli("show", "--slug", slug))
    assert shown["revision"] == 2, (
        f"A rejected CLI update must not change the revision, got {shown['revision']}."
    )
    assert shown["body"] == "line two", (
        f"A rejected CLI update must not change the body, got {shown['body']!r}."
    )


def test_cli_not_found_and_conflict_exit_codes(gel_server):
    slug = unique_slug("clierr")

    create_proc = run_cli(
        "create",
        "--slug",
        slug,
        "--title",
        "CLI page",
        "--body",
        "line one",
        "--author",
        "cli",
    )
    assert create_proc.returncode == 0, (
        f"`wikicli.py create` failed: stdout={create_proc.stdout!r} "
        f"stderr={create_proc.stderr!r}"
    )

    missing_proc = run_cli("show", "--slug", "missing-page-xyz")
    assert missing_proc.returncode == 4, (
        "An unknown slug must exit with status 4, got "
        f"{missing_proc.returncode} (stdout={missing_proc.stdout!r}, "
        f"stderr={missing_proc.stderr!r})."
    )
    assert cli_json(missing_proc) == {
        "error": "document_not_found",
        "slug": "missing-page-xyz",
    }, f"Unexpected not-found payload: {missing_proc.stdout!r}."

    conflict_proc = run_cli(
        "create",
        "--slug",
        slug,
        "--title",
        "x",
        "--body",
        "y",
        "--author",
        "cli",
    )
    assert conflict_proc.returncode == 5, (
        "A duplicate slug must exit with status 5, got "
        f"{conflict_proc.returncode} (stdout={conflict_proc.stdout!r}, "
        f"stderr={conflict_proc.stderr!r})."
    )
    assert cli_json(conflict_proc) == {
        "error": "slug_conflict",
        "slug": slug,
    }, f"Unexpected slug-conflict payload: {conflict_proc.stdout!r}."


def test_cli_race_accepts_every_append(gel_server):
    slug = unique_slug("clirace")
    count = 8

    create_proc = run_cli(
        "create",
        "--slug",
        slug,
        "--title",
        "Race page",
        "--body",
        "seed",
        "--author",
        "bot",
    )
    assert create_proc.returncode == 0, (
        f"`wikicli.py create` failed: stdout={create_proc.stdout!r} "
        f"stderr={create_proc.stderr!r}"
    )

    started = time.time()
    race_proc = run_cli("race", "--slug", slug, "--count", str(count), "--author", "bot")
    elapsed = time.time() - started
    assert race_proc.returncode == 0, (
        f"`wikicli.py race` failed: stdout={race_proc.stdout!r} "
        f"stderr={race_proc.stderr!r}"
    )
    assert elapsed < 180, (
        f"`wikicli.py race --count {count}` took {elapsed:.1f}s, which suggests a "
        "deadlock or livelock."
    )
    payload = cli_json(race_proc)
    assert set(payload) == RACE_KEYS, (
        f"race output keys must be exactly {sorted(RACE_KEYS)}, got {sorted(payload)}."
    )
    assert payload == {
        "slug": slug,
        "requested": count,
        "accepted": count,
        "final_revision": count + 1,
        "history_length": count + 1,
    }, f"Unexpected race payload: {payload!r}."

    shown = cli_json(run_cli("show", "--slug", slug))
    assert shown["revision"] == count + 1, (
        f"After the race the revision must be {count + 1}, got {shown['revision']}."
    )
    body_lines = shown["body"].split("\n")
    assert body_lines[0] == "seed", (
        f"The seed body line must survive the race, got {body_lines[:1]!r}."
    )
    assert sorted(body_lines[1:]) == sorted(
        f"bot#{i}" for i in range(1, count + 1)
    ), (
        "The body must contain each racing line exactly once, got "
        f"{body_lines[1:]!r}."
    )
