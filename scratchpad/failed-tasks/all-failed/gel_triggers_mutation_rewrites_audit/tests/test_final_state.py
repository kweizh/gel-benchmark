"""Final-state verification for the schema-enforced audit engine task.

Everything is checked by executing plain EdgeQL against the real local Gel
instance and by running the real reporting command; no part of the executor's
code is imported or called directly.
"""

import glob
import json
import os
import secrets
import subprocess
from datetime import datetime, timedelta, timezone

import gel
import pytest

PROJECT_DIR = "/home/user/auditdb"
MIGRATIONS_DIR = os.path.join(PROJECT_DIR, "dbschema", "migrations")
REPORT_SCRIPT = os.path.join(PROJECT_DIR, "audit_report.py")
GEL_UP = "/usr/local/bin/gel-up"

SLACK = timedelta(seconds=5)

GEL_ERRORS = tuple(
    t
    for t in (
        getattr(gel.errors, "GelError", None),
        getattr(gel.errors, "EdgeDBError", None),
    )
    if isinstance(t, type)
) or (Exception,)

DOC_SNAPSHOT_KEYS = {"slug", "title", "version"}
COMMENT_SNAPSHOT_KEYS = {"body", "document_slug", "version"}
REPORT_KEYS = {
    "slug",
    "document_exists",
    "current_version",
    "document_entry_counts",
    "document_max_version",
    "comment_entry_counts",
    "deleted_comment_bodies",
}


# --------------------------------------------------------------------------- #
# infrastructure fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="session")
def client():
    """Start the local Gel server (idempotent) and yield a connected client."""
    proc = subprocess.run([GEL_UP], capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, (
        f"{GEL_UP} failed to start the local Gel server (rc={proc.returncode}): "
        f"{proc.stdout}\n{proc.stderr}"
    )
    c = gel.create_client()
    try:
        c.ensure_connected()
        yield c
    finally:
        c.close()


@pytest.fixture(scope="session")
def token():
    return secrets.token_hex(4)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def now_utc():
    return datetime.now(timezone.utc)


def fetch_document(client, slug):
    return client.query_single(
        """
        select Document { id, slug, title, body, version, created_at, modified_at }
        filter .slug = <str>$slug
        """,
        slug=slug,
    )


def fetch_comment(client, body):
    return client.query_single(
        """
        select Comment {
            id,
            body,
            version,
            created_at,
            modified_at,
            document_slug,
            linked := .document.slug,
        }
        filter .body = <str>$body
        limit 1
        """,
        body=body,
    )


def entries_for_object(client, object_id):
    """All audit entries recorded for one concrete object, oldest first."""
    rows = client.query(
        """
        select AuditEntry { action, entity_type, entity_id, version, at, snapshot }
        filter .entity_id = <uuid>$oid
        order by .version then .action
        """,
        oid=object_id,
    )
    return [_normalize(r) for r in rows]


def document_entries_for_slug(client, slug):
    rows = client.query(
        """
        select AuditEntry { action, entity_type, entity_id, version, at, snapshot }
        filter .entity_type = 'Document'
           and json_get(.snapshot, 'slug') ?= <json><str>$slug
        order by .at then .version
        """,
        slug=slug,
    )
    return [_normalize(r) for r in rows]


def comment_entries_for_slug(client, slug):
    rows = client.query(
        """
        select AuditEntry { action, entity_type, entity_id, version, at, snapshot }
        filter .entity_type = 'Comment'
           and json_get(.snapshot, 'document_slug') ?= <json><str>$slug
        order by .at then .version
        """,
        slug=slug,
    )
    return [_normalize(r) for r in rows]


def _normalize(row):
    snapshot = row.snapshot
    if isinstance(snapshot, (str, bytes)):
        snapshot = json.loads(snapshot)
    return {
        "action": row.action,
        "entity_type": row.entity_type,
        "entity_id": str(row.entity_id),
        "version": row.version,
        "at": row.at,
        "snapshot": snapshot,
    }


def assert_in_window(value, low, high, label):
    assert isinstance(value, datetime), f"{label} is not a datetime: {value!r}"
    assert low - SLACK <= value <= high + SLACK, (
        f"{label} = {value!r} was not produced by the executing statement "
        f"(expected between {low!r} and {high!r}); a client-supplied or stale value leaked through."
    )


def run_report(*args):
    cmd = ["python3", REPORT_SCRIPT, *args]
    return subprocess.run(
        cmd,
        cwd="/tmp",
        capture_output=True,
        text=True,
        timeout=300,
        env=os.environ.copy(),
    )


def report_json(*args):
    proc = run_report(*args)
    assert proc.returncode == 0, (
        f"{' '.join(['python3', REPORT_SCRIPT, *args])} exited {proc.returncode}. "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"stdout of the report command is not a single JSON object ({exc}): {proc.stdout!r}"
        ) from exc
    assert isinstance(payload, dict), f"The report must print a JSON object, got: {proc.stdout!r}"
    assert set(payload) == REPORT_KEYS, (
        f"The report JSON must contain exactly the keys {sorted(REPORT_KEYS)}, "
        f"got {sorted(payload)}."
    )
    return payload


# --------------------------------------------------------------------------- #
# scenario fixtures (perform the mutations, recording observations)
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="session")
def doc_a(client, token):
    """insert with forged metadata, a no-op update, then an update with forged metadata."""
    slug = f"doc-a-{token}"
    result: dict = {"slug": slug}

    before = now_utc()
    client.execute(
        """
        insert Document {
            slug := <str>$slug,
            title := 'A',
            body := 'a',
            version := 999,
            created_at := <datetime>'2000-01-01T00:00:00Z',
            modified_at := <datetime>'2000-01-01T00:00:00Z',
        }
        """,
        slug=slug,
    )
    result["insert_window"] = (before, now_utc())
    result["after_insert"] = fetch_document(client, slug)

    before = now_utc()
    client.execute(
        "update Document filter .slug = <str>$slug set { title := 'A' }",
        slug=slug,
    )
    result["noop_window"] = (before, now_utc())
    result["after_noop"] = fetch_document(client, slug)

    before = now_utc()
    client.execute(
        """
        update Document filter .slug = <str>$slug set {
            title := 'A2',
            version := 42,
            created_at := <datetime>'1990-01-01T00:00:00Z',
            modified_at := <datetime>'1990-01-01T00:00:00Z',
        }
        """,
        slug=slug,
    )
    result["forged_window"] = (before, now_utc())
    result["after_forged"] = fetch_document(client, slug)
    result["entries"] = entries_for_object(client, result["after_forged"].id)
    return result


