import glob
import json
import os
import subprocess

import pytest

PROJECT_DIR = "/home/user/vault"
MIGRATIONS_DIR = os.path.join(PROJECT_DIR, "dbschema", "migrations")
BASELINE_MIGRATION = "/opt/vault-baseline/00001.edgeql"
START_SCRIPT = "/usr/local/bin/start-gel.sh"

ANA = "ana@vault.test"
EVAN = "evan@vault.test"
VERA = "vera@vault.test"
BRUNO = "bruno@vault.test"
NOMAD = "nomad@vault.test"

BYPASS = "configure session set apply_access_policies := false"

DOC_KEYS = {"title", "workspace", "owner_email", "archived"}
READ_KEYS = DOC_KEYS | {"body"}

BASELINE_DOCS = {
    "alpha-archive-2019": ("alpha", ANA, True, "Archived plans"),
    "alpha-notes": ("alpha", EVAN, False, "Notes v1"),
    "alpha-roadmap": ("alpha", ANA, False, "Roadmap v1"),
    "beta-charter": ("beta", BRUNO, False, "Charter v1"),
}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _gel(*queries, expect_success=True):
    """Run EdgeQL statements in one gel CLI session; return rows of the last one."""
    cmd = ["gel", "query", "--output-format=json-lines"]
    cmd.extend(queries)
    proc = subprocess.run(
        cmd, cwd=PROJECT_DIR, capture_output=True, text=True, timeout=180
    )
    if expect_success:
        assert proc.returncode == 0, (
            f"gel query {queries} failed (exit {proc.returncode}):\n"
            f"stdout={proc.stdout}\nstderr={proc.stderr}"
        )
    rows = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("OK:"):
            continue
        rows.append(json.loads(line))
    return proc, rows


def _privileged(*queries):
    """Run queries with object-level rules disabled (inspection only)."""
    _, rows = _gel(BYPASS, *queries)
    return rows


def _actor_id(email):
    rows = _privileged(
        f"select <str>(select Actor filter .email = '{email}').id"
    )
    assert rows, f"No Actor row found for {email}."
    return rows[0]


def _as_actor(email, *queries, expect_success=True):
    """Run queries on a session whose current_actor_id global is set to `email`."""
    actor_id = _actor_id(email)
    return _gel(
        f"set global current_actor_id := <uuid>'{actor_id}'",
        *queries,
        expect_success=expect_success,
    )


def _rows_as_actor(email, *queries):
    _, rows = _as_actor(email, *queries)
    return rows


def _rows_anonymous(*queries):
    _, rows = _gel(*queries)
    return rows


def _cli(*args):
    """Run the vault CLI; return (exit_code, parsed stdout json, raw stdout)."""
    proc = subprocess.run(
        ["node", "dist/cli.js", *args],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=180,
    )
    stdout = proc.stdout.strip()
    lines = [line for line in stdout.splitlines() if line.strip()]
    assert len(lines) == 1, (
        f"Expected exactly one line of JSON on stdout for 'node dist/cli.js "
        f"{' '.join(args)}', got {len(lines)} line(s):\n{proc.stdout}\n"
        f"stderr={proc.stderr}"
    )
    try:
        payload = json.loads(lines[0])
    except json.JSONDecodeError as exc:  # pragma: no cover - assertion path
        raise AssertionError(
            f"stdout of 'node dist/cli.js {' '.join(args)}' is not valid JSON: "
            f"{lines[0]!r} ({exc})"
        )
    return proc.returncode, payload, proc.stdout


def _expect_ok(payload, code, action, actor):
    assert payload.get("ok") is True, (
        f"Expected ok=true for action {action}, got: {payload}"
    )
    assert code == 0, f"Expected exit code 0 for action {action}, got {code}: {payload}"
    assert payload.get("action") == action, (
        f"Expected action={action!r} in output, got: {payload}"
    )
    assert payload.get("actor") == actor, (
        f"Expected actor={actor!r} in output, got: {payload}"
    )
    return payload["data"]


def _expect_error(payload, code, action, expected_code, expected_exit):
    assert payload.get("ok") is False, (
        f"Expected ok=false for action {action}, got: {payload}"
    )
    assert payload.get("action") == action, (
        f"Expected action={action!r} in output, got: {payload}"
    )
    error = payload.get("error")
    assert isinstance(error, dict), f"Expected an error object, got: {payload}"
    assert error.get("code") == expected_code, (
        f"Expected error code {expected_code} for action {action}, got: {payload}"
    )
    assert isinstance(error.get("message"), str) and error["message"], (
        f"Expected a non-empty error message for action {action}, got: {payload}"
    )
    assert code == expected_exit, (
        f"Expected exit code {expected_exit} for action {action}, got {code}: {payload}"
    )


