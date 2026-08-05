import asyncio
import gel


async def clear(client):
    await client.query("""
        delete default::ArchivedRecord;
        delete default::Document;
        delete default::Folder;
        delete default::Workspace;
        delete default::Attachment;
        delete default::Editor;
    """)


def ws(name):
    return f"assert_single((select default::Workspace filter .name = '{name}'))"


def folder(name):
    return f"assert_single((select default::Folder filter .name = '{name}'))"


def doc(title):
    return f"assert_single((select default::Document filter .title = '{title}'))"


def att(filename):
    return f"assert_single((select default::Attachment filter .filename = '{filename}'))"


async def run():
    client = gel.create_async_client()
    await clear(client)

    # Scenario 3: attachment A3 shared by D3a and D3b, BOTH in workspace W3.
    # Purging W3 deletes both docs => A3 orphaned => must be deleted.
    await client.query(f"""
        insert default::Workspace {{ name := "W3" }};
        insert default::Folder {{ name := "F3", workspace := {ws("W3")} }};
        insert default::Attachment {{ filename := "A3", byte_size := 1 }};
        insert default::Document {{ title := "D3a", folder := {folder("F3")}, attachments := {att("A3")} }};
        insert default::Document {{ title := "D3b", folder := {folder("F3")}, attachments := {att("A3")} }};
    """)
    await client.query("delete default::Workspace filter .name = 'W3';")
    a3 = await client.query_single("select exists (select default::Attachment filter .filename = 'A3');")
    print(f"S3 (shared within purged ws): A3 exists={a3} (expect False)")
    assert a3 is False, "S3 FAILED - attachment should be GC'd when both sharing docs are purged"

    # Scenario 4: attachment A4 linked by doc in purged W4 AND an archived doc in W4b.
    # Archived doc cannot be deleted, so it survives and keeps A4.
    await client.query(f"""
        insert default::Workspace {{ name := "W4" }};
        insert default::Workspace {{ name := "W4b" }};
        insert default::Folder {{ name := "F4", workspace := {ws("W4")} }};
        insert default::Folder {{ name := "F4b", workspace := {ws("W4b")} }};
        insert default::Attachment {{ filename := "A4", byte_size := 1 }};
        insert default::Document {{ title := "D4", folder := {folder("F4")}, attachments := {att("A4")} }};
        insert default::Document {{ title := "D4b", folder := {folder("F4b")}, attachments := {att("A4")} }};
        insert default::ArchivedRecord {{ label := "AR4", document := {doc("D4b")} }};
    """)
    # Purging W4 deletes D4 but D4b is archived (in another ws) and survives => A4 kept
    await client.query("delete default::Workspace filter .name = 'W4';")
    a4 = await client.query_single("select exists (select default::Attachment filter .filename = 'A4');")
    d4b = await client.query_single("select exists (select default::Document filter .title = 'D4b');")
    d4b_links_a4 = await client.query_single("select exists (select default::Document filter .title = 'D4b' and 'A4' in .attachments.filename);")
    print(f"S4 (shared with archived doc in other ws): A4={a4} D4b={d4b} D4b->A4={d4b_links_a4} (expect True,True,True)")
    assert (a4, d4b, d4b_links_a4) == (True, True, True), "S4 FAILED"
    # cleanup (must remove archive before purging W4b)
    await client.query("delete default::ArchivedRecord filter .label = 'AR4';")
    await client.query("delete default::Workspace filter .name = 'W4b';")

    # Scenario 5: purging a workspace whose folder contains an archived doc must FAIL entirely.
    await client.query(f"""
        insert default::Workspace {{ name := "W5" }};
        insert default::Folder {{ name := "F5", workspace := {ws("W5")} }};
        insert default::Document {{ title := "D5", folder := {folder("F5")} }};
        insert default::ArchivedRecord {{ label := "AR5", document := {doc("D5")} }};
        insert default::Attachment {{ filename := "A5", byte_size := 1 }};
        insert default::Document {{ title := "D5b", folder := {folder("F5")}, attachments := {att("A5")} }};
    """)
    try:
        await client.query("delete default::Workspace filter .name = 'W5';")
        print("S5: NO ERROR (BAD!)")
        assert False, "S5 FAILED"
    except gel.errors.ConstraintViolationError:
        print("S5: purge of ws with archived doc raises ConstraintViolationError OK")
    # verify nothing changed
    w5 = await client.query_single("select exists (select default::Workspace filter .name = 'W5');")
    d5 = await client.query_single("select exists (select default::Document filter .title = 'D5');")
    a5 = await client.query_single("select exists (select default::Attachment filter .filename = 'A5');")
    ar5 = await client.query_single("select exists (select default::ArchivedRecord filter .label = 'AR5');")
    print(f"S5 unchanged: W5={w5} D5={d5} A5={a5} AR5={ar5} (expect all True)")
    assert all([w5, d5, a5, ar5]), "S5 FAILED - DB changed despite error"
    await client.query("delete default::ArchivedRecord filter .label = 'AR5';")
    await client.query("delete default::Workspace filter .name = 'W5';")

    await client.aclose()
    print("ALL GC TESTS PASSED")


asyncio.run(run())