@pytest.fixture(scope="session")
def bulk_docs(client, token):
    """Three documents inserted by one statement, then updated by one statement."""
    slugs = [f"doc-b1-{token}", f"doc-b2-{token}", f"doc-b3-{token}"]
    client.execute(
        """
        for s in array_unpack(<array<str>>$slugs) union (
            insert Document { slug := s, title := 'x', body := 'y' }
        )
        """,
        slugs=slugs,
    )
    inserted = {s: fetch_document(client, s) for s in slugs}
    client.execute(
        """
        for s in array_unpack(<array<str>>$slugs) union (
            update Document filter .slug = s set { title := 'z' }
        )
        """,
        slugs=slugs,
    )
    updated = {s: fetch_document(client, s) for s in slugs}
    entries = {s: entries_for_object(client, updated[s].id) for s in slugs}
    return {"slugs": slugs, "inserted": inserted, "updated": updated, "entries": entries}


@pytest.fixture(scope="session")
def moved_comment(client, token, doc_a, bulk_docs):
    """A comment created on doc-a with a forged document_slug, later moved to doc-b1."""
    body = f"cmt-{token}"
    target = bulk_docs["slugs"][0]
    client.execute(
        """
        insert Comment {
            body := <str>$body,
            document := assert_single((select Document filter .slug = <str>$slug)),
            document_slug := 'FORGED',
        }
        """,
        body=body,
        slug=doc_a["slug"],
    )
    after_insert = fetch_comment(client, body)
    client.execute(
        """
        update Comment filter .body = <str>$body set {
            document := assert_single((select Document filter .slug = <str>$slug)),
        }
        """,
        body=body,
        slug=target,
    )
    after_move = fetch_comment(client, body)
    return {
        "body": body,
        "target_slug": target,
        "after_insert": after_insert,
        "after_move": after_move,
        "entries": entries_for_object(client, after_move.id),
    }


@pytest.fixture(scope="session")
def solo_comment(client, token, doc_a, moved_comment):
    """A comment on doc-a that is deleted directly with a `delete Comment` statement."""
    body = f"cmt-solo-{token}"
    client.execute(
        """
        insert Comment {
            body := <str>$body,
            document := assert_single((select Document filter .slug = <str>$slug)),
        }
        """,
        body=body,
        slug=doc_a["slug"],
    )
    created = fetch_comment(client, body)
    client.execute("delete Comment filter .body = <str>$body", body=body)
    return {
        "body": body,
        "id": str(created.id),
        "still_exists": fetch_comment(client, body) is not None,
        "entries": entries_for_object(client, created.id),
    }


