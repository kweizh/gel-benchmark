import asyncio
import gel
import pytest
from purge import purge_workspace

async def clear_db(client):
    async for tx in client.transaction():
        async with tx:
            await tx.execute("delete ArchivedRecord;")
            await tx.execute("delete Document;")
            await tx.execute("delete Folder;")
            await tx.execute("delete Workspace;")
            await tx.execute("delete Attachment;")
            await tx.execute("delete Editor;")

async def run_tests():
    client = gel.create_async_client()
    print("Connecting to Gel database...")
    
    # Check connection
    await client.query("select 1")
    print("Connected successfully.")

    # -------------------------------------------------------------
    # Test Case 1: Workspace purge raises LookupError if workspace doesn't exist
    # -------------------------------------------------------------
    print("\n--- Running Test Case 1: LookupError for nonexistent workspace ---")
    await clear_db(client)
    try:
        await purge_workspace(client, "nonexistent")
        assert False, "Should have raised LookupError"
    except LookupError as e:
        print("Success: Raised LookupError as expected:", e)

    # -------------------------------------------------------------
    # Test Case 2: Basic purge with cascading delete
    # -------------------------------------------------------------
    print("\n--- Running Test Case 2: Basic purge with cascading delete ---")
    await clear_db(client)
    async for tx in client.transaction():
        async with tx:
            # Create workspace, folders, documents, attachments
            await tx.execute("""
                insert Workspace { name := 'W1' };
            """)
            await tx.execute("""
                insert Folder { name := 'F1', workspace := (select Workspace filter .name = 'W1' limit 1) };
                insert Folder { name := 'F2', workspace := (select Workspace filter .name = 'W1' limit 1) };
            """)
            await tx.execute("""
                insert Attachment { filename := 'A1', byte_size := 100 };
                insert Attachment { filename := 'A2', byte_size := 200 };
            """)
            await tx.execute("""
                insert Document {
                    title := 'D1',
                    folder := (select Folder filter .name = 'F1' limit 1),
                    attachments := (select Attachment filter .filename = 'A1')
                };
                insert Document {
                    title := 'D2',
                    folder := (select Folder filter .name = 'F2' limit 1),
                    attachments := (select Attachment filter .filename = 'A2')
                };
            """)

    # Verify counts before purge
    folders_before = await client.query("select count(Folder)")
    docs_before = await client.query("select count(Document)")
    attachments_before = await client.query("select count(Attachment)")
    assert folders_before[0] == 2
    assert docs_before[0] == 2
    assert attachments_before[0] == 2

    # Purge workspace
    result = await purge_workspace(client, "W1")
    print("Purge result:", result)
    assert result["workspace"] == "W1"
    assert result["folders_deleted"] == 2
    assert result["documents_deleted"] == 2
    assert result["attachments_deleted"] == 2
    assert result["attachments_kept"] == 0

    # Verify database is empty
    workspaces_after = await client.query("select count(Workspace)")
    folders_after = await client.query("select count(Folder)")
    docs_after = await client.query("select count(Document)")
    attachments_after = await client.query("select count(Attachment)")
    assert workspaces_after[0] == 0
    assert folders_after[0] == 0
    assert docs_after[0] == 0
    assert attachments_after[0] == 0
    print("Success: Basic purge cascaded and deleted everything.")

    # -------------------------------------------------------------
    # Test Case 3: Shared attachments (garbage collection and keeping)
    # -------------------------------------------------------------
    print("\n--- Running Test Case 3: Shared attachments (GC and keeping) ---")
    await clear_db(client)
    async for tx in client.transaction():
        async with tx:
            await tx.execute("insert Workspace { name := 'W1' };")
            await tx.execute("insert Workspace { name := 'W2' };")
            await tx.execute("insert Folder { name := 'F1', workspace := (select Workspace filter .name = 'W1' limit 1) };")
            await tx.execute("insert Folder { name := 'F2', workspace := (select Workspace filter .name = 'W2' limit 1) };")
            await tx.execute("insert Attachment { filename := 'A1', byte_size := 100 };")
            await tx.execute("""
                insert Document {
                    title := 'D1',
                    folder := (select Folder filter .name = 'F1' limit 1),
                    attachments := (select Attachment filter .filename = 'A1')
                };
                insert Document {
                    title := 'D2',
                    folder := (select Folder filter .name = 'F2' limit 1),
                    attachments := (select Attachment filter .filename = 'A1')
                };
            """)

    # Purge W1
    result = await purge_workspace(client, "W1")
    print("Purge result:", result)
    assert result["workspace"] == "W1"
    assert result["folders_deleted"] == 1
    assert result["documents_deleted"] == 1
    assert result["attachments_deleted"] == 0
    assert result["attachments_kept"] == 1

    # Verify W2, F2, D2, A1 still exist
    workspaces = await client.query("select Workspace.name")
    folders = await client.query("select Folder.name")
    docs = await client.query("select Document.title")
    attachments = await client.query("select Attachment.filename")
    assert set(workspaces) == {"W2"}
    assert set(folders) == {"F2"}
    assert set(docs) == {"D2"}
    assert set(attachments) == {"A1"}
    print("Success: Shared attachment was kept because D2 still references it.")

    # -------------------------------------------------------------
    # Test Case 4: Archived documents protection
    # -------------------------------------------------------------
    print("\n--- Running Test Case 4: Archived documents protection ---")
    await clear_db(client)
    async for tx in client.transaction():
        async with tx:
            await tx.execute("insert Workspace { name := 'W1' };")
            await tx.execute("insert Folder { name := 'F1', workspace := (select Workspace filter .name = 'W1' limit 1) };")
            await tx.execute("insert Document { title := 'D1', folder := (select Folder filter .name = 'F1' limit 1) };")
            await tx.execute("""
                insert ArchivedRecord {
                    label := 'Archived D1',
                    document := (select Document filter .title = 'D1' limit 1)
                };
            """)

    # Try to purge W1. It must raise ConstraintViolationError and make no changes.
    try:
        await purge_workspace(client, "W1")
        assert False, "Should have failed with ConstraintViolationError"
    except gel.errors.ConstraintViolationError as e:
        print("Success: Raised ConstraintViolationError as expected:", e)

    # Verify database was unchanged
    workspaces = await client.query("select count(Workspace)")
    folders = await client.query("select count(Folder)")
    docs = await client.query("select count(Document)")
    archived = await client.query("select count(ArchivedRecord)")
    assert workspaces[0] == 1
    assert folders[0] == 1
    assert docs[0] == 1
    assert archived[0] == 1
    print("Success: Database is completely unchanged after failed purge.")

    # -------------------------------------------------------------
    # Test Case 5: Editor checked-out protection (deferred restrict)
    # -------------------------------------------------------------
    print("\n--- Running Test Case 5: Editor checked-out protection ---")
    await clear_db(client)
    async for tx in client.transaction():
        async with tx:
            await tx.execute("insert Editor { email := 'editor1@test.com' };")
            await tx.execute("insert Workspace { name := 'W1' };")
            await tx.execute("insert Folder { name := 'F1', workspace := (select Workspace filter .name = 'W1' limit 1) };")
            await tx.execute("""
                insert Document {
                    title := 'D1',
                    folder := (select Folder filter .name = 'F1' limit 1),
                    checked_out_by := (select Editor filter .email = 'editor1@test.com' limit 1)
                };
            """)

    # Part A: Try to delete editor directly without clearing reference.
    # This must fail at transaction commit time.
    print("Part A: Deleting editor directly without clearing reference...")
    try:
        async for tx in client.transaction():
            async with tx:
                await tx.execute("delete Editor filter .email = 'editor1@test.com';")
        assert False, "Should have raised ConstraintViolationError"
    except gel.errors.ConstraintViolationError as e:
        print("Success: Raised ConstraintViolationError at commit as expected:", e)

    # Part B: Delete editor and clear reference in the same transaction.
    # This must succeed.
    print("Part B: Deleting editor and clearing reference in same transaction...")
    async for tx in client.transaction():
        async with tx:
            await tx.execute("delete Editor filter .email = 'editor1@test.com';")
            await tx.execute("update Document filter .title = 'D1' set { checked_out_by := {} };")
    
    # Verify editor is deleted and document's checked_out_by is empty
    editors = await client.query("select count(Editor)")
    assert editors[0] == 0
    docs = await client.query("select Document { title, checked_out_by: { email } }")
    assert docs[0].checked_out_by is None
    print("Success: Deleting editor and clearing reference in the same transaction succeeded.")

    # Part C: Recreate editor and reference, then delete workspace (which cascades to document) and editor in same transaction.
    # This must succeed.
    print("Part C: Deleting editor and workspace (cascading to doc) in same transaction...")
    async for tx in client.transaction():
        async with tx:
            await tx.execute("insert Editor { email := 'editor1@test.com' };")
            await tx.execute("""
                update Document filter .title = 'D1' set {
                    checked_out_by := (select Editor filter .email = 'editor1@test.com' limit 1)
                };
            """)
    
    # Now delete editor and workspace
    async for tx in client.transaction():
        async with tx:
            await tx.execute("delete Editor filter .email = 'editor1@test.com';")
            await tx.execute("delete Workspace filter .name = 'W1';")

    # Verify everything is gone
    editors = await client.query("select count(Editor)")
    workspaces = await client.query("select count(Workspace)")
    docs = await client.query("select count(Document)")
    assert editors[0] == 0
    assert workspaces[0] == 0
    assert docs[0] == 0
    print("Success: Deleting editor and cascading document in the same transaction succeeded.")

    await clear_db(client)
    await client.aclose()
    print("\nAll tests passed successfully!")

if __name__ == "__main__":
    asyncio.run(run_tests())