def _doc_state(title):
    rows = _privileged(
        "select Document { title, workspace_name := .workspace.name, "
        "owner_email := .owner.email, archived, body } "
        f"filter .title = '{title}'"
    )
    return rows[0] if rows else None


def _log_count():
    return _privileged("select count(ActivityLog)")[0]


def _logs():
    return _privileged(
        "select ActivityLog { action, actor_email, doc_title } order by .at"
    )


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def server():
    proc = subprocess.run([START_SCRIPT], capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, (
        f"{START_SCRIPT} failed (exit {proc.returncode}):\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    return True


@pytest.fixture(scope="session")
def built(server):
    """Build the CLI and clear leftovers from a previous run."""
    proc = subprocess.run(
        ["npm", "run", "build"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, (
        f"'npm run build' failed (exit {proc.returncode}):\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    dist_cli = os.path.join(PROJECT_DIR, "dist", "cli.js")
    assert os.path.isfile(dist_cli), f"'npm run build' did not produce {dist_cli}."
    _privileged(
        "delete Document filter .title in {'alpha-draft', 'vera-attempt', 'spoofed'}",
        "delete ActivityLog",
    )
    return dist_cli


# --------------------------------------------------------------------------- #
# schema / migration state
# --------------------------------------------------------------------------- #
def test_actor_global_is_declared(server):
    rows = _privileged(
        "select schema::Global { name, required, target_name := .target.name } "
        "filter .name = 'default::current_actor_id'"
    )
    assert len(rows) == 1, (
        "Expected a schema global named 'current_actor_id' in the default module, "
        f"found: {rows}"
    )
    assert rows[0]["target_name"] == "std::uuid", (
        f"Expected current_actor_id to be a std::uuid global, got: {rows[0]}"
    )
    assert rows[0]["required"] is False, (
        f"Expected current_actor_id to be optional, got: {rows[0]}"
    )


def test_access_policies_exist_on_protected_types(server):
    rows = _privileged(
        "select schema::ObjectType { name, policy_count := count(.access_policies) } "
        "filter .name in {'default::Document', 'default::Actor', 'default::Workspace', "
        "'default::Membership', 'default::ActivityLog'} order by .name"
    )
    counts = {row["name"]: row["policy_count"] for row in rows}
    for type_name in (
        "default::Document",
        "default::Actor",
        "default::Workspace",
        "default::Membership",
    ):
        assert counts.get(type_name, 0) >= 1, (
            f"{type_name} must be protected by at least one access policy, got {counts}"
        )
    assert counts.get("default::ActivityLog") == 0, (
        f"default::ActivityLog must stay unrestricted, got {counts}"
    )


def test_document_policies_cover_every_access_kind(server):
    rows = _privileged(
        "select (select schema::ObjectType filter .name = 'default::Document')"
        ".access_policies { name, access_kinds }"
    )
    kinds = set()
    for row in rows:
        kinds.update(row["access_kinds"])
    assert kinds == {"Select", "Insert", "UpdateRead", "UpdateWrite", "Delete"}, (
        "The access policies on default::Document must govern select, insert, "
        f"update (read and write) and delete; found kinds {sorted(kinds)}"
    )


def test_migration_history_extended_and_in_sync(server):
    migrations = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql")))
    assert len(migrations) >= 2, (
        f"Expected the schema change to be captured in a new migration, found: {migrations}"
    )
    baseline = [m for m in migrations if os.path.basename(m).startswith("00001-")]
    assert len(baseline) == 1, (
        f"The original 00001-* migration must still be present, found: {migrations}"
    )
    with open(baseline[0]) as handle:
        current = handle.read()
    with open(BASELINE_MIGRATION) as handle:
        original = handle.read()
    assert current == original, (
        f"The baseline migration {baseline[0]} was modified; it must stay untouched."
    )
    proc = subprocess.run(
        ["gel", "migration", "status"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, (
        f"'gel migration status' failed:\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert "up to date" in (proc.stdout + proc.stderr).lower(), (
        f"The branch is not in sync with dbschema/default.gel:\n{proc.stdout}\n{proc.stderr}"
    )


def test_seed_rows_are_intact(server):
    counts = _privileged(
        "select { docs := count(Document), actors := count(Actor), "
        "workspaces := count(Workspace), memberships := count(Membership) }"
    )[0]
    assert counts == {
        "docs": 4,
        "actors": 5,
        "workspaces": 4,
        "memberships": 6,
    }, f"Seeded rows were modified: {counts}"
    docs = _privileged(
        "select Document { title, workspace_name := .workspace.name, "
        "owner_email := .owner.email, archived, body } order by .title"
    )
    actual = {
        row["title"]: (
            row["workspace_name"],
            row["owner_email"],
            row["archived"],
            row["body"],
        )
        for row in docs
    }
    assert actual == BASELINE_DOCS, f"Seeded documents were modified: {actual}"


# --------------------------------------------------------------------------- #
# database-enforced visibility
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "email,expected",
    [
        (ANA, ["alpha-archive-2019", "alpha-notes", "alpha-roadmap"]),
        (EVAN, ["alpha-notes", "alpha-roadmap"]),
        (VERA, ["alpha-notes", "alpha-roadmap"]),
        (BRUNO, ["beta-charter"]),
        (NOMAD, []),
    ],
)
def test_document_visibility_per_actor(server, email, expected):
    rows = _rows_as_actor(email, "select Document { title } order by .title")
    assert [row["title"] for row in rows] == expected, (
        f"{email} must only be able to read {expected} straight from the database, "
        f"got {[row['title'] for row in rows]}"
    )


def test_document_visibility_for_anonymous_connection(server):
    rows = _rows_anonymous("select Document { title } order by .title")
    assert rows == [], (
        f"A connection without current_actor_id must read no documents, got {rows}"
    )


@pytest.mark.parametrize(
    "email,expected",
    [
        (ANA, ["alpha", "gamma"]),
        (EVAN, ["alpha", "delta"]),
        (VERA, ["alpha"]),
        (BRUNO, ["beta"]),
        (NOMAD, []),
    ],
)
def test_workspace_visibility_per_actor(server, email, expected):
    rows = _rows_as_actor(email, "select Workspace { name } order by .name")
    assert [row["name"] for row in rows] == expected, (
        f"{email} must only see workspaces {expected}, got {rows}"
    )


def test_workspace_visibility_for_anonymous_connection(server):
    assert _rows_anonymous("select Workspace { name }") == [], (
        "A connection without current_actor_id must read no workspaces."
    )


@pytest.mark.parametrize(
    "email,expected",
    [(ANA, 2), (EVAN, 2), (VERA, 1), (NOMAD, 0)],
)
def test_membership_visibility_per_actor(server, email, expected):
    rows = _rows_as_actor(email, "select count(Membership)")
    assert rows == [expected], (
        f"{email} must only see their own {expected} membership row(s), got {rows}"
    )


def test_membership_visibility_for_anonymous_connection(server):
    assert _rows_anonymous("select count(Membership)") == [0], (
        "A connection without current_actor_id must read no memberships."
    )


@pytest.mark.parametrize(
    "email,expected",
    [
        (ANA, [ANA, EVAN, VERA]),
        (BRUNO, [BRUNO]),
        (NOMAD, [NOMAD]),
    ],
)
def test_actor_visibility_per_actor(server, email, expected):
    rows = _rows_as_actor(email, "select Actor { email } order by .email")
    assert [row["email"] for row in rows] == expected, (
        f"{email} must only see actors {expected}, got {rows}"
    )


def test_actor_visibility_for_anonymous_connection(server):
    assert _rows_anonymous("select Actor { email }") == [], (
        "A connection without current_actor_id must read no actors."
    )


def test_no_leak_through_backlink_traversal(server):
    rows = _rows_as_actor(
        VERA,
        "select Workspace { name, docs := (select .<workspace[is Document] "
        "order by .title).title } order by .name",
    )
    assert rows == [
        {"name": "alpha", "docs": ["alpha-notes", "alpha-roadmap"]}
    ], f"Backlink traversal leaked data to a viewer: {rows}"


def test_no_leak_through_nested_shape(server):
    rows = _rows_as_actor(
        BRUNO, "select Document { title, owner_email := .owner.email } order by .title"
    )
    assert rows == [{"title": "beta-charter", "owner_email": BRUNO}], (
        f"Nested shape leaked documents from other workspaces: {rows}"
    )
    anonymous = _rows_anonymous("select Document { title, owner: { email } }")
    assert anonymous == [], (
        f"Nested shape leaked documents to an anonymous connection: {anonymous}"
    )


def test_owner_spoofing_insert_is_rejected_by_the_database(server):
    proc, _ = _as_actor(
        EVAN,
        "insert Document { title := 'spoofed', body := 'x', "
        "workspace := assert_single((select Workspace filter .name = 'alpha')), "
        f"owner := assert_single((select Actor filter .email = '{ANA}')) }}",
        expect_success=False,
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0, (
        f"Inserting a document owned by another actor must fail, got: {combined}"
    )
    assert "AccessPolicyError" in combined, (
        f"Expected an AccessPolicyError when spoofing the owner, got: {combined}"
    )
    assert _privileged("select count((select Document filter .title = 'spoofed'))") == [0], (
        "A spoofed document was persisted even though the insert should be rejected."
    )


# --------------------------------------------------------------------------- #
# CLI read paths
# --------------------------------------------------------------------------- #
def test_cli_list_documents_for_viewer(built):
    code, payload, _ = _cli("list-documents", "--actor", VERA)
    data = _expect_ok(payload, code, "list-documents", VERA)
    assert data == [
        {
            "title": "alpha-notes",
            "workspace": "alpha",
            "owner_email": EVAN,
            "archived": False,
        },
        {
            "title": "alpha-roadmap",
            "workspace": "alpha",
            "owner_email": ANA,
            "archived": False,
        },
    ], f"Unexpected list-documents payload for a viewer: {data}"


def test_cli_list_documents_for_workspace_owner(built):
    code, payload, _ = _cli("list-documents", "--actor", ANA)
    data = _expect_ok(payload, code, "list-documents", ANA)
    assert [row["title"] for row in data] == [
        "alpha-archive-2019",
        "alpha-notes",
        "alpha-roadmap",
    ], f"Unexpected list-documents payload for the workspace owner: {data}"
    assert data[0]["archived"] is True, (
        f"Expected the archived document to be reported as archived: {data[0]}"
    )
    for row in data:
        assert set(row) == DOC_KEYS, f"Unexpected keys in a document object: {row}"


def test_cli_list_documents_without_membership_and_anonymously(built):
    code, payload, _ = _cli("list-documents", "--actor", NOMAD)
    assert _expect_ok(payload, code, "list-documents", NOMAD) == [], (
        f"An actor without memberships must see no documents: {payload}"
    )
    code, payload, _ = _cli("list-documents")
    assert _expect_ok(payload, code, "list-documents", None) == [], (
        f"An anonymous invocation must see no documents: {payload}"
    )


def test_cli_read_document_for_viewer(built):
    code, payload, _ = _cli("read-document", "--actor", VERA, "--title", "alpha-roadmap")
    data = _expect_ok(payload, code, "read-document", VERA)
    assert set(data) == READ_KEYS, f"Unexpected keys in read-document payload: {data}"
    assert data == {
        "title": "alpha-roadmap",
        "workspace": "alpha",
        "owner_email": ANA,
        "archived": False,
        "body": "Roadmap v1",
    }, f"Unexpected read-document payload: {data}"


@pytest.mark.parametrize(
    "args",
    [
        ["read-document", "--actor", EVAN, "--title", "alpha-archive-2019"],
        ["read-document", "--actor", ANA, "--title", "beta-charter"],
        ["read-document", "--title", "alpha-roadmap"],
    ],
)
def test_cli_read_document_not_found(built, args):
    code, payload, _ = _cli(*args)
    _expect_error(payload, code, "read-document", "NOT_FOUND", 3)


@pytest.mark.parametrize(
    "args,action",
    [
        (
            ["read-document", "--actor", "ghost@vault.test", "--title", "alpha-roadmap"],
            "read-document",
        ),
        (
            [
                "create-document",
                "--actor",
                EVAN,
                "--workspace",
                "epsilon",
                "--title",
                "x",
                "--body",
                "y",
            ],
            "create-document",
        ),
        (["frobnicate", "--actor", EVAN], "frobnicate"),
        (["update-document", "--actor", EVAN, "--title", "alpha-notes"], "update-document"),
    ],
)
def test_cli_bad_requests(built, args, action):
    code, payload, _ = _cli(*args)
    _expect_error(payload, code, action, "BAD_REQUEST", 2)


def test_cli_audit_bypasses_all_rules(built):
    code, payload, _ = _cli("audit")
    data = _expect_ok(payload, code, "audit", None)
    assert [row["title"] for row in data] == sorted(BASELINE_DOCS), (
        f"audit must list every stored document in title order, got: {data}"
    )
    for row in data:
        assert set(row) == DOC_KEYS, f"Unexpected keys in an audit row: {row}"
        workspace, owner, archived, _body = BASELINE_DOCS[row["title"]]
        assert (row["workspace"], row["owner_email"], row["archived"]) == (
            workspace,
            owner,
            archived,
        ), f"audit reported wrong data for {row['title']}: {row}"


# --------------------------------------------------------------------------- #
# CLI write paths
# --------------------------------------------------------------------------- #
def test_cli_create_denied_for_viewer_leaves_no_trace(built):
    before_docs = _privileged("select count(Document)")[0]
    before_logs = _log_count()
    code, payload, _ = _cli(
        "create-document",
        "--actor",
        VERA,
        "--workspace",
        "alpha",
        "--title",
        "vera-attempt",
        "--body",
        "nope",
    )
    _expect_error(payload, code, "create-document", "POLICY_VIOLATION", 4)
    assert _privileged("select count(Document)") == [before_docs], (
        "A rejected create must not change the number of stored documents."
    )
    assert _doc_state("vera-attempt") is None, (
        "A rejected create persisted the document anyway."
    )
    assert _log_count() == before_logs, (
        "A rejected create must not append an ActivityLog row."
    )


def test_cli_create_allowed_for_editor_with_atomic_log(built):
    before_docs = _privileged("select count(Document)")[0]
    before_logs = _log_count()
    code, payload, _ = _cli(
        "create-document",
        "--actor",
        EVAN,
        "--workspace",
        "alpha",
        "--title",
        "alpha-draft",
        "--body",
        "Draft v1",
    )
    data = _expect_ok(payload, code, "create-document", EVAN)
    assert data == {
        "title": "alpha-draft",
        "workspace": "alpha",
        "owner_email": EVAN,
        "archived": False,
    }, f"Unexpected create-document payload: {data}"
    assert _privileged("select count(Document)") == [before_docs + 1], (
        "The new document was not persisted."
    )
    stored = _doc_state("alpha-draft")
    assert stored is not None and stored["body"] == "Draft v1", (
        f"The new document was stored with the wrong body: {stored}"
    )
    assert _log_count() == before_logs + 1, (
        "A successful create must append exactly one ActivityLog row."
    )
    latest = _logs()[-1]
    assert latest == {
        "action": "create-document",
        "actor_email": EVAN,
        "doc_title": "alpha-draft",
    }, f"Unexpected ActivityLog row for the create: {latest}"


def test_cli_create_duplicate_title_conflicts(built):
    before_docs = _privileged("select count(Document)")[0]
    code, payload, _ = _cli(
        "create-document",
        "--actor",
        EVAN,
        "--workspace",
        "alpha",
        "--title",
        "alpha-draft",
        "--body",
        "Draft v1",
    )
    _expect_error(payload, code, "create-document", "CONFLICT", 5)
    assert _privileged("select count(Document)") == [before_docs], (
        "A conflicting create must not add a document."
    )


@pytest.mark.parametrize(
    "email,title,expected_body",
    [
        (VERA, "alpha-notes", "Notes v1"),
        (EVAN, "alpha-archive-2019", "Archived plans"),
    ],
)
def test_cli_update_denied(built, email, title, expected_body):
    before_logs = _log_count()
    code, payload, _ = _cli(
        "update-document", "--actor", email, "--title", title, "--body", "hacked"
    )
    _expect_error(payload, code, "update-document", "NO_MATCH", 3)
    stored = _doc_state(title)
    assert stored is not None and stored["body"] == expected_body, (
        f"A rejected update modified {title}: {stored}"
    )
    assert _log_count() == before_logs, (
        "A rejected update must not append an ActivityLog row."
    )


def test_cli_update_allowed_for_workspace_owner_and_restore(built):
    before_logs = _log_count()
    code, payload, _ = _cli(
        "update-document", "--actor", ANA, "--title", "alpha-notes", "--body", "Notes v2"
    )
    data = _expect_ok(payload, code, "update-document", ANA)
    assert set(data) == DOC_KEYS, f"Unexpected keys in update-document payload: {data}"
    stored = _doc_state("alpha-notes")
    assert stored is not None and stored["body"] == "Notes v2", (
        f"The update was not persisted: {stored}"
    )
    assert _log_count() == before_logs + 1, (
        "A successful update must append exactly one ActivityLog row."
    )
    latest = _logs()[-1]
    assert latest == {
        "action": "update-document",
        "actor_email": ANA,
        "doc_title": "alpha-notes",
    }, f"Unexpected ActivityLog row for the update: {latest}"

    code, payload, _ = _cli(
        "update-document", "--actor", ANA, "--title", "alpha-notes", "--body", "Notes v1"
    )
    _expect_ok(payload, code, "update-document", ANA)
    restored = _doc_state("alpha-notes")
    assert restored is not None and restored["body"] == "Notes v1", (
        f"Could not restore the baseline body: {restored}"
    )


def test_cli_move_denied_without_rights_on_target_workspace(built):
    before_logs = _log_count()
    code, payload, _ = _cli(
        "move-document", "--actor", EVAN, "--title", "alpha-notes", "--to-workspace", "delta"
    )
    _expect_error(payload, code, "move-document", "POLICY_VIOLATION", 4)
    stored = _doc_state("alpha-notes")
    assert stored is not None and stored["workspace_name"] == "alpha", (
        f"A rejected move changed the document's workspace: {stored}"
    )
    assert _log_count() == before_logs, (
        "A rejected move must not append an ActivityLog row."
    )


def test_cli_move_allowed_and_restore(built):
    code, payload, _ = _cli(
        "move-document", "--actor", ANA, "--title", "alpha-roadmap", "--to-workspace", "gamma"
    )
    data = _expect_ok(payload, code, "move-document", ANA)
    assert data["workspace"] == "gamma", f"Unexpected move-document payload: {data}"
    stored = _doc_state("alpha-roadmap")
    assert stored is not None and stored["workspace_name"] == "gamma", (
        f"The move was not persisted: {stored}"
    )
    latest = _logs()[-1]
    assert latest == {
        "action": "move-document",
        "actor_email": ANA,
        "doc_title": "alpha-roadmap",
    }, f"Unexpected ActivityLog row for the move: {latest}"

    code, payload, _ = _cli(
        "move-document", "--actor", ANA, "--title", "alpha-roadmap", "--to-workspace", "alpha"
    )
    _expect_ok(payload, code, "move-document", ANA)
    restored = _doc_state("alpha-roadmap")
    assert restored is not None and restored["workspace_name"] == "alpha", (
        f"Could not move the document back to its baseline workspace: {restored}"
    )


def test_cli_delete_denied_for_non_owner(built):
    before_logs = _log_count()
    code, payload, _ = _cli("delete-document", "--actor", ANA, "--title", "alpha-draft")
    _expect_error(payload, code, "delete-document", "NO_MATCH", 3)
    assert _doc_state("alpha-draft") is not None, (
        "A rejected delete removed the document anyway."
    )
    assert _log_count() == before_logs, (
        "A rejected delete must not append an ActivityLog row."
    )


def test_cli_delete_allowed_for_document_owner(built):
    before_docs = _privileged("select count(Document)")[0]
    code, payload, _ = _cli("delete-document", "--actor", EVAN, "--title", "alpha-draft")
    data = _expect_ok(payload, code, "delete-document", EVAN)
    assert data == {"title": "alpha-draft"}, (
        f"Unexpected delete-document payload: {data}"
    )
    assert _doc_state("alpha-draft") is None, "The document was not deleted."
    assert _privileged("select count(Document)") == [before_docs - 1], (
        "The document count did not go back to the baseline after the delete."
    )
    latest = _logs()[-1]
    assert latest == {
        "action": "delete-document",
        "actor_email": EVAN,
        "doc_title": "alpha-draft",
    }, f"Unexpected ActivityLog row for the delete: {latest}"


def test_activity_log_only_contains_successful_operations(built):
    logs = _logs()
    assert logs == [
        {"action": "create-document", "actor_email": EVAN, "doc_title": "alpha-draft"},
        {"action": "update-document", "actor_email": ANA, "doc_title": "alpha-notes"},
        {"action": "update-document", "actor_email": ANA, "doc_title": "alpha-notes"},
        {"action": "move-document", "actor_email": ANA, "doc_title": "alpha-roadmap"},
        {"action": "move-document", "actor_email": ANA, "doc_title": "alpha-roadmap"},
        {"action": "delete-document", "actor_email": EVAN, "doc_title": "alpha-draft"},
    ], f"Unexpected ActivityLog contents after the whole run: {logs}"


def test_typescript_sources_and_build_output_exist(built):
    for path in (
        os.path.join(PROJECT_DIR, "src", "service.ts"),
        os.path.join(PROJECT_DIR, "src", "cli.ts"),
        os.path.join(PROJECT_DIR, "dist", "cli.js"),
    ):
        assert os.path.isfile(path), f"Expected {path} to exist."
