import asyncio
import gel
import gel.errors


def ws(name):
    return f"assert_single((select default::Workspace filter .name = '{name}'))"


def folder(name):
    return f"assert_single((select default::Folder filter .name = '{name}'))"


def doc(title):
    return f"assert_single((select default::Document filter .title = '{title}'))"


def editor(email):
    return f"assert_single((select default::Editor filter .email = '{email}'))"


def att(filename):
    return f"assert_single((select default::Attachment filter .filename = '{filename}'))"


async def clear(client):
    await client.query("""
        delete default::ArchivedRecord;
        delete default::Document;
        delete default::Folder;
        delete default::Workspace;
        delete default::Attachment;
        delete default::Editor;
    """)


async def run():
    client = gel.create_async_client()
    await clear(client)
    print("=== slate cleared ===")

    # ---- Test 1: container purge cascades ----
    await client.query(f"""
        insert default::Workspace {{ name := "W1" }};
        insert default::Folder {{ name := "F1", workspace := {ws("W1")} }};
        insert default::Document {{ title := "D1", folder := {folder("F1")} }};
    """)
    await client.query("delete default::Workspace filter .name = 'W1';")
    n_folder = await client.query_single("select count(default::Folder);")
    n_doc = await client.query_single("select count(default::Document);")
    n_ws = await client.query_single("select count(default::Workspace);")
    print(f"T1 cascade: ws={n_ws} folder={n_folder} doc={n_doc} (expect 0,0,0)")
    assert (n_ws, n_folder, n_doc) == (0, 0, 0), "T1 FAILED"

    # folder cascade
    await client.query(f"""
        insert default::Workspace {{ name := "W1" }};
        insert default::Folder {{ name := "F1", workspace := {ws("W1")} }};
        insert default::Document {{ title := "D1", folder := {folder("F1")} }};
    """)
    await client.query("delete default::Folder filter .name = 'F1';")
    n_doc = await client.query_single("select count(default::Document);")
    n_folder = await client.query_single("select count(default::Folder filter .workspace.name = 'W1');")
    print(f"T1b folder cascade: folder={n_folder} doc={n_doc} (expect 0,0)")
    assert (n_folder, n_doc) == (0, 0), "T1b FAILED"
    await client.query("delete default::Workspace;")
    print("=== T1 PASS ===")

    # ---- Test 2: shared blob GC ----
    await client.query(f"""
        insert default::Workspace {{ name := "W1" }};
        insert default::Workspace {{ name := "W2" }};
        insert default::Folder {{ name := "F1", workspace := {ws("W1")} }};
        insert default::Folder {{ name := "F2", workspace := {ws("W2")} }};
        insert default::Attachment {{ filename := "A", byte_size := 100 }};
        insert default::Document {{
            title := "D1",
            folder := {folder("F1")},
            attachments := {att("A")},
        }};
        insert default::Document {{
            title := "D2",
            folder := {folder("F2")},
            attachments := {att("A")},
        }};
    """)
    # Delete W1 -> D1 deleted, A should survive (D2 still links)
    await client.query("delete default::Workspace filter .name = 'W1';")
    a_exists = await client.query_single("select exists (select default::Attachment filter .filename = 'A');")
    d2_exists = await client.query_single("select exists (select default::Document filter .title = 'D2');")
    d2_links_a = await client.query_single("select exists (select default::Document filter .title = 'D2' and 'A' in .attachments.filename);")
    print(f"T2 after W1 delete: A exists={a_exists} D2 exists={d2_exists} D2 links A={d2_links_a} (expect True,True,True)")
    assert (a_exists, d2_exists, d2_links_a) == (True, True, True), "T2 FAILED - shared blob wrongly deleted"
    # Now delete D2 -> A should be deleted (no surviving doc)
    await client.query("delete default::Document filter .title = 'D2';")
    a_exists = await client.query_single("select exists (select default::Attachment filter .filename = 'A');")
    print(f"T2 after D2 delete: A exists={a_exists} (expect False)")
    assert a_exists == False, "T2 FAILED - orphan blob not GC'd"
    await client.query("delete default::Workspace;")
    print("=== T2 PASS ===")

    # ---- Test 3: archived documents protected ----
    await client.query(f"""
        insert default::Workspace {{ name := "W1" }};
        insert default::Folder {{ name := "F1", workspace := {ws("W1")} }};
        insert default::Document {{ title := "D1", folder := {folder("F1")} }};
        insert default::ArchivedRecord {{ label := "AR1", document := {doc("D1")} }};
    """)
    for label, stmt in [
        ("delete Document", "delete default::Document filter .title = 'D1';"),
        ("delete Folder", "delete default::Folder filter .name = 'F1';"),
        ("delete Workspace", "delete default::Workspace filter .name = 'W1';"),
    ]:
        try:
            await client.query(stmt)
            print(f"T3 {label}: NO ERROR (BAD!)")
            assert False, f"T3 FAILED {label} did not raise"
        except gel.errors.ConstraintViolationError as e:
            print(f"T3 {label}: ConstraintViolationError OK")
        except Exception as e:
            print(f"T3 {label}: WRONG ERROR TYPE: {type(e).__name__}: {e}")
            assert False, f"T3 FAILED {label} wrong error"
    d1_exists = await client.query_single("select exists (select default::Document filter .title = 'D1');")
    f1_exists = await client.query_single("select exists (select default::Folder filter .name = 'F1');")
    w1_exists = await client.query_single("select exists (select default::Workspace filter .name = 'W1');")
    ar_exists = await client.query_single("select exists (select default::ArchivedRecord filter .label = 'AR1');")
    print(f"T3 unchanged: D1={d1_exists} F1={f1_exists} W1={w1_exists} AR1={ar_exists} (expect all True)")
    assert all([d1_exists, f1_exists, w1_exists, ar_exists]), "T3 FAILED - DB changed"
    await client.query("delete default::ArchivedRecord filter .label = 'AR1';")
    await client.query("delete default::Workspace filter .name = 'W1';")
    print("=== T3 PASS ===")

    # ---- Test 4: checked-out editors protected until end of txn ----
    await client.query(f"""
        insert default::Editor {{ email := "E1" }};
        insert default::Workspace {{ name := "W1" }};
        insert default::Folder {{ name := "F1", workspace := {ws("W1")} }};
        insert default::Document {{
            title := "D1",
            folder := {folder("F1")},
            checked_out_by := {editor("E1")},
        }};
    """)
    # 4a: delete editor + commit -> fail
    try:
        async for tx in client.transaction():
            async with tx:
                await tx.query("delete default::Editor filter .email = 'E1';")
        print("T4a: committed (BAD!)")
        assert False, "T4a FAILED"
    except gel.errors.ConstraintViolationError as e:
        print("T4a: ConstraintViolationError OK")
    except Exception as e:
        print(f"T4a: WRONG ERROR: {type(e).__name__}: {e}")
        assert False, "T4a FAILED wrong error"
    e_exists = await client.query_single("select exists (select default::Editor filter .email = 'E1');")
    d_links_e = await client.query_single("select exists (select default::Document filter .title = 'D1' and 'E1' in .checked_out_by.email);")
    print(f"T4a unchanged: E1={e_exists} D1->E1={d_links_e} (expect True,True)")
    assert e_exists and d_links_e, "T4a FAILED - DB changed"

    # 4b: delete editor then unlink documents in same txn -> commit
    async for tx in client.transaction():
        async with tx:
            await tx.query("delete default::Editor filter .email = 'E1';")
            await tx.query("update default::Document filter .title = 'D1' set { checked_out_by := {} };")
    e_exists = await client.query_single("select exists (select default::Editor filter .email = 'E1');")
    d_links_e = await client.query_single("select exists (select default::Document filter .title = 'D1' and exists(.checked_out_by));")
    print(f"T4b after txn: E1={e_exists} D1->editor={d_links_e} (expect False,False)")
    assert e_exists == False and d_links_e == False, "T4b FAILED"

    # 4c: deleting an unreferenced editor succeeds and doesn't touch documents
    await client.query(f"""
        insert default::Editor {{ email := "E2" }};
        insert default::Document {{
            title := "D2",
            folder := {folder("F1")},
        }};
    """)
    await client.query("delete default::Editor filter .email = 'E2';")
    d_count = await client.query_single("select count(default::Document);")
    print(f"T4c: doc count after deleting unreferenced editor = {d_count} (expect 2)")
    assert d_count == 2, "T4c FAILED"
    await client.query("delete default::Workspace;")
    print("=== T4 PASS ===")

    # ---- Test archived_at auto-populate ----
    await client.query(f"""
        insert default::Workspace {{ name := "W1" }};
        insert default::Folder {{ name := "F1", workspace := {ws("W1")} }};
        insert default::Document {{ title := "D1", folder := {folder("F1")} }};
        insert default::ArchivedRecord {{ label := "AR1", document := {doc("D1")} }};
    """)
    ts = await client.query_single("select (select default::ArchivedRecord filter .label = 'AR1').archived_at;")
    print(f"T5 archived_at auto = {ts} (expect not None)")
    assert ts is not None, "T5 FAILED"
    await client.query("delete default::ArchivedRecord;")
    await client.query("delete default::Workspace;")
    print("=== T5 PASS ===")

    await client.aclose()
    print("ALL TESTS PASSED")


asyncio.run(run())