@pytest.fixture(scope="session")
def cascade(client, token):
    """A document with three comments (one of them updated), deleted in one statement."""
    slug = f"doc-del-{token}"
    bodies = [f"cd1-{token}", f"cd2-{token}", f"cd3-{token}"]
    renamed = f"cd1x-{token}"

    client.execute(
        "insert Document { slug := <str>$slug, title := 'D', body := 'd' }",
        slug=slug,
    )
    doc = fetch_document(client, slug)
    client.execute(
        """
        for b in array_unpack(<array<str>>$bodies) union (
            insert Comment {
                body := b,
                document := assert_single((select Document filter .slug = <str>$slug)),
            }
        )
        """,
        bodies=bodies,
        slug=slug,
    )
    comments = {b: fetch_comment(client, b) for b in bodies}
    client.execute(
        "update Comment filter .body = <str>$old set { body := <str>$new }",
        old=bodies[0],
        new=renamed,
    )

    client.execute("delete Document filter .slug = <str>$slug", slug=slug)

    return {
        "slug": slug,
        "renamed": renamed,
        "bodies": [renamed, bodies[1], bodies[2]],
        "doc_id": str(doc.id),
        "comment_ids": {b: str(c.id) for b, c in comments.items()},
        "document_left": fetch_document(client, slug),
        "comments_left": client.query_single(
            """
            select count((
                select Comment
                filter .body in array_unpack(<array<str>>$bodies)
            ))
            """,
            bodies=[renamed, bodies[1], bodies[2]],
        ),
        "doc_entries": document_entries_for_slug(client, slug),
        "comment_entries": comment_entries_for_slug(client, slug),
    }


# --------------------------------------------------------------------------- #
# 1. migration state
# --------------------------------------------------------------------------- #


