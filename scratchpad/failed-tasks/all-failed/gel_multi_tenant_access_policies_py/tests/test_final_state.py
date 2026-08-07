"""Final-state verification for gel_multi_tenant_access_policies_py.

Every check drives the real system: the local Gel 6 instance (through the `gel`
CLI and the `gel` Python client) and the real `app.tenant_gateway` coroutines.
"raw" clients only set the two globals, so they prove that the database itself
enforces isolation rather than the Python layer.
"""

import asyncio
import glob
import os
import re
import subprocess
import sys
import uuid

import pytest

PROJECT_DIR = "/home/user/mtsaas"
SCHEMA_DIR = os.path.join(PROJECT_DIR, "dbschema")
MIGRATIONS_DIR = os.path.join(SCHEMA_DIR, "migrations")
START_SCRIPT = "/usr/local/bin/gel-start.sh"

TENANT_TYPES = (
    "default::Tenant",
    "default::Workspace",
    "default::Document",
    "default::Comment",
)
ALL_ACCESS_KINDS = {"Select", "Insert", "UpdateRead", "UpdateWrite", "Delete"}

BAKED_TENANTS = {
    "acme": "Acme Corp",
    "globex": "Globex Inc",
    "initech": "Initech LLC",
}
BAKED_WORKSPACE_DOCS = {
    "alpha": {"Alpha Charter", "Alpha Roadmap"},
    "beta": {"Beta Notes"},
    "zeta-archived": {"Frozen Plan"},
    "gamma": {"Gamma Spec", "Gamma Budget"},
    "delta": set(),
    "omega": set(),
}
ACME_TITLES = {"Alpha Charter", "Alpha Roadmap", "Beta Notes", "Frozen Plan"}
ACME_COMMENTS = {
    "charter note one",
    "charter note two",
    "roadmap note one",
    "frozen note one",
}
GLOBEX_TITLES = {"Gamma Spec", "Gamma Budget"}

TOKEN = uuid.uuid4().hex[:10]
WS = "wsx" + TOKEN
AUTHOR = "writer@acme.test"
LONG_BODY = "x" * 300

# Values shared between ordered test cases.
STATE: dict = {}

if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

LOOP = asyncio.new_event_loop()


def run(coro):
    """Drive gateway coroutines from one single long-lived event loop."""
    return LOOP.run_until_complete(coro)


def rand_title(tag):
    return "doc {} {}".format(tag, uuid.uuid4().hex[:12])


def rand_body():
    return "body " + uuid.uuid4().hex[:16]


def rand_comments(count):
    return ["comment{}{}".format(i, uuid.uuid4().hex[:12]) for i in range(count)]


def need(key):
    assert key in STATE, (
        f"Internal fixture value {key!r} is missing because an earlier test in "
        "this file did not complete successfully."
    )
    return STATE[key]


