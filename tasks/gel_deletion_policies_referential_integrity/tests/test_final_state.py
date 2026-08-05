"""Final-state verification for the Gel deletion-policy / referential-integrity task."""

import asyncio
import importlib.util
import os
import shutil
import subprocess
import sys

import gel
import gel.errors
import pytest

PROJECT_DIR = "/home/user/docmgr"
MIGRATIONS_DIR = os.path.join(PROJECT_DIR, "dbschema", "migrations")
PURGE_MODULE = os.path.join(PROJECT_DIR, "purge.py")

TYPE_NAMES = [
    "default::Workspace",
    "default::Folder",
    "default::Attachment",
    "default::Editor",
    "default::Document",
    "default::ArchivedRecord",
]

WIPE_ORDER = [
    "delete ArchivedRecord;",
    "delete Document;",
    "delete Folder;",
    "delete Workspace;",
    "delete Attachment;",
    "delete Editor;",
]


# ---------------------------------------------------------------- fixtures


@pytest.fixture(scope="session")
def gel_server():
    """Guarantee the local Gel server is up before anything touches the DB or CLI."""
    serve = shutil.which("gel-serve")
    assert serve is not None, "The 'gel-serve' helper is missing from PATH."
    proc = subprocess.run([serve], capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, (
        "Could not start the local Gel server: "
        f"stdout={proc.stdout} stderr={proc.stderr}"
    )
    return True


@pytest.fixture(scope="session")
def client(gel_server):
    c = gel.create_client()
    try:
        c.ensure_connected()
    except Exception as exc:  # pragma: no cover - environment failure
        pytest.fail(f"Could not connect to the local Gel instance: {exc!r}")
    yield c
    c.close()


@pytest.fixture(scope="session")
def purge_module(client):
    assert os.path.isfile(PURGE_MODULE), f"Expected the purge module at {PURGE_MODULE}."
    if PROJECT_DIR not in sys.path:
        sys.path.insert(0, PROJECT_DIR)
    spec = importlib.util.spec_from_file_location("purge", PURGE_MODULE)
    assert spec is not None and spec.loader is not None, (
        f"{PURGE_MODULE} could not be loaded as a Python module."
    )
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        pytest.fail(f"Importing {PURGE_MODULE} failed: {exc!r}")
    assert hasattr(module, "purge_workspace"), (
        "purge.py does not expose a 'purge_workspace' function."
    )
    return module


@pytest.fixture(autouse=True)
def clean_database(client):
    _wipe(client)
    yield


def _wipe(client):
    for stmt in WIPE_ORDER:
        try:
            client.execute(stmt)
        except gel.errors.InvalidReferenceError as exc:
            pytest.fail(
                "The required object types are missing from the 'default' module "
                f"(failed on {stmt!r}): {exc}"
            )
        except Exception as exc:  # pragma: no cover
            pytest.fail(f"Could not reset the database with {stmt!r}: {exc!r}")


# ---------------------------------------------------------------- helpers


def add_workspace(client, name):
    client.execute("insert Workspace { name := <str>$name }", name=name)


def add_folder(client, name, workspace):
    client.execute(
        """
        insert Folder {
            name := <str>$name,
            workspace := assert_single((
                select Workspace filter .name = <str>$workspace
            ))
        }
        """,
        name=name,
        workspace=workspace,
    )


def add_attachment(client, filename, byte_size):
    client.execute(
        "insert Attachment { filename := <str>$f, byte_size := <int64>$s }",
        f=filename,
        s=byte_size,
    )


def add_editor(client, email):
    client.execute("insert Editor { email := <str>$e }", e=email)


def add_document(client, title, folder, workspace, attachments=(), editor=None):
    client.execute(
        """
        insert Document {
            title := <str>$title,
            folder := assert_single((
                select Folder
                filter .name = <str>$folder and .workspace.name = <str>$workspace
            )),
            attachments := (
                select Attachment filter contains(<array<str>>$atts, .filename)
            ),
            checked_out_by := assert_single((
                select Editor filter .email = <optional str>$editor
            ))
        }
        """,
        title=title,
        folder=folder,
        workspace=workspace,
        atts=list(attachments),
        editor=editor,
    )


def add_archived_record(client, label, document_title):
    client.execute(
        """
        insert ArchivedRecord {
            label := <str>$label,
            document := assert_single((
                select Document filter .title = <str>$title
            ))
        }
        """,
        label=label,
        title=document_title,
    )


def names(client, expr):
    return sorted(client.query(expr))


def workspace_names(client):
    return names(client, "select Workspace.name")


def folder_names(client):
    return names(client, "select Folder.name")


def document_titles(client):
    return names(client, "select Document.title")


def attachment_filenames(client):
    return names(client, "select Attachment.filename")


def editor_emails(client):
    return names(client, "select Editor.email")


def counts(client):
    return client.query_single(
        """
        select {
            workspaces := count(Workspace),
            folders := count(Folder),
            documents := count(Document),
            attachments := count(Attachment),
            editors := count(Editor),
            records := count(ArchivedRecord)
        }
        """
    )


def counts_tuple(client):
    c = counts(client)
    return (
        c.workspaces,
        c.folders,
        c.documents,
        c.attachments,
        c.editors,
        c.records,
    )


def run_async(coro_factory):
    """Run an async scenario against a fresh async client."""

    async def _main():
        async_client = gel.create_async_client()
        try:
            return await coro_factory(async_client)
        finally:
            await async_client.aclose()

    return asyncio.run(_main())


def seed_two_workspaces(client):
    """workspace 'acme' (folders legal/specs) + workspace 'globex' (folder misc)."""
    add_workspace(client, "acme")
    add_workspace(client, "globex")
    add_folder(client, "legal", "acme")
    add_folder(client, "specs", "acme")
    add_folder(client, "misc", "globex")


def seed_graph_with_attachments(client):
    """Full graph used by the attachment / purge scenarios."""
    seed_two_workspaces(client)
    add_attachment(client, "shared.pdf", 11)
    add_attachment(client, "solo.pdf", 22)
    add_attachment(client, "lonely.pdf", 33)
    add_attachment(client, "spec.pdf", 44)
    add_document(
        client, "contract", "legal", "acme", attachments=["shared.pdf", "solo.pdf"]
    )
    add_document(client, "nda", "legal", "acme", attachments=["lonely.pdf"])
    add_document(client, "api-spec", "specs", "acme", attachments=["spec.pdf"])
    add_document(client, "memo", "misc", "globex", attachments=["shared.pdf"])


def seed_checked_out(client):
    add_editor(client, "ed@example.com")
    add_workspace(client, "acme")
    add_folder(client, "legal", "acme")
    add_document(client, "contract", "legal", "acme", editor="ed@example.com")
    add_document(client, "nda", "legal", "acme", editor="ed@example.com")


# ---------------------------------------------------------------- migrations


def test_migration_history_applied_and_in_sync(client):
    assert os.path.isdir(MIGRATIONS_DIR), (
        f"Expected the migration history directory {MIGRATIONS_DIR} to exist."
    )
    migration_files = [
        f for f in os.listdir(MIGRATIONS_DIR) if f.endswith(".edgeql")
    ]
    assert migration_files, (
        f"{MIGRATIONS_DIR} contains no migration files; the schema must be "
        "delivered through the migration history."
    )
    proc = subprocess.run(
        ["gel", "migration", "status"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=180,
    )
    combined = (proc.stdout + proc.stderr).lower()
    assert proc.returncode == 0, (
        f"'gel migration status' reported a problem: stdout={proc.stdout} "
        f"stderr={proc.stderr}"
    )
    assert "up to date" in combined, (
        f"The database is not in sync with the migration history: {combined!r}"
    )


# ---------------------------------------------------------------- schema shape


def test_schema_types_and_pointers(client):
    rows = client.query(
        """
        select schema::ObjectType {
            name,
            pointers: {
                name,
                required,
                card := <str>.cardinality,
                target_name := .target.name
            }
        }
        filter .name in array_unpack(<array<str>>$names)
        """,
        names=TYPE_NAMES,
    )
    found = {row.name: row for row in rows}
    for type_name in TYPE_NAMES:
        assert type_name in found, (
            f"Object type {type_name} is missing from the applied schema. "
            f"Found: {sorted(found)}"
        )

    expected = {
        "default::Workspace": {
            "name": (True, "One", "std::str"),
        },
        "default::Folder": {
            "name": (True, "One", "std::str"),
            "workspace": (True, "One", "default::Workspace"),
        },
        "default::Attachment": {
            "filename": (True, "One", "std::str"),
            "byte_size": (True, "One", "std::int64"),
        },
        "default::Editor": {
            "email": (True, "One", "std::str"),
        },
        "default::Document": {
            "title": (True, "One", "std::str"),
            "folder": (True, "One", "default::Folder"),
            "attachments": (False, "Many", "default::Attachment"),
            "checked_out_by": (False, "One", "default::Editor"),
        },
        "default::ArchivedRecord": {
            "label": (True, "One", "std::str"),
            "document": (True, "One", "default::Document"),
            "archived_at": (True, "One", "std::datetime"),
        },
    }

    for type_name, pointers in expected.items():
        actual = {p.name: p for p in found[type_name].pointers}
        for pointer_name, (required, card, target) in pointers.items():
            assert pointer_name in actual, (
                f"{type_name} is missing '{pointer_name}'. "
                f"Found pointers: {sorted(actual)}"
            )
            ptr = actual[pointer_name]
            assert ptr.target_name == target, (
                f"{type_name}.{pointer_name} should target {target}, "
                f"got {ptr.target_name}."
            )
            assert ptr.card == card, (
                f"{type_name}.{pointer_name} should have cardinality {card}, "
                f"got {ptr.card}."
            )
            if required:
                assert ptr.required, (
                    f"{type_name}.{pointer_name} must be required."
                )


def test_unique_constraints(client):
    add_workspace(client, "acme")
    with pytest.raises(gel.errors.ConstraintViolationError):
        add_workspace(client, "acme")

    add_attachment(client, "shared.pdf", 11)
    with pytest.raises(gel.errors.ConstraintViolationError):
        add_attachment(client, "shared.pdf", 99)

    add_editor(client, "ed@example.com")
    with pytest.raises(gel.errors.ConstraintViolationError):
        add_editor(client, "ed@example.com")

    add_folder(client, "legal", "acme")
    add_document(client, "contract", "legal", "acme")
    add_document(client, "nda", "legal", "acme")
    add_archived_record(client, "AR-1", "contract")
    with pytest.raises(gel.errors.ConstraintViolationError):
        add_archived_record(client, "AR-1", "nda")


def test_archived_at_is_populated_automatically(client):
    add_workspace(client, "acme")
    add_folder(client, "legal", "acme")
    add_document(client, "contract", "legal", "acme")
    add_archived_record(client, "AR-1", "contract")
    stamped = client.query_single(
        "select count((select ArchivedRecord filter exists .archived_at))"
    )
    assert stamped == 1, (
        "ArchivedRecord.archived_at must be filled in automatically when the "
        f"record is inserted without it (records with a value: {stamped})."
    )


# ---------------------------------------------------------------- cascades


def test_workspace_delete_cascades_to_folders_and_documents(client):
    seed_two_workspaces(client)
    add_document(client, "contract", "legal", "acme")
    add_document(client, "nda", "legal", "acme")
    add_document(client, "api-spec", "specs", "acme")
    add_document(client, "memo", "misc", "globex")

    client.execute('delete Workspace filter .name = "acme"')

    assert workspace_names(client) == ["globex"], (
        f"Only 'globex' should remain, got {workspace_names(client)}."
    )
    assert folder_names(client) == ["misc"], (
        "Deleting workspace 'acme' must remove its folders 'legal' and 'specs'; "
        f"remaining folders: {folder_names(client)}."
    )
    assert document_titles(client) == ["memo"], (
        "Deleting workspace 'acme' must remove all documents in its folders; "
        f"remaining documents: {document_titles(client)}."
    )


def test_folder_delete_cascades_to_documents_only(client):
    seed_two_workspaces(client)
    add_document(client, "contract", "legal", "acme")
    add_document(client, "nda", "legal", "acme")
    add_document(client, "api-spec", "specs", "acme")
    add_document(client, "memo", "misc", "globex")

    client.execute('delete Folder filter .name = "legal"')

    assert document_titles(client) == ["api-spec", "memo"], (
        "Deleting folder 'legal' must remove 'contract' and 'nda' only; "
        f"remaining documents: {document_titles(client)}."
    )
    assert folder_names(client) == ["misc", "specs"], (
        f"Folders 'specs' and 'misc' must survive, got {folder_names(client)}."
    )
    assert workspace_names(client) == ["acme", "globex"], (
        f"Both workspaces must survive, got {workspace_names(client)}."
    )


# ---------------------------------------------------------------- attachment GC


def test_orphan_attachments_removed_on_direct_document_delete(client):
    seed_graph_with_attachments(client)

    client.execute('delete Document filter .title = "contract"')

    remaining = attachment_filenames(client)
    assert "solo.pdf" not in remaining, (
        "'solo.pdf' was only linked by 'contract' and must be deleted with it; "
        f"remaining attachments: {remaining}."
    )
    assert "shared.pdf" in remaining, (
        "'shared.pdf' is still linked by 'memo' and must survive; "
        f"remaining attachments: {remaining}."
    )
    assert "lonely.pdf" in remaining, (
        f"'lonely.pdf' belongs to 'nda' and must survive; remaining: {remaining}."
    )
    assert document_titles(client) == ["api-spec", "memo", "nda"], (
        f"Only 'contract' should be gone, got {document_titles(client)}."
    )


def test_orphan_attachments_removed_through_workspace_cascade(client):
    seed_graph_with_attachments(client)

    client.execute('delete Workspace filter .name = "acme"')

    remaining = attachment_filenames(client)
    assert remaining == ["shared.pdf"], (
        "Cascading the purge of 'acme' must delete 'solo.pdf', 'lonely.pdf' and "
        "'spec.pdf' while keeping 'shared.pdf' (still linked by 'memo'); "
        f"remaining attachments: {remaining}."
    )
    memo_attachments = client.query(
        'select Document { atts := .attachments.filename } filter .title = "memo"'
    )
    assert len(memo_attachments) == 1, (
        f"Document 'memo' must survive the purge of 'acme', got {memo_attachments}."
    )
    assert sorted(memo_attachments[0].atts) == ["shared.pdf"], (
        "Document 'memo' must still link 'shared.pdf' after the cascade, got "
        f"{sorted(memo_attachments[0].atts)}."
    )


# ---------------------------------------------------------------- archived protection


def test_archived_document_cannot_be_deleted_directly(client):
    add_workspace(client, "acme")
    add_folder(client, "legal", "acme")
    add_document(client, "contract", "legal", "acme")
    add_archived_record(client, "AR-1", "contract")

    with pytest.raises(gel.errors.ConstraintViolationError):
        client.execute('delete Document filter .title = "contract"')

    assert document_titles(client) == ["contract"], (
        "The archived document must survive the refused deletion, got "
        f"{document_titles(client)}."
    )
    assert folder_names(client) == ["legal"], (
        f"Folder 'legal' must still exist, got {folder_names(client)}."
    )
    assert client.query_single("select count(ArchivedRecord)") == 1, (
        "The ArchivedRecord must still exist after the refused deletion."
    )


def test_archived_document_blocks_folder_and_workspace_cascade(client):
    add_workspace(client, "acme")
    add_folder(client, "legal", "acme")
    add_document(client, "contract", "legal", "acme")
    add_archived_record(client, "AR-1", "contract")

    with pytest.raises(gel.errors.ConstraintViolationError):
        client.execute('delete Folder filter .name = "legal"')
    with pytest.raises(gel.errors.ConstraintViolationError):
        client.execute('delete Workspace filter .name = "acme"')

    assert workspace_names(client) == ["acme"], (
        f"Workspace 'acme' must survive, got {workspace_names(client)}."
    )
    assert folder_names(client) == ["legal"], (
        f"Folder 'legal' must survive, got {folder_names(client)}."
    )
    assert document_titles(client) == ["contract"], (
        f"Document 'contract' must survive, got {document_titles(client)}."
    )
    assert client.query_single("select count(ArchivedRecord)") == 1, (
        "The ArchivedRecord must survive the refused cascades."
    )

    client.execute("delete ArchivedRecord")
    client.execute('delete Workspace filter .name = "acme"')

    assert workspace_names(client) == [], (
        "Once the ArchivedRecord is gone the workspace purge must succeed, "
        f"remaining workspaces: {workspace_names(client)}."
    )
    assert folder_names(client) == [], (
        f"The folder must be cascaded away, got {folder_names(client)}."
    )
    assert document_titles(client) == [], (
        f"The document must be cascaded away, got {document_titles(client)}."
    )


# ---------------------------------------------------------------- deferred protection


def test_editor_protection_is_deferred_until_commit(client):
    seed_checked_out(client)

    async def scenario(async_client):
        outcome: dict = {"delete_error": None, "commit_error": None}
        try:
            async for tx in async_client.transaction():
                async with tx:
                    try:
                        await tx.execute(
                            'delete Editor filter .email = "ed@example.com";'
                        )
                    except Exception as exc:  # noqa: BLE001
                        outcome["delete_error"] = repr(exc)
                        raise
                    await tx.execute(
                        "update Document set { checked_out_by := {} };"
                    )
        except Exception as exc:  # noqa: BLE001
            outcome["commit_error"] = repr(exc)
        return outcome

    outcome = run_async(scenario)

    assert outcome["delete_error"] is None, (
        "Deleting a still-referenced Editor must not fail while the transaction "
        f"is open, but the delete statement raised: {outcome['delete_error']}"
    )
    assert outcome["commit_error"] is None, (
        "A transaction that deletes the Editor and then releases the referencing "
        "documents must commit successfully, but it failed with: "
        f"{outcome['commit_error']}"
    )
    assert editor_emails(client) == [], (
        f"The editor must be gone after the successful commit, got "
        f"{editor_emails(client)}."
    )
    assert document_titles(client) == ["contract", "nda"], (
        f"Both documents must survive, got {document_titles(client)}."
    )
    still_linked = client.query_single(
        "select count((select Document filter exists .checked_out_by))"
    )
    assert still_linked == 0, (
        f"No document may still reference the deleted editor, got {still_linked}."
    )


def test_editor_deletion_fails_at_commit_when_still_referenced(client):
    seed_checked_out(client)

    async def scenario(async_client):
        outcome: dict = {"delete_error": None, "commit_error": None}
        try:
            async for tx in async_client.transaction():
                async with tx:
                    try:
                        await tx.execute(
                            'delete Editor filter .email = "ed@example.com";'
                        )
                    except Exception as exc:  # noqa: BLE001
                        outcome["delete_error"] = exc
                        raise
        except Exception as exc:  # noqa: BLE001
            outcome["commit_error"] = exc
        return outcome

    outcome = run_async(scenario)

    assert outcome["delete_error"] is None, (
        "The failure must not be reported by the delete statement itself, but "
        f"it raised: {outcome['delete_error']!r}"
    )
    assert isinstance(outcome["commit_error"], gel.errors.ConstraintViolationError), (
        "Committing a transaction that leaves the deleted editor referenced must "
        "raise gel.errors.ConstraintViolationError, got "
        f"{outcome['commit_error']!r}"
    )
    assert editor_emails(client) == ["ed@example.com"], (
        f"The editor must survive the rolled-back transaction, got "
        f"{editor_emails(client)}."
    )
    assert document_titles(client) == ["contract", "nda"], (
        f"Both documents must survive, got {document_titles(client)}."
    )
    linked = client.query_single(
        "select count((select Document filter .checked_out_by.email "
        '= "ed@example.com"))'
    )
    assert linked == 2, (
        f"Both documents must still be checked out by the editor, got {linked}."
    )

    with pytest.raises(gel.errors.ConstraintViolationError):
        client.execute('delete Editor filter .email = "ed@example.com"')
    assert editor_emails(client) == ["ed@example.com"], (
        "A plain delete of a still-referenced editor must leave it in place, got "
        f"{editor_emails(client)}."
    )


def test_unreferenced_editor_can_be_deleted(client):
    add_editor(client, "free@example.com")
    add_workspace(client, "acme")
    add_folder(client, "legal", "acme")
    add_document(client, "contract", "legal", "acme")

    client.execute('delete Editor filter .email = "free@example.com"')

    assert editor_emails(client) == [], (
        f"The unreferenced editor must be deleted, got {editor_emails(client)}."
    )
    assert document_titles(client) == ["contract"], (
        "Deleting an editor must never delete documents, got "
        f"{document_titles(client)}."
    )


# ---------------------------------------------------------------- purge routine


def test_purge_workspace_happy_path(client, purge_module):
    seed_graph_with_attachments(client)

    result = run_async(lambda ac: purge_module.purge_workspace(ac, "acme"))

    assert result == {
        "workspace": "acme",
        "folders_deleted": 2,
        "documents_deleted": 3,
        "attachments_deleted": 3,
        "attachments_kept": 1,
    }, f"Unexpected purge report: {result!r}"

    assert workspace_names(client) == ["globex"], (
        f"Workspace 'acme' must be gone, got {workspace_names(client)}."
    )
    assert folder_names(client) == ["misc"], (
        f"Folders of 'acme' must be gone, got {folder_names(client)}."
    )
    assert document_titles(client) == ["memo"], (
        f"Documents of 'acme' must be gone, got {document_titles(client)}."
    )
    assert attachment_filenames(client) == ["shared.pdf"], (
        "Only the still-referenced 'shared.pdf' may survive, got "
        f"{attachment_filenames(client)}."
    )
    memo = client.query(
        'select Document { atts := .attachments.filename } filter .title = "memo"'
    )
    assert len(memo) == 1 and sorted(memo[0].atts) == ["shared.pdf"], (
        f"Document 'memo' must still link 'shared.pdf', got {memo!r}."
    )


def test_purge_workspace_unknown_name_raises_lookup_error(client, purge_module):
    seed_graph_with_attachments(client)
    before = counts_tuple(client)

    with pytest.raises(LookupError):
        run_async(lambda ac: purge_module.purge_workspace(ac, "nope"))

    assert counts_tuple(client) == before, (
        "Purging a non-existent workspace must not change anything: "
        f"before={before} after={counts_tuple(client)}."
    )


def test_purge_workspace_refused_purge_is_atomic(client, purge_module):
    seed_graph_with_attachments(client)
    add_archived_record(client, "AR-1", "nda")
    before = counts_tuple(client)

    with pytest.raises(gel.errors.ConstraintViolationError):
        run_async(lambda ac: purge_module.purge_workspace(ac, "acme"))

    assert counts_tuple(client) == before, (
        "A purge refused by the database must leave the graph untouched: "
        f"before={before} after={counts_tuple(client)}."
    )
    assert workspace_names(client) == ["acme", "globex"], (
        f"Both workspaces must survive, got {workspace_names(client)}."
    )
    assert folder_names(client) == ["legal", "misc", "specs"], (
        f"All folders must survive, got {folder_names(client)}."
    )
    assert document_titles(client) == ["api-spec", "contract", "memo", "nda"], (
        f"All documents must survive, got {document_titles(client)}."
    )
    assert attachment_filenames(client) == [
        "lonely.pdf",
        "shared.pdf",
        "solo.pdf",
        "spec.pdf",
    ], f"All attachments must survive, got {attachment_filenames(client)}."
    assert client.query_single("select count(ArchivedRecord)") == 1, (
        "The ArchivedRecord must survive the refused purge."
    )


def test_purge_workspace_reports_real_numbers(client, purge_module):
    add_workspace(client, "empty-ws")

    empty_result = run_async(lambda ac: purge_module.purge_workspace(ac, "empty-ws"))
    assert empty_result == {
        "workspace": "empty-ws",
        "folders_deleted": 0,
        "documents_deleted": 0,
        "attachments_deleted": 0,
        "attachments_kept": 0,
    }, f"Unexpected report for a workspace without folders: {empty_result!r}"
    assert workspace_names(client) == [], (
        f"'empty-ws' must be gone, got {workspace_names(client)}."
    )

    add_workspace(client, "solo-ws")
    add_workspace(client, "other-ws")
    add_folder(client, "only", "solo-ws")
    add_folder(client, "other", "other-ws")
    add_attachment(client, "team.pdf", 55)
    add_document(client, "doc-a", "only", "solo-ws", attachments=["team.pdf"])
    add_document(client, "doc-b", "other", "other-ws", attachments=["team.pdf"])

    solo_result = run_async(lambda ac: purge_module.purge_workspace(ac, "solo-ws"))
    assert solo_result == {
        "workspace": "solo-ws",
        "folders_deleted": 1,
        "documents_deleted": 1,
        "attachments_deleted": 0,
        "attachments_kept": 1,
    }, f"Unexpected report for 'solo-ws': {solo_result!r}"
    assert attachment_filenames(client) == ["team.pdf"], (
        "'team.pdf' is still linked by 'doc-b' and must survive, got "
        f"{attachment_filenames(client)}."
    )
    assert document_titles(client) == ["doc-b"], (
        f"Only 'doc-b' may remain, got {document_titles(client)}."
    )


def test_purge_workspace_keeps_editors(client, purge_module):
    seed_checked_out(client)

    result = run_async(lambda ac: purge_module.purge_workspace(ac, "acme"))

    assert result["documents_deleted"] == 2, (
        f"Both checked-out documents must be purged, got {result!r}."
    )
    assert editor_emails(client) == ["ed@example.com"], (
        "Purging documents must never delete editors, got "
        f"{editor_emails(client)}."
    )