def test_migration_status_reports_up_to_date(client):
    proc = subprocess.run(
        ["gel", "migration", "status"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, (
        "'gel migration status' must exit 0 with the database up to date, but it exited "
        f"{proc.returncode}: {proc.stdout}\n{proc.stderr}"
    )
    combined = f"{proc.stdout}\n{proc.stderr}".lower()
    assert "up to date" in combined, (
        f"'gel migration status' did not report the database up to date: {proc.stdout}\n{proc.stderr}"
    )


def test_schema_changes_are_recorded_as_migrations(client):
    files = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql")))
    assert len(files) >= 2, (
        "The new schema must be delivered as an additional migration in "
        f"{MIGRATIONS_DIR}; found only {files}."
    )


# --------------------------------------------------------------------------- #
# 2. pre-existing data survived
# --------------------------------------------------------------------------- #


def test_seeded_documents_survived(client):
    expected = {
        "seed-alpha": ("Alpha Doc", "alpha body"),
        "seed-beta": ("Beta Doc", "beta body"),
        "seed-gamma": ("Gamma Doc", "gamma body"),
    }
    for slug, (title, body) in expected.items():
        doc = fetch_document(client, slug)
        assert doc is not None, f"Pre-existing Document '{slug}' no longer exists."
        assert (doc.title, doc.body) == (title, body), (
            f"Pre-existing Document '{slug}' must keep title/body {(title, body)}, "
            f"got {(doc.title, doc.body)}."
        )
        assert doc.version is not None and doc.version >= 1, (
            f"Pre-existing Document '{slug}' must have a version of at least 1, got {doc.version!r}."
        )
        assert isinstance(doc.created_at, datetime), (
            f"Pre-existing Document '{slug}' has no created_at value."
        )
        assert isinstance(doc.modified_at, datetime), (
            f"Pre-existing Document '{slug}' has no modified_at value."
        )


def test_seeded_comments_survived(client):
    expected = {
        "alpha comment one": "seed-alpha",
        "alpha comment two": "seed-alpha",
        "beta comment one": "seed-beta",
    }
    for body, slug in expected.items():
        comment = fetch_comment(client, body)
        assert comment is not None, f"Pre-existing Comment '{body}' no longer exists."
        assert comment.linked == slug, (
            f"Pre-existing Comment '{body}' must still link to Document '{slug}', "
            f"got '{comment.linked}'."
        )
        assert comment.version is not None and comment.version >= 1, (
            f"Pre-existing Comment '{body}' must have a version of at least 1, got {comment.version!r}."
        )


# --------------------------------------------------------------------------- #
# 3. insert metadata cannot be forged
# --------------------------------------------------------------------------- #


def test_insert_ignores_client_supplied_metadata(doc_a):
    doc = doc_a["after_insert"]
    low, high = doc_a["insert_window"]
    assert doc is not None, f"Document '{doc_a['slug']}' was not inserted."
    assert doc.version == 1, (
        f"A freshly inserted Document must have version 1 even when the client sends "
        f"version := 999, got {doc.version}."
    )
    assert_in_window(doc.created_at, low, high, "created_at after insert")
    assert_in_window(doc.modified_at, low, high, "modified_at after insert")
    assert doc.created_at == doc.modified_at, (
        "Right after an insert, modified_at must equal created_at, got "
        f"{doc.created_at!r} vs {doc.modified_at!r}."
    )


def test_insert_produces_exactly_one_audit_entry(doc_a):
    inserts = [e for e in doc_a["entries"] if e["action"] == "insert"]
    assert len(inserts) == 1, (
        f"Exactly one 'insert' audit entry is expected for {doc_a['slug']}, got {len(inserts)}: "
        f"{doc_a['entries']}"
    )
    entry = inserts[0]
    doc = doc_a["after_insert"]
    assert entry["entity_type"] == "Document", (
        f"entity_type must be 'Document', got {entry['entity_type']!r}."
    )
    assert entry["entity_id"] == str(doc.id), (
        f"entity_id must be the audited object's id {doc.id}, got {entry['entity_id']}."
    )
    assert entry["version"] == 1, f"The insert entry must record version 1, got {entry['version']}."
    assert entry["at"] == doc.created_at, (
        "The audit entry's 'at' must be the timestamp of the statement that inserted the object "
        f"(created_at={doc.created_at!r}), got {entry['at']!r}."
    )


def test_document_snapshot_shape(doc_a):
    inserts = [e for e in doc_a["entries"] if e["action"] == "insert"]
    snapshot = inserts[0]["snapshot"]
    assert isinstance(snapshot, dict), f"snapshot must be a JSON object, got {snapshot!r}."
    assert set(snapshot) == DOC_SNAPSHOT_KEYS, (
        f"A Document snapshot must have exactly the keys {sorted(DOC_SNAPSHOT_KEYS)}, "
        f"got {sorted(snapshot)}."
    )
    assert snapshot == {"slug": doc_a["slug"], "title": "A", "version": 1}, (
        f"Unexpected Document snapshot content: {snapshot}."
    )


# --------------------------------------------------------------------------- #
# 4./5. updates: version bumps, immutable created_at, forged values ignored
# --------------------------------------------------------------------------- #


def test_noop_update_still_bumps_version(doc_a):
    doc = doc_a["after_noop"]
    low, high = doc_a["noop_window"]
    assert doc.version == 2, (
        "An update statement that assigns the value the object already had must still "
        f"bump version to 2, got {doc.version}."
    )
    assert doc.created_at == doc_a["after_insert"].created_at, (
        "created_at must never change after the insert, but the no-op update changed it from "
        f"{doc_a['after_insert'].created_at!r} to {doc.created_at!r}."
    )
    assert_in_window(doc.modified_at, low, high, "modified_at after the no-op update")
    assert doc.modified_at >= doc.created_at, (
        f"modified_at ({doc.modified_at!r}) must not be older than created_at ({doc.created_at!r})."
    )


def test_noop_update_is_audited(doc_a):
    updates = [e for e in doc_a["entries"] if e["action"] == "update" and e["version"] == 2]
    assert len(updates) == 1, (
        f"Exactly one 'update' audit entry with version 2 is expected, got {len(updates)}: "
        f"{doc_a['entries']}"
    )
    entry = updates[0]
    assert entry["at"] == doc_a["after_noop"].modified_at, (
        "The update entry's 'at' must equal the modified_at written by the same statement "
        f"({doc_a['after_noop'].modified_at!r}), got {entry['at']!r}."
    )
    assert entry["snapshot"] == {"slug": doc_a["slug"], "title": "A", "version": 2}, (
        f"Unexpected snapshot for the no-op update entry: {entry['snapshot']}."
    )


def test_update_ignores_client_supplied_metadata(doc_a):
    doc = doc_a["after_forged"]
    low, high = doc_a["forged_window"]
    assert doc.version == 3, (
        "An update that explicitly sets version := 42 must still result in the previous "
        f"version plus one (3), got {doc.version}."
    )
    assert doc.created_at == doc_a["after_insert"].created_at, (
        "created_at must be immutable, but a client managed to change it to "
        f"{doc.created_at!r} (insert value was {doc_a['after_insert'].created_at!r})."
    )
    assert_in_window(doc.modified_at, low, high, "modified_at after the forged update")
    assert doc.title == "A2", f"The update must still apply real data changes, got title {doc.title!r}."


def test_forged_update_is_audited(doc_a):
    updates = [e for e in doc_a["entries"] if e["action"] == "update" and e["version"] == 3]
    assert len(updates) == 1, (
        f"Exactly one 'update' audit entry with version 3 is expected, got {len(updates)}: "
        f"{doc_a['entries']}"
    )
    entry = updates[0]
    assert entry["at"] == doc_a["after_forged"].modified_at, (
        "The update entry's 'at' must equal the modified_at written by the same statement, "
        f"got {entry['at']!r} vs {doc_a['after_forged'].modified_at!r}."
    )
    assert entry["snapshot"] == {"slug": doc_a["slug"], "title": "A2", "version": 3}, (
        f"Unexpected snapshot for the forged update entry: {entry['snapshot']}."
    )


def test_no_extra_entries_for_doc_a(doc_a):
    actions = sorted(e["action"] for e in doc_a["entries"])
    assert actions == ["insert", "update", "update"], (
        "Exactly three audit entries (one insert, two updates) are expected for "
        f"{doc_a['slug']}, got {doc_a['entries']}."
    )


# --------------------------------------------------------------------------- #
# 6./7. transactions
# --------------------------------------------------------------------------- #


def test_multi_statement_transaction_versions(client, token):
    slug = f"doc-tx-{token}"
    for tx in client.transaction():
        with tx:
            tx.execute(
                "insert Document { slug := <str>$slug, title := 'T', body := 't' }",
                slug=slug,
            )
            tx.execute(
                "update Document filter .slug = <str>$slug set { title := 'T2' }",
                slug=slug,
            )
            tx.execute(
                "update Document filter .slug = <str>$slug set { title := 'T3' }",
                slug=slug,
            )

    doc = fetch_document(client, slug)
    assert doc is not None, f"The committed transaction did not create Document '{slug}'."
    assert doc.version == 3, (
        f"Three statements (insert + 2 updates) in one transaction must yield version 3, got {doc.version}."
    )
    entries = entries_for_object(client, doc.id)
    assert [(e["action"], e["version"]) for e in entries] == [
        ("insert", 1),
        ("update", 2),
        ("update", 3),
    ], f"Expected exactly one entry per statement with versions 1, 2, 3; got {entries}."


def test_rolled_back_transaction_leaves_no_audit_trail(client, token):
    slug = f"doc-rb-{token}"

    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        for tx in client.transaction():
            with tx:
                tx.execute(
                    "insert Document { slug := <str>$slug, title := 'R', body := 'r' }",
                    slug=slug,
                )
                raise Boom()

    assert fetch_document(client, slug) is None, (
        f"Document '{slug}' must not exist after the transaction was rolled back."
    )
    entries = document_entries_for_slug(client, slug)
    assert entries == [], (
        f"A rolled back transaction must leave no audit entries behind, found {entries}."
    )


# --------------------------------------------------------------------------- #
# 8. bulk statements
# --------------------------------------------------------------------------- #


def test_bulk_insert_creates_one_entry_per_object(bulk_docs):
    ats = set()
    for slug in bulk_docs["slugs"]:
        inserts = [e for e in bulk_docs["entries"][slug] if e["action"] == "insert"]
        assert len(inserts) == 1, (
            f"Exactly one 'insert' entry is expected for {slug} after the bulk insert, got {inserts}."
        )
        assert inserts[0]["version"] == 1, (
            f"The bulk-inserted document {slug} must be audited with version 1, got {inserts[0]['version']}."
        )
        assert inserts[0]["snapshot"] == {"slug": slug, "title": "x", "version": 1}, (
            f"Unexpected snapshot for bulk insert of {slug}: {inserts[0]['snapshot']}."
        )
        ats.add(inserts[0]["at"])
    assert len(ats) == 1, (
        "All audit entries produced by a single bulk insert statement must share one identical "
        f"'at' timestamp, got {sorted(ats)}."
    )


def test_bulk_update_creates_one_entry_per_object(bulk_docs):
    insert_ats = set()
    update_ats = set()
    for slug in bulk_docs["slugs"]:
        entries = bulk_docs["entries"][slug]
        updates = [e for e in entries if e["action"] == "update"]
        assert len(updates) == 1, (
            f"Exactly one 'update' entry is expected for {slug} after the bulk update, got {updates}."
        )
        assert updates[0]["version"] == 2, (
            f"The bulk-updated document {slug} must be audited with version 2, got {updates[0]['version']}."
        )
        assert bulk_docs["updated"][slug].version == 2, (
            f"{slug} must have version 2 after the bulk update, got {bulk_docs['updated'][slug].version}."
        )
        update_ats.add(updates[0]["at"])
        insert_ats.update(e["at"] for e in entries if e["action"] == "insert")
    assert len(update_ats) == 1, (
        "All audit entries produced by a single bulk update statement must share one identical "
        f"'at' timestamp, got {sorted(update_ats)}."
    )
    assert update_ats.isdisjoint(insert_ats), (
        "The bulk update entries must carry their own statement timestamp, but it matches the "
        "timestamp of the earlier bulk insert."
    )


# --------------------------------------------------------------------------- #
# 9. comment denormalization
# --------------------------------------------------------------------------- #


def test_comment_document_slug_cannot_be_forged(moved_comment, doc_a):
    comment = moved_comment["after_insert"]
    assert comment is not None, f"Comment '{moved_comment['body']}' was not inserted."
    assert comment.document_slug == doc_a["slug"], (
        "document_slug must be derived from the linked Document even when the client sends "
        f"'FORGED', got {comment.document_slug!r}."
    )
    assert comment.version == 1, f"A new Comment must have version 1, got {comment.version}."
    inserts = [e for e in moved_comment["entries"] if e["action"] == "insert"]
    assert len(inserts) == 1, (
        f"Exactly one 'insert' audit entry is expected for the comment, got {inserts}."
    )
    assert inserts[0]["entity_type"] == "Comment", (
        f"entity_type must be 'Comment', got {inserts[0]['entity_type']!r}."
    )
    assert set(inserts[0]["snapshot"]) == COMMENT_SNAPSHOT_KEYS, (
        f"A Comment snapshot must have exactly the keys {sorted(COMMENT_SNAPSHOT_KEYS)}, "
        f"got {sorted(inserts[0]['snapshot'])}."
    )
    assert inserts[0]["snapshot"] == {
        "body": moved_comment["body"],
        "document_slug": doc_a["slug"],
        "version": 1,
    }, f"Unexpected Comment snapshot: {inserts[0]['snapshot']}."


def test_comment_document_slug_follows_link_change(moved_comment):
    comment = moved_comment["after_move"]
    assert comment.document_slug == moved_comment["target_slug"], (
        "After re-pointing the document link, document_slug must follow the new parent "
        f"({moved_comment['target_slug']}), got {comment.document_slug!r}."
    )
    assert comment.version == 2, f"The moved Comment must have version 2, got {comment.version}."
    updates = [e for e in moved_comment["entries"] if e["action"] == "update"]
    assert len(updates) == 1, f"Exactly one 'update' entry is expected for the comment, got {updates}."
    assert updates[0]["snapshot"] == {
        "body": moved_comment["body"],
        "document_slug": moved_comment["target_slug"],
        "version": 2,
    }, f"Unexpected snapshot for the moved comment: {updates[0]['snapshot']}."


# --------------------------------------------------------------------------- #
# 10. direct comment deletion
# --------------------------------------------------------------------------- #


def test_direct_comment_delete_is_audited_once(solo_comment):
    assert not solo_comment["still_exists"], (
        f"Comment '{solo_comment['body']}' should have been deleted."
    )
    deletes = [e for e in solo_comment["entries"] if e["action"] == "delete"]
    assert len(deletes) == 1, (
        f"Exactly one 'delete' audit entry is expected for a directly deleted comment, got {deletes}."
    )
    entry = deletes[0]
    assert entry["entity_type"] == "Comment", (
        f"entity_type must be 'Comment', got {entry['entity_type']!r}."
    )
    assert entry["entity_id"] == solo_comment["id"], (
        f"entity_id must be {solo_comment['id']}, got {entry['entity_id']}."
    )
    assert entry["version"] == 1, f"The deleted comment's version was 1, got {entry['version']}."
    assert entry["snapshot"]["body"] == solo_comment["body"], (
        f"Unexpected snapshot for the deleted comment: {entry['snapshot']}."
    )


# --------------------------------------------------------------------------- #
# 11. cascade-aware deletion
# --------------------------------------------------------------------------- #


def test_document_delete_removes_its_comments(cascade):
    assert cascade["document_left"] is None, (
        f"Document '{cascade['slug']}' must be gone after the delete statement."
    )
    assert cascade["comments_left"] == 0, (
        f"All comments of '{cascade['slug']}' must be removed with it, "
        f"{cascade['comments_left']} still exist."
    )


def test_document_delete_audits_document_and_children(cascade):
    doc_deletes = [e for e in cascade["doc_entries"] if e["action"] == "delete"]
    assert len(doc_deletes) == 1, (
        f"Exactly one 'delete' entry is expected for the document, got {doc_deletes}."
    )
    assert doc_deletes[0]["entity_id"] == cascade["doc_id"], (
        f"The document delete entry must reference {cascade['doc_id']}, got {doc_deletes[0]['entity_id']}."
    )
    assert doc_deletes[0]["version"] == 1, (
        f"The document was never updated, so its delete entry must record version 1, "
        f"got {doc_deletes[0]['version']}."
    )

    comment_deletes = [e for e in cascade["comment_entries"] if e["action"] == "delete"]
    assert len(comment_deletes) == 3, (
        "Deleting a document with three comments must record exactly one 'delete' entry per "
        f"removed comment, got {comment_deletes}."
    )
    by_body = {e["snapshot"]["body"]: e for e in comment_deletes}
    expected_versions = {
        cascade["renamed"]: 2,
        cascade["bodies"][1]: 1,
        cascade["bodies"][2]: 1,
    }
    assert set(by_body) == set(expected_versions), (
        f"Expected delete entries for {sorted(expected_versions)}, got {sorted(by_body)}."
    )
    for body, version in expected_versions.items():
        assert by_body[body]["version"] == version, (
            f"The delete entry for comment '{body}' must record version {version}, "
            f"got {by_body[body]['version']}."
        )
        assert by_body[body]["snapshot"]["document_slug"] == cascade["slug"], (
            f"The delete entry for comment '{body}' must keep document_slug "
            f"'{cascade['slug']}', got {by_body[body]['snapshot']['document_slug']!r}."
        )
        assert set(by_body[body]["snapshot"]) == COMMENT_SNAPSHOT_KEYS, (
            f"Unexpected snapshot keys for comment '{body}': {sorted(by_body[body]['snapshot'])}."
        )

    ats = {e["at"] for e in doc_deletes + comment_deletes}
    assert len(ats) == 1, (
        "All audit entries created by the single delete statement must share one identical 'at' "
        f"timestamp, got {sorted(ats)}."
    )


# --------------------------------------------------------------------------- #
# 12. append-only log
# --------------------------------------------------------------------------- #


def test_audit_entry_update_is_rejected(client, doc_a):
    entries = document_entries_for_slug(client, doc_a["slug"])
    assert entries, f"No audit entries found for {doc_a['slug']}."
    target = entries[0]
    found = client.query_single(
        """
        select (
            select AuditEntry
            filter .entity_type = 'Document'
               and json_get(.snapshot, 'slug') ?= <json><str>$slug
               and .action = <str>$action
               and .version = <int64>$version
            limit 1
        ) { id }
        """,
        slug=doc_a["slug"],
        action=target["action"],
        version=target["version"],
    )
    assert found is not None, "Could not resolve an existing AuditEntry id."
    target_id = found.id

    with pytest.raises(GEL_ERRORS):
        client.execute(
            "update AuditEntry filter .id = <uuid>$oid set { action := 'insert' }",
            oid=target_id,
        )

    after = client.query_single(
        "select AuditEntry { action, snapshot } filter .id = <uuid>$oid",
        oid=target_id,
    )
    assert after is not None, "The rejected update must not remove the audit entry."
    assert after.action == target["action"], (
        f"The audit entry's action changed from {target['action']!r} to {after.action!r} "
        "even though the update was supposed to fail."
    )
    snapshot = after.snapshot
    if isinstance(snapshot, (str, bytes)):
        snapshot = json.loads(snapshot)
    assert snapshot == target["snapshot"], (
        f"The audit entry's snapshot changed from {target['snapshot']} to {snapshot}."
    )


def test_audit_entry_delete_is_rejected(client, doc_a):
    before = document_entries_for_slug(client, doc_a["slug"])
    assert before, f"No audit entries found for {doc_a['slug']}."
    found = client.query_single(
        """
        select (
            select AuditEntry
            filter .entity_type = 'Document'
               and json_get(.snapshot, 'slug') ?= <json><str>$slug
            limit 1
        ) { id }
        """,
        slug=doc_a["slug"],
    )
    assert found is not None, "Could not resolve an existing AuditEntry id."
    target_id = found.id
    with pytest.raises(GEL_ERRORS):
        client.execute("delete AuditEntry filter .id = <uuid>$oid", oid=target_id)

    still_there = client.query_single(
        "select exists (select AuditEntry filter .id = <uuid>$oid)", oid=target_id
    )
    assert still_there, "A rejected delete must leave the audit entry in place."
    after = document_entries_for_slug(client, doc_a["slug"])
    assert len(after) == len(before), (
        f"The number of audit entries for {doc_a['slug']} changed from {len(before)} to {len(after)}."
    )


def test_audit_entry_bulk_delete_is_rejected(client, doc_a):
    before = document_entries_for_slug(client, doc_a["slug"])
    with pytest.raises(GEL_ERRORS):
        client.execute(
            """
            delete AuditEntry
            filter .entity_type = 'Document'
               and json_get(.snapshot, 'slug') ?= <json><str>$slug
            """,
            slug=doc_a["slug"],
        )
    after = document_entries_for_slug(client, doc_a["slug"])
    assert len(after) == len(before) and len(after) > 0, (
        "A bulk delete against the audit log must fail and delete nothing, but the entry count "
        f"went from {len(before)} to {len(after)}."
    )


# --------------------------------------------------------------------------- #
# 13.-16. reporting command
# --------------------------------------------------------------------------- #


def test_report_script_exists(client):
    assert os.path.isfile(REPORT_SCRIPT), f"{REPORT_SCRIPT} does not exist."


def test_report_for_populated_slug(client, doc_a, moved_comment, solo_comment):
    payload = report_json("--slug", doc_a["slug"])
    assert payload["slug"] == doc_a["slug"], f"Unexpected slug echoed back: {payload['slug']!r}."
    assert payload["document_exists"] is True, (
        f"document_exists must be true for the live document {doc_a['slug']}, got {payload['document_exists']!r}."
    )
    assert payload["current_version"] == 3, (
        f"current_version must be the live document's version 3, got {payload['current_version']!r}."
    )
    assert payload["document_entry_counts"] == {"insert": 1, "update": 2, "delete": 0}, (
        f"Unexpected document_entry_counts: {payload['document_entry_counts']}."
    )
    assert payload["document_max_version"] == 3, (
        f"document_max_version must be 3, got {payload['document_max_version']!r}."
    )
    assert payload["comment_entry_counts"] == {"insert": 2, "update": 0, "delete": 1}, (
        f"Unexpected comment_entry_counts: {payload['comment_entry_counts']}."
    )
    assert payload["deleted_comment_bodies"] == [solo_comment["body"]], (
        f"Unexpected deleted_comment_bodies: {payload['deleted_comment_bodies']}."
    )


def test_report_for_deleted_document(client, cascade):
    payload = report_json("--slug", cascade["slug"])
    assert payload["document_exists"] is False, (
        f"document_exists must be false for the deleted document, got {payload['document_exists']!r}."
    )
    assert payload["current_version"] is None, (
        f"current_version must be null for a deleted document, got {payload['current_version']!r}."
    )
    assert payload["document_entry_counts"] == {"insert": 1, "update": 0, "delete": 1}, (
        f"Unexpected document_entry_counts: {payload['document_entry_counts']}."
    )
    assert payload["document_max_version"] == 1, (
        f"document_max_version must be 1, got {payload['document_max_version']!r}."
    )
    assert payload["comment_entry_counts"] == {"insert": 3, "update": 1, "delete": 3}, (
        f"Unexpected comment_entry_counts: {payload['comment_entry_counts']}."
    )
    assert payload["deleted_comment_bodies"] == sorted(cascade["bodies"]), (
        f"deleted_comment_bodies must be {sorted(cascade['bodies'])} in ascending order, "
        f"got {payload['deleted_comment_bodies']}."
    )


def test_report_for_unknown_slug(client, token):
    unknown = f"no-such-slug-{token}"
    payload = report_json("--slug", unknown)
    assert payload["slug"] == unknown, f"Unexpected slug echoed back: {payload['slug']!r}."
    assert payload["document_exists"] is False, (
        f"document_exists must be false for an unknown slug, got {payload['document_exists']!r}."
    )
    assert payload["current_version"] is None, (
        f"current_version must be null for an unknown slug, got {payload['current_version']!r}."
    )
    assert payload["document_entry_counts"] == {"insert": 0, "update": 0, "delete": 0}, (
        f"All action keys must be present with zeros, got {payload['document_entry_counts']}."
    )
    assert payload["comment_entry_counts"] == {"insert": 0, "update": 0, "delete": 0}, (
        f"All action keys must be present with zeros, got {payload['comment_entry_counts']}."
    )
    assert payload["document_max_version"] is None, (
        f"document_max_version must be null when there are no entries, got {payload['document_max_version']!r}."
    )
    assert payload["deleted_comment_bodies"] == [], (
        f"deleted_comment_bodies must be empty, got {payload['deleted_comment_bodies']}."
    )


def test_report_is_read_only(client, doc_a, moved_comment, solo_comment):
    entries_before = len(document_entries_for_slug(client, doc_a["slug"])) + len(
        comment_entries_for_slug(client, doc_a["slug"])
    )
    first = report_json("--slug", doc_a["slug"])
    second = report_json("--slug", doc_a["slug"])
    assert first == second, (
        f"Two consecutive report runs returned different payloads:\n{first}\n{second}"
    )
    doc = fetch_document(client, doc_a["slug"])
    assert doc.version == 3, (
        f"Running the report must not mutate anything, but the document version is now {doc.version}."
    )
    entries_after = len(document_entries_for_slug(client, doc_a["slug"])) + len(
        comment_entries_for_slug(client, doc_a["slug"])
    )
    assert entries_after == entries_before, (
        f"Running the report changed the number of audit entries from {entries_before} to {entries_after}."
    )


def test_report_requires_slug_argument(client):
    proc = run_report()
    assert proc.returncode != 0, (
        "Running the report without --slug must exit with a non-zero status, "
        f"got {proc.returncode} (stdout={proc.stdout!r})."
    )
    assert proc.stdout.strip() == "", (
        f"Running the report without --slug must print nothing on stdout, got {proc.stdout!r}."
    )