@pytest.fixture(scope="session")
def gel_server():
    """Start (or confirm) the local Gel server before any DB or CLI usage."""
    assert os.path.isfile(START_SCRIPT), f"{START_SCRIPT} is missing from the image."
    proc = subprocess.run([START_SCRIPT], capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, (
        f"{START_SCRIPT} failed (rc={proc.returncode}).\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    return True


@pytest.fixture(scope="session")
def raw(gel_server):
    """A plain Gel client; scopes are derived from it with with_globals()."""
    import gel

    client = gel.create_client()
    try:
        client.ensure_connected()
    except Exception as exc:  # pragma: no cover - environment failure
        pytest.fail(f"Could not connect to the local Gel instance: {exc!r}")
    yield client
    client.close()


@pytest.fixture(scope="session")
def gateway(gel_server):
    try:
        import app.tenant_gateway as module
    except Exception as exc:
        pytest.fail(f"Could not import app.tenant_gateway from {PROJECT_DIR}: {exc!r}")
    return module


def scope(raw_client, tenant_slug, role):
    globals_ = {"current_actor_role": role}
    if tenant_slug is not None:
        globals_["current_tenant_slug"] = tenant_slug
    return raw_client.with_globals(globals_)


def counts(client):
    row = client.query_single(
        """
        select {
            tenants := count(Tenant),
            workspaces := count(Workspace),
            documents := count(Document),
            comments := count(Comment),
        }
        """
    )
    return (row.tenants, row.workspaces, row.documents, row.comments)


def doc_id(client, title):
    rows = client.query(
        "select Document { id } filter .title = <str>$title", title=title
    )
    assert len(rows) == 1, (
        f"Expected exactly one document titled {title!r} to be readable in this "
        f"scope, found {len(rows)}."
    )
    return str(rows[0].id)


def count_documents_titled(client, title):
    return client.query_single(
        "select count((select Document filter .title = <str>$title))", title=title
    )


def count_comments_with_bodies(client, bodies):
    return client.query_single(
        """
        select count((
            select Comment filter .body in array_unpack(<array<str>>$bodies)
        ))
        """,
        bodies=list(bodies),
    )


def workspace_entry(entries, name):
    matches = [entry for entry in entries if entry.get("name") == name]
    assert len(matches) == 1, (
        f"Expected exactly one workspace entry named {name!r}, got "
        f"{[entry.get('name') for entry in entries]}."
    )
    return matches[0]


# ---------------------------------------------------------------- 1. API surface


def test_gateway_module_surface(gateway):
    for name in (
        "list_workspaces",
        "get_document",
        "create_document",
        "rename_document",
        "delete_document",
        "archive_workspace",
        "platform_document_counts",
    ):
        func = getattr(gateway, name, None)
        assert func is not None, f"app.tenant_gateway.{name} is not defined."
        assert asyncio.iscoroutinefunction(
            func
        ), f"app.tenant_gateway.{name} must be an async (coroutine) function."

    base = getattr(gateway, "TenantGatewayError", None)
    assert base is not None and issubclass(
        base, Exception
    ), "TenantGatewayError must be defined and derive from Exception."
    for name in (
        "TenantValidationError",
        "TenantObjectNotFound",
        "TenantAccessDenied",
    ):
        exc = getattr(gateway, name, None)
        assert exc is not None, f"app.tenant_gateway.{name} is not defined."
        assert issubclass(exc, base), f"{name} must derive from TenantGatewayError."


# ------------------------------------------------------------- 2. migration state


def test_migration_history_is_versioned_and_in_sync(gel_server, raw):
    scripts = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql")))
    assert len(scripts) >= 2, (
        "Expected at least 2 migration scripts in "
        f"{MIGRATIONS_DIR} (the baked one plus the new one), found {len(scripts)}: "
        f"{[os.path.basename(path) for path in scripts]}"
    )
    for path in scripts:
        name = os.path.basename(path)
        assert re.fullmatch(r"\d{5}-[a-z0-9]+\.edgeql", name), (
            f"Migration script {name!r} does not follow Gel's "
            "<5-digit index>-<hash>.edgeql naming."
        )

    proc = subprocess.run(
        ["gel", "migration", "status", "--schema-dir", SCHEMA_DIR],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, (
        "'gel migration status' reports the branch is not in sync with "
        f"{MIGRATIONS_DIR}.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )

    recorded = raw.query_single("select count(schema::Migration)")
    assert recorded == len(scripts), (
        f"The branch records {recorded} migrations but {len(scripts)} migration "
        "scripts exist on disk; every migration script must be applied."
    )


# --------------------------------------------------------------------- 3. globals


def test_globals_are_declared_in_schema(raw):
    rows = raw.query(
        "select schema::Global { name, required } filter .name like 'default::%'"
    )
    found = {row.name: row.required for row in rows}
    assert "default::current_tenant_slug" in found, (
        "The global 'current_tenant_slug' is not declared in module default; "
        f"found globals: {sorted(found)}."
    )
    assert found["default::current_tenant_slug"] is False, (
        "'current_tenant_slug' must be optional (required = false)."
    )
    assert "default::current_actor_role" in found, (
        "The global 'current_actor_role' is not declared in module default; "
        f"found globals: {sorted(found)}."
    )
    assert found["default::current_actor_role"] is True, (
        "'current_actor_role' must be declared required."
    )


def test_global_defaults(raw):
    role = raw.query_single("select global current_actor_role")
    assert role == "member", (
        "With no globals set, 'global current_actor_role' must default to "
        f"'member', got {role!r}."
    )
    slug = raw.query("select global current_tenant_slug")
    assert list(slug) == [], (
        "With no globals set, 'global current_tenant_slug' must be an empty set, "
        f"got {list(slug)!r}."
    )


# ------------------------------------------------------------- 5. policy coverage


def test_access_policies_cover_all_actions(raw):
    rows = raw.query(
        """
        select schema::ObjectType {
            name,
            policies := (select .access_policies { name, access_kinds })
        }
        filter .name in array_unpack(<array<str>>$names)
        """,
        names=list(TENANT_TYPES),
    )
    by_type = {row.name: row.policies for row in rows}
    assert set(by_type) == set(TENANT_TYPES), (
        f"Expected the object types {TENANT_TYPES} to still exist, found "
        f"{sorted(by_type)}."
    )
    for type_name, policies in by_type.items():
        assert len(policies) > 0, (
            f"{type_name} has no access policy; object-level security must be "
            "declared for all four types."
        )
        kinds = set()
        for policy in policies:
            for kind in policy.access_kinds:
                kinds.add(str(kind))
        if "All" in kinds:
            kinds |= ALL_ACCESS_KINDS
        missing = ALL_ACCESS_KINDS - kinds
        assert not missing, (
            f"The access policies of {type_name} do not cover {sorted(missing)}; "
            f"they only cover {sorted(kinds)}."
        )


# -------------------------------------------------------------- 6. computed links


def test_computed_links_declared(raw):
    rows = raw.query(
        """
        select schema::ObjectType {
            name,
            links := (
                select .pointers[is schema::Link] {
                    name,
                    cardinality,
                    target_name := .target.name
                }
            )
        }
        filter .name in {'default::Workspace', 'default::Document'}
        """
    )
    by_type = {row.name: {link.name: link for link in row.links} for row in rows}

    expectations = (
        ("default::Workspace", "documents", "default::Document"),
        ("default::Document", "comments", "default::Comment"),
    )
    for type_name, link_name, target in expectations:
        links = by_type.get(type_name, {})
        assert link_name in links, (
            f"{type_name} does not expose a link named {link_name!r}; found "
            f"{sorted(links)}."
        )
        link = links[link_name]
        assert str(link.cardinality) == "Many", (
            f"{type_name}.{link_name} must be a multi link, cardinality is "
            f"{str(link.cardinality)!r}."
        )
        assert link.target_name == target, (
            f"{type_name}.{link_name} must target {target}, targets "
            f"{link.target_name!r}."
        )


# -------------------------------------------------- 7-10. raw visibility contract


def test_unscoped_client_sees_nothing(raw):
    assert counts(raw) == (0, 0, 0, 0), (
        "A client with no globals set must not be able to read any Tenant, "
        f"Workspace, Document or Comment, got counts {counts(raw)}."
    )


def test_support_scope_sees_whole_platform(raw):
    support = scope(raw, None, "support")
    slugs = set(support.query("select Tenant.slug"))
    assert slugs == set(BAKED_TENANTS), (
        "A 'support' scope with no tenant slug must read every tenant, got "
        f"{sorted(slugs)}."
    )
    names = set(support.query("select Workspace.name"))
    assert set(BAKED_WORKSPACE_DOCS) <= names, (
        "A 'support' scope must read the workspaces of every tenant; missing "
        f"{sorted(set(BAKED_WORKSPACE_DOCS) - names)}."
    )
    titles = set(support.query("select Document.title"))
    expected_titles = set()
    for docs in BAKED_WORKSPACE_DOCS.values():
        expected_titles |= docs
    assert expected_titles <= titles, (
        "A 'support' scope must read the documents of every tenant; missing "
        f"{sorted(expected_titles - titles)}."
    )


def test_member_scope_reads_are_tenant_isolated(raw):
    acme = scope(raw, "acme", "member")
    assert set(acme.query("select Tenant.slug")) == {"acme"}, (
        "An 'acme' member scope must only read the acme tenant, got "
        f"{sorted(set(acme.query('select Tenant.slug')))}."
    )
    acme_ws = set(acme.query("select Workspace.name"))
    assert {"alpha", "beta", "zeta-archived"} <= acme_ws, (
        "An 'acme' member scope must read acme's workspaces, missing "
        f"{sorted({'alpha', 'beta', 'zeta-archived'} - acme_ws)}."
    )
    assert acme_ws & {"gamma", "delta", "omega"} == set(), (
        "An 'acme' member scope leaked workspaces of other tenants: "
        f"{sorted(acme_ws & {'gamma', 'delta', 'omega'})}."
    )
    acme_docs = set(acme.query("select Document.title"))
    assert ACME_TITLES <= acme_docs, (
        f"An 'acme' member scope must read acme's documents, missing "
        f"{sorted(ACME_TITLES - acme_docs)}."
    )
    assert acme_docs & GLOBEX_TITLES == set(), (
        "An 'acme' member scope leaked documents of another tenant: "
        f"{sorted(acme_docs & GLOBEX_TITLES)}."
    )
    acme_comments = set(acme.query("select Comment.body"))
    assert ACME_COMMENTS <= acme_comments, (
        f"An 'acme' member scope must read acme's comments, missing "
        f"{sorted(ACME_COMMENTS - acme_comments)}."
    )
    assert "spec note one" not in acme_comments, (
        "An 'acme' member scope leaked a comment of another tenant."
    )

    globex = scope(raw, "globex", "member")
    globex_ws = set(globex.query("select Workspace.name"))
    assert globex_ws == {"gamma", "delta"}, (
        f"A 'globex' member scope must read exactly globex's workspaces, got "
        f"{sorted(globex_ws)}."
    )
    globex_docs = set(globex.query("select Document.title"))
    assert globex_docs == GLOBEX_TITLES, (
        f"A 'globex' member scope must read exactly globex's documents, got "
        f"{sorted(globex_docs)}."
    )
    globex_comments = set(globex.query("select Comment.body"))
    assert globex_comments == {"spec note one"}, (
        f"A 'globex' member scope must read exactly globex's comments, got "
        f"{sorted(globex_comments)}."
    )


def test_unknown_role_sees_nothing(raw):
    intruder = scope(raw, "acme", "intruder")
    assert counts(intruder) == (0, 0, 0, 0), (
        "A scope whose role is neither member, auditor nor support must not read "
        f"anything, got counts {counts(intruder)}."
    )


# ----------------------------------------------------------- 11-13. list_workspaces


def test_list_workspaces_per_tenant_scope(gateway):
    entries = run(gateway.list_workspaces("acme", "member"))
    assert isinstance(entries, list) and entries, (
        f"list_workspaces('acme', 'member') must return a non-empty list, got "
        f"{entries!r}."
    )
    for entry in entries:
        assert set(entry) == {
            "name",
            "archived",
            "document_count",
            "comment_count",
        }, f"Unexpected keys in a list_workspaces entry: {sorted(entry)}."

    names = [entry["name"] for entry in entries]
    assert names == sorted(names), (
        f"list_workspaces must order entries by name ascending, got {names}."
    )
    for forbidden in ("gamma", "delta", "omega"):
        assert forbidden not in names, (
            f"list_workspaces('acme', 'member') leaked the workspace "
            f"{forbidden!r} of another tenant."
        )

    expected = {
        "alpha": (False, 2, 3),
        "beta": (False, 1, 0),
        "zeta-archived": (True, 1, 1),
    }
    for name, (archived, docs, comments) in expected.items():
        entry = workspace_entry(entries, name)
        assert (
            entry["archived"],
            entry["document_count"],
            entry["comment_count"],
        ) == (archived, docs, comments), (
            f"Wrong data for workspace {name!r}: expected archived={archived}, "
            f"document_count={docs}, comment_count={comments}, got {entry!r}."
        )

    globex = run(gateway.list_workspaces("globex", "member"))
    assert [
        (entry["name"], entry["archived"], entry["document_count"], entry["comment_count"])
        for entry in globex
    ] == [("delta", False, 0, 0), ("gamma", False, 2, 1)], (
        f"Unexpected list_workspaces('globex', 'member') result: {globex!r}."
    )

    initech = run(gateway.list_workspaces("initech", "member"))
    assert [
        (entry["name"], entry["archived"], entry["document_count"], entry["comment_count"])
        for entry in initech
    ] == [("omega", False, 0, 0)], (
        f"Unexpected list_workspaces('initech', 'member') result: {initech!r}."
    )


def test_auditor_reads_like_member(gateway):
    member = run(gateway.list_workspaces("acme", "member"))
    auditor = run(gateway.list_workspaces("acme", "auditor"))
    assert auditor == member, (
        "An 'auditor' must read exactly what a 'member' of the same tenant reads.\n"
        f"member: {member!r}\nauditor: {auditor!r}"
    )


def test_support_scope_lists_across_tenants(gateway):
    entries = run(gateway.list_workspaces("acme", "support"))
    names = {entry["name"] for entry in entries}
    for expected in ("alpha", "gamma", "omega"):
        assert expected in names, (
            "A 'support' scope must be able to read workspaces of every tenant; "
            f"{expected!r} is missing from {sorted(names)}."
        )


# ----------------------------------------------------------- 14-15. get_document


def test_get_document_scope_and_cross_tenant_visibility(gateway, raw):
    acme = scope(raw, "acme", "member")
    charter_id = doc_id(acme, "Alpha Charter")
    STATE["charter_id"] = charter_id

    doc = run(gateway.get_document("acme", "member", charter_id))
    assert set(doc) == {
        "id",
        "title",
        "body",
        "workspace_name",
        "comment_bodies",
    }, f"Unexpected keys from get_document: {sorted(doc)}."
    assert doc["id"] == charter_id, (
        f"get_document returned id {doc['id']!r} instead of {charter_id!r}."
    )
    assert doc["title"] == "Alpha Charter", (
        f"get_document returned title {doc['title']!r} instead of 'Alpha Charter'."
    )
    assert doc["workspace_name"] == "alpha", (
        f"get_document returned workspace_name {doc['workspace_name']!r} instead "
        "of 'alpha'."
    )
    assert doc["comment_bodies"] == ["charter note one", "charter note two"], (
        "get_document must return the readable comment bodies sorted ascending, "
        f"got {doc['comment_bodies']!r}."
    )

    globex = scope(raw, "globex", "member")
    gamma_id = doc_id(globex, "Gamma Spec")
    STATE["gamma_id"] = gamma_id

    with pytest.raises(gateway.TenantObjectNotFound):
        run(gateway.get_document("acme", "member", gamma_id))

    from_globex = run(gateway.get_document("globex", "member", gamma_id))
    assert from_globex["title"] == "Gamma Spec", (
        f"A globex member must read its own document, got {from_globex!r}."
    )
    from_support = run(gateway.get_document("acme", "support", gamma_id))
    assert from_support["title"] == "Gamma Spec", (
        "A 'support' scope must read documents of other tenants, got "
        f"{from_support!r}."
    )


def test_get_document_argument_validation(gateway):
    charter_id = need("charter_id")
    with pytest.raises(gateway.TenantValidationError):
        run(gateway.get_document("acme", "root", charter_id))
    with pytest.raises(gateway.TenantValidationError):
        run(gateway.get_document("acme", "member", "not-a-uuid"))
    with pytest.raises(gateway.TenantObjectNotFound):
        run(gateway.get_document("acme", "member", str(uuid.uuid4())))


# ------------------------------------------------- 16. platform_document_counts


def test_platform_document_counts_baseline_and_role_gate(gateway, raw):
    for role in ("member", "auditor"):
        with pytest.raises(gateway.TenantAccessDenied):
            run(gateway.platform_document_counts(role))

    # Independent oracle: count the documents a raw support client can read,
    # grouped by the slug of their owning tenant.
    support = scope(raw, None, "support")
    slugs = sorted(support.query("select Tenant.slug"))
    per_slug: dict = {slug: 0 for slug in slugs}
    for row in support.query("select Document { slug := .workspace.tenant.slug }"):
        per_slug[row.slug] = per_slug.get(row.slug, 0) + 1
    oracle = [
        {"tenant_slug": slug, "document_count": per_slug[slug]} for slug in slugs
    ]

    rows = run(gateway.platform_document_counts("support"))
    for row in rows:
        assert set(row) == {"tenant_slug", "document_count"}, (
            f"Unexpected keys from platform_document_counts: {sorted(row)}."
        )
    assert rows == oracle, (
        "platform_document_counts('support') does not match the per-tenant "
        f"document counts the database exposes.\ngot:      {rows!r}\nexpected: {oracle!r}"
    )

    counts_by_slug = {row["tenant_slug"]: row["document_count"] for row in rows}
    assert counts_by_slug.get("globex") == 2, (
        f"globex must report its 2 baked documents, got {counts_by_slug!r}."
    )
    assert counts_by_slug.get("initech") == 0, (
        f"initech must report 0 documents, got {counts_by_slug!r}."
    )
    assert counts_by_slug.get("acme", 0) >= 4, (
        f"acme must report at least its 4 baked documents, got {counts_by_slug!r}."
    )


# -------------------------------------------------------- 17-21. write happy path


def test_create_document_with_comments(gateway, raw):
    member = scope(raw, "acme", "member")
    member.query(
        """
        insert Workspace {
            name := <str>$name,
            tenant := assert_single((select Tenant filter .slug = 'acme'))
        }
        """,
        name=WS,
    )

    title = rand_title("created")
    body = rand_body()
    bodies = rand_comments(3)
    STATE["doc1_title"] = title
    STATE["doc1_bodies"] = bodies

    new_id = run(
        gateway.create_document("acme", "member", WS, title, body, bodies, AUTHOR)
    )
    assert isinstance(new_id, str) and re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", new_id
    ), f"create_document must return a canonical lowercase uuid string, got {new_id!r}."
    STATE["doc1_id"] = new_id

    doc = run(gateway.get_document("acme", "member", new_id))
    assert doc["id"] == new_id, f"get_document returned a different id: {doc!r}."
    assert doc["title"] == title, f"Expected title {title!r}, got {doc['title']!r}."
    assert doc["body"] == body, f"Expected body {body!r}, got {doc['body']!r}."
    assert doc["workspace_name"] == WS, (
        f"Expected workspace_name {WS!r}, got {doc['workspace_name']!r}."
    )
    assert doc["comment_bodies"] == sorted(bodies), (
        f"Expected comment bodies {sorted(bodies)!r}, got {doc['comment_bodies']!r}."
    )

    entry = workspace_entry(run(gateway.list_workspaces("acme", "member")), WS)
    assert (entry["document_count"], entry["comment_count"]) == (1, 3), (
        f"Expected the new workspace to report 1 document and 3 comments, got {entry!r}."
    )


def test_comment_length_is_a_schema_constraint(raw):
    from gel import errors as gel_errors

    member = scope(raw, "acme", "member")
    with pytest.raises(gel_errors.ConstraintViolationError):
        member.query(
            """
            insert Comment {
                body := <str>$body,
                author_email := <str>$email,
                document := assert_single((select Document filter .id = <uuid>$doc))
            }
            """,
            body=LONG_BODY,
            email=AUTHOR,
            doc=uuid.UUID(need("doc1_id")),
        )


def test_create_document_rolls_back_on_constraint_violation(gateway, raw):
    title = rand_title("rollback")
    bodies = rand_comments(3) + [LONG_BODY]

    with pytest.raises(gateway.TenantValidationError):
        run(
            gateway.create_document(
                "acme", "member", WS, title, rand_body(), bodies, AUTHOR
            )
        )

    support = scope(raw, None, "support")
    assert count_documents_titled(support, title) == 0, (
        f"A failed create_document left a document titled {title!r} behind; the "
        "operation must be atomic."
    )
    assert count_comments_with_bodies(support, bodies) == 0, (
        "A failed create_document left comments behind; the operation must be atomic."
    )


def test_create_document_validates_before_writing(gateway, raw):
    support = scope(raw, None, "support")

    blank_title = "   "
    with pytest.raises(gateway.TenantValidationError):
        run(
            gateway.create_document(
                "acme", "member", WS, blank_title, rand_body(), ["a body"], AUTHOR
            )
        )
    assert count_documents_titled(support, blank_title) == 0, (
        "create_document accepted a whitespace-only title."
    )

    no_comments_title = rand_title("nocomments")
    with pytest.raises(gateway.TenantValidationError):
        run(
            gateway.create_document(
                "acme", "member", WS, no_comments_title, rand_body(), [], AUTHOR
            )
        )
    assert count_documents_titled(support, no_comments_title) == 0, (
        "create_document accepted an empty comment_bodies list."
    )

    blank_comment_title = rand_title("blankcomment")
    with pytest.raises(gateway.TenantValidationError):
        run(
            gateway.create_document(
                "acme",
                "member",
                WS,
                blank_comment_title,
                rand_body(),
                ["a body", "   "],
                AUTHOR,
            )
        )
    assert count_documents_titled(support, blank_comment_title) == 0, (
        "create_document accepted a whitespace-only comment body."
    )

    bad_role_title = rand_title("badrole")
    with pytest.raises(gateway.TenantValidationError):
        run(
            gateway.create_document(
                "acme", "owner", WS, bad_role_title, rand_body(), ["a body"], AUTHOR
            )
        )
    assert count_documents_titled(support, bad_role_title) == 0, (
        "create_document accepted the unknown role 'owner'."
    )


def test_platform_counts_follow_new_documents(gateway):
    before = {
        row["tenant_slug"]: row["document_count"]
        for row in run(gateway.platform_document_counts("support"))
    }
    run(
        gateway.create_document(
            "acme", "member", WS, rand_title("counted"), rand_body(),
            rand_comments(1), AUTHOR,
        )
    )
    after_rows = run(gateway.platform_document_counts("support"))
    after = {row["tenant_slug"]: row["document_count"] for row in after_rows}

    assert [row["tenant_slug"] for row in after_rows] == ["acme", "globex", "initech"], (
        "platform_document_counts must be ordered by tenant_slug ascending, got "
        f"{[row['tenant_slug'] for row in after_rows]}."
    )
    assert after["acme"] == before["acme"] + 1, (
        f"The acme document count should have grown by exactly 1 "
        f"({before['acme']} -> {after['acme']})."
    )
    assert after["globex"] == 2, (
        f"The globex document count must stay 2, got {after['globex']}."
    )
    assert after["initech"] == 0, (
        f"The initech document count must stay 0, got {after['initech']}."
    )


# ------------------------------------------------------------- 22-23. negatives


def test_auditor_and_support_cannot_write(gateway):
    doc1_id = need("doc1_id")
    for role in ("auditor", "support"):
        with pytest.raises(gateway.TenantAccessDenied):
            run(
                gateway.create_document(
                    "acme", role, WS, rand_title(role), rand_body(),
                    ["a body"], AUTHOR,
                )
            )
        with pytest.raises(gateway.TenantAccessDenied):
            run(gateway.rename_document("acme", role, doc1_id, "hijacked"))
        with pytest.raises(gateway.TenantAccessDenied):
            run(gateway.delete_document("acme", role, doc1_id))
        with pytest.raises(gateway.TenantAccessDenied):
            run(gateway.archive_workspace("acme", role, WS))

    doc = run(gateway.get_document("acme", "member", doc1_id))
    assert doc["title"] == need("doc1_title"), (
        "A refused write changed the document title anyway: "
        f"{doc['title']!r} != {need('doc1_title')!r}."
    )
    entry = workspace_entry(run(gateway.list_workspaces("acme", "member")), WS)
    assert entry["archived"] is False, (
        f"A refused archive_workspace archived {WS!r} anyway."
    )


def test_cross_tenant_writes_are_refused(gateway, raw):
    gamma_id = need("gamma_id")

    with pytest.raises(gateway.TenantObjectNotFound):
        run(
            gateway.create_document(
                "acme", "member", "gamma", rand_title("cross"), rand_body(),
                ["a body"], AUTHOR,
            )
        )
    with pytest.raises(gateway.TenantObjectNotFound):
        run(gateway.rename_document("acme", "member", gamma_id, "stolen"))
    with pytest.raises(gateway.TenantObjectNotFound):
        run(gateway.delete_document("acme", "member", gamma_id))

    acme = scope(raw, "acme", "member")
    updated = acme.query(
        "update Workspace filter .name = 'gamma' set { archived := true }"
    )
    assert list(updated) == [], (
        "An acme member was able to update the globex workspace 'gamma': "
        f"{updated!r}."
    )

    globex = scope(raw, "globex", "member")
    rows = globex.query("select Workspace { name, archived } filter .name = 'gamma'")
    assert len(rows) == 1 and rows[0].archived is False, (
        f"The globex workspace 'gamma' was modified across tenants: {rows!r}."
    )
    gamma_doc = run(gateway.get_document("globex", "member", gamma_id))
    assert gamma_doc["title"] == "Gamma Spec", (
        f"The globex document was modified across tenants: {gamma_doc!r}."
    )


# ------------------------------------------------------------ 24-25. update/delete


def test_rename_document_in_scope(gateway):
    bodies = rand_comments(1)
    target_id = run(
        gateway.create_document(
            "acme", "member", WS, rand_title("torename"), rand_body(), bodies, AUTHOR
        )
    )
    new_title = rand_title("renamed")
    result = run(gateway.rename_document("acme", "member", target_id, new_title))
    assert set(result) == {
        "id",
        "title",
        "body",
        "workspace_name",
        "comment_bodies",
    }, f"Unexpected keys from rename_document: {sorted(result)}."
    assert result["id"] == target_id, (
        f"rename_document returned id {result['id']!r} instead of {target_id!r}."
    )
    assert result["title"] == new_title, (
        f"rename_document returned title {result['title']!r} instead of {new_title!r}."
    )
    assert result["comment_bodies"] == sorted(bodies), (
        f"rename_document lost the comments: {result['comment_bodies']!r}."
    )

    reread = run(gateway.get_document("acme", "member", target_id))
    assert reread["title"] == new_title, (
        f"The renamed title was not persisted, get_document reports {reread['title']!r}."
    )


def test_delete_document_removes_comments(gateway, raw):
    title = rand_title("todelete")
    bodies = rand_comments(3)
    target_id = run(
        gateway.create_document(
            "acme", "member", WS, title, rand_body(), bodies, AUTHOR
        )
    )

    assert run(gateway.delete_document("acme", "member", target_id)) is None, (
        "delete_document must return None."
    )
    with pytest.raises(gateway.TenantObjectNotFound):
        run(gateway.get_document("acme", "member", target_id))

    support = scope(raw, None, "support")
    assert count_documents_titled(support, title) == 0, (
        f"The deleted document titled {title!r} still exists."
    )
    assert count_comments_with_bodies(support, bodies) == 0, (
        "Deleting a document must also remove its comments."
    )


# ------------------------------------------------------------- 26-27. archived rules


def test_archived_workspace_is_frozen(gateway, raw):
    acme = scope(raw, "acme", "member")
    frozen_id = doc_id(acme, "Frozen Plan")

    with pytest.raises(gateway.TenantAccessDenied):
        run(
            gateway.create_document(
                "acme", "member", "zeta-archived", rand_title("frozen"),
                rand_body(), ["a body"], AUTHOR,
            )
        )
    with pytest.raises(gateway.TenantAccessDenied):
        run(gateway.rename_document("acme", "member", frozen_id, "thawed"))
    with pytest.raises(gateway.TenantAccessDenied):
        run(gateway.delete_document("acme", "member", frozen_id))

    doc = run(gateway.get_document("acme", "member", frozen_id))
    assert doc["title"] == "Frozen Plan", (
        f"The document in the archived workspace was modified: {doc!r}."
    )
    assert doc["comment_bodies"] == ["frozen note one"], (
        f"The comments of the archived workspace's document changed: {doc!r}."
    )


def test_archiving_workspace_freezes_documents(gateway):
    doc1_id = need("doc1_id")
    assert run(gateway.archive_workspace("acme", "member", WS)) is None, (
        "archive_workspace must return None."
    )

    entry = workspace_entry(run(gateway.list_workspaces("acme", "member")), WS)
    assert entry["archived"] is True, (
        f"archive_workspace did not archive {WS!r}: {entry!r}."
    )

    with pytest.raises(gateway.TenantAccessDenied):
        run(
            gateway.create_document(
                "acme", "member", WS, rand_title("afterarchive"), rand_body(),
                ["a body"], AUTHOR,
            )
        )
    with pytest.raises(gateway.TenantAccessDenied):
        run(gateway.rename_document("acme", "member", doc1_id, "late edit"))

    doc = run(gateway.get_document("acme", "member", doc1_id))
    assert doc["title"] == need("doc1_title"), (
        "Documents of an archived workspace must stay readable and unchanged, got "
        f"{doc!r}."
    )


# ---------------------------------------------------------------- 28. regression


def test_baked_rows_preserved(raw):
    support = scope(raw, None, "support")
    tenants = support.query("select Tenant { slug, name }")
    assert {row.slug: row.name for row in tenants} == BAKED_TENANTS, (
        "The baked tenants were modified: "
        f"{ {row.slug: row.name for row in tenants} }."
    )

    rows = support.query(
        """
        select Workspace {
            name,
            titles := (select .documents.title)
        }
        filter .name in array_unpack(<array<str>>$names)
        """,
        names=list(BAKED_WORKSPACE_DOCS),
    )
    got = {row.name: set(row.titles) for row in rows}
    assert got == BAKED_WORKSPACE_DOCS, (
        f"The baked workspaces/documents were modified: {got}."
    )

    charter_comments = sorted(
        support.query(
            """
            select (
                select Document filter .title = 'Alpha Charter'
            ).comments.body
            """
        )
    )
    assert charter_comments == ["charter note one", "charter note two"], (
        f"The comments of 'Alpha Charter' were modified: {charter_comments}."
    )
