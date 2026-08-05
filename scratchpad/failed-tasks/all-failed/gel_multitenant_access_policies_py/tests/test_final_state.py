"""Final-state verification for gel_multitenant_access_policies_py.

The checks below combine three independent angles:

* the CLI contract (`python3 app.py ...`): exit codes, stdout/stderr split and
  JSON shapes,
* direct database access with the graders' own clients, which never go through
  the executor's code, and
* regression / idempotency checks over the shipped dataset.
"""

import concurrent.futures
import json
import os
import shutil
import subprocess

import gel
import pytest

PROJECT_DIR = "/home/user/tenantdesk"
APP_PATH = os.path.join(PROJECT_DIR, "app.py")
SCHEMA_PATH = os.path.join(PROJECT_DIR, "dbschema", "default.gel")
DATASET_PATH = os.path.join(PROJECT_DIR, "seed", "dataset.json")

EXPECTED_TENANTS = {
    "acme": "Acme Industrial",
    "globex": "Globex Systems",
    "initech": "Initech Holdings",
}

EXPECTED_ACTORS = {
    "ava@acme.example": ("acme", "admin"),
    "ben@acme.example": ("acme", "agent"),
    "cleo@acme.example": ("acme", "readonly"),
    "dan@globex.example": ("globex", "admin"),
    "eve@globex.example": ("globex", "agent"),
    "fay@initech.example": ("initech", "admin"),
    "gus@initech.example": ("initech", "readonly"),
}

ACME_ADMIN = "ava@acme.example"
ACME_AGENT = "ben@acme.example"
ACME_READONLY = "cleo@acme.example"
GLOBEX_ADMIN = "dan@globex.example"
INITECH_ADMIN = "fay@initech.example"
INITECH_READONLY = "gus@initech.example"
UNKNOWN_ACTOR = "nobody@acme.example"

EXPECTED_SEED_COUNTS = {"acme": 300, "globex": 200, "initech": 100}

LEFTOVER_REFS = [
    "ACME-9001",
    "ACME-9002",
    "ACME-9003",
    "ACME-9004",
    "ACME-9500",
    "ACME-7001",
    "ACME-7002",
    "GLOBEX-0001",
]

TICKET_SHAPE = """
    select Ticket {
        ref,
        subject,
        status_str := <str>.status,
        tenant_slug := .tenant.slug
    }
"""


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def gel_server():
    """Make sure the local Gel server is running and ready."""
    gel_up = shutil.which("gel-up")
    assert gel_up is not None, "The 'gel-up' helper is not available in PATH."
    proc = subprocess.run([gel_up], capture_output=True, text=True, timeout=600)
    print("gel-up stdout:", proc.stdout)
    print("gel-up stderr:", proc.stderr)
    assert proc.returncode == 0, (
        "'gel-up' failed to bring the local Gel server up.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    return True


@pytest.fixture(scope="session")
def base_client(gel_server):
    """A grader-owned client with no identity set at all."""
    client = gel.create_client()
    try:
        client.ensure_connected()
        yield client
    finally:
        client.close()


@pytest.fixture(scope="session")
def as_actor(base_client):
    """Return a client scoped to the given actor identity."""

    def factory(email=None):
        if email is None:
            return base_client
        return base_client.with_globals({"current_actor_email": email})

    return factory


@pytest.fixture(scope="session", autouse=True)
def scrub_leftovers(as_actor):
    """Remove tickets created by an earlier verification run (best effort)."""

    def scrub():
        client = as_actor(ACME_ADMIN)
        for ref in LEFTOVER_REFS:
            try:
                client.query(
                    "delete Ticket filter .ref = <str>$ref",
                    ref=ref,
                )
            except Exception as exc:  # pragma: no cover - best effort only
                print(f"cleanup of {ref} failed: {exc}")

    scrub()
    yield
    scrub()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def run_cli(*args, timeout=180):
    return subprocess.run(
        ["python3", APP_PATH, *args],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def expect_ok(proc, argv):
    assert proc.returncode == 0, (
        f"`app.py {' '.join(argv)}` should have succeeded but exited with "
        f"{proc.returncode}.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"`app.py {' '.join(argv)}` must print exactly one JSON document on "
            f"stdout, got {proc.stdout!r} ({exc})."
        ) from exc
    return payload


def expect_failure(proc, argv, code, message):
    assert proc.returncode == code, (
        f"`app.py {' '.join(argv)}` should have exited with {code} but exited "
        f"with {proc.returncode}.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert proc.stdout.strip() == "", (
        f"`app.py {' '.join(argv)}` must print nothing on stdout when it fails, "
        f"got {proc.stdout!r}."
    )
    lines = [line for line in proc.stderr.splitlines() if line.strip()]
    assert lines == [message], (
        f"`app.py {' '.join(argv)}` must print exactly one stderr line "
        f"{message!r}, got {proc.stderr!r}."
    )


def cli_json(*args):
    proc = run_cli(*args)
    return expect_ok(proc, list(args))


def fetch_ticket(client, ref):
    """Return the ticket visible to this identity under `ref`, or None."""
    rows = client.query(TICKET_SHAPE + " filter .ref = <str>$ref", ref=ref)
    rows = list(rows)
    assert len(rows) <= 1, (
        f"Exactly one ticket with ref {ref} may be visible to a single "
        f"identity, found {len(rows)}."
    )
    if not rows:
        return None
    return _as_dict(rows[0])


def _as_dict(row):
    return {
        "ref": row.ref,
        "subject": row.subject,
        "status": row.status_str,
        "tenant": row.tenant_slug,
    }


def fetch_tickets(client, refs):
    rows = client.query(
        TICKET_SHAPE + " filter .ref in array_unpack(<array<str>>$refs)",
        refs=list(refs),
    )
    return {row.ref: _as_dict(row) for row in rows}


def visible_count(client):
    return client.query_single("select count(Ticket)")


def visible_refs(client):
    return sorted(client.query("select Ticket.ref"))


def try_write(client, query, **kwargs):
    """Run a mutating query, returning (result, error) instead of raising."""
    try:
        return list(client.query(query, **kwargs)), None
    except Exception as exc:  # denial surfaces as an error for insert/update write
        return None, exc


def load_dataset():
    with open(DATASET_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def dataset_tickets_by_tenant(slug):
    return {
        entry["ref"]: entry
        for entry in load_dataset()["tickets"]
        if entry["tenant"] == slug
    }


# ---------------------------------------------------------------------------
# project artefacts
# ---------------------------------------------------------------------------


def test_project_artifacts_exist():
    assert os.path.isfile(APP_PATH), f"{APP_PATH} does not exist."
    assert os.path.getsize(APP_PATH) > 0, f"{APP_PATH} is empty."
    assert os.path.isfile(SCHEMA_PATH), f"{SCHEMA_PATH} does not exist."
    assert os.path.getsize(SCHEMA_PATH) > 0, f"{SCHEMA_PATH} is empty."


def test_dataset_file_untouched():
    data = load_dataset()
    assert len(data["tenants"]) == 3, "The shipped dataset must still hold 3 tenants."
    assert len(data["actors"]) == 7, "The shipped dataset must still hold 7 actors."
    assert len(data["tickets"]) == 600, (
        "The shipped dataset must still hold 600 tickets."
    )


def test_gel_cli_reaches_the_instance(gel_server):
    proc = subprocess.run(
        ["gel", "query", "select 1"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        "'gel query' could not reach the local Gel instance.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


# ---------------------------------------------------------------------------
# schema-level state that must be readable by anyone
# ---------------------------------------------------------------------------


def test_tenants_readable_without_any_identity(as_actor):
    client = as_actor()
    rows = client.query("select Tenant { slug, name }")
    found = {row.slug: row.name for row in rows}
    assert found == EXPECTED_TENANTS, (
        "Every connection must be able to read all Tenant objects; expected "
        f"{EXPECTED_TENANTS}, got {found}."
    )


def test_actors_readable_without_any_identity(as_actor):
    client = as_actor()
    rows = client.query(
        "select Actor { email, role_str := <str>.role, tenant_slug := .tenant.slug }"
    )
    found = {row.email: (row.tenant_slug, row.role_str) for row in rows}
    assert found == EXPECTED_ACTORS, (
        "Every connection must be able to read all Actor objects with their "
        f"tenant and role; expected {EXPECTED_ACTORS}, got {found}."
    )


# ---------------------------------------------------------------------------
# isolation, verified straight against the database
# ---------------------------------------------------------------------------


def test_no_tickets_visible_without_identity(as_actor):
    count = visible_count(as_actor())
    assert count == 0, (
        "A connection that never sets current_actor_email must not see any "
        f"Ticket, but it saw {count}."
    )


def test_no_tickets_visible_for_unknown_identity(as_actor):
    count = visible_count(as_actor(UNKNOWN_ACTOR))
    assert count == 0, (
        "A connection whose current_actor_email matches no Actor must not see "
        f"any Ticket, but it saw {count}."
    )


def test_seeded_tickets_are_loaded_and_scoped_per_tenant(as_actor):
    for email, slug in (
        (ACME_ADMIN, "acme"),
        (GLOBEX_ADMIN, "globex"),
        (INITECH_READONLY, "initech"),
    ):
        client = as_actor(email)
        expected = EXPECTED_SEED_COUNTS[slug]
        dataset_refs = set(dataset_tickets_by_tenant(slug))
        found = fetch_tickets(client, dataset_refs)
        assert len(found) == expected, (
            f"{email} must see all {expected} seeded tickets of tenant {slug}, "
            f"but only {len(found)} of them are visible."
        )
        assert all(item["tenant"] == slug for item in found.values()), (
            f"Every ticket visible to {email} must belong to tenant {slug}: "
            f"{sorted({item['tenant'] for item in found.values()})}."
        )


def test_seeded_ticket_field_values(as_actor):
    client = as_actor(ACME_ADMIN)
    for ref, subject, status in (
        ("ACME-0001", "acme intake 0001", "open"),
        ("ACME-0002", "acme intake 0002", "pending"),
        ("ACME-0003", "acme intake 0003", "closed"),
    ):
        ticket = fetch_ticket(client, ref)
        assert ticket == {
            "ref": ref,
            "subject": subject,
            "status": status,
            "tenant": "acme",
        }, f"Seeded ticket {ref} was not loaded faithfully: {ticket}."

    initech = as_actor(INITECH_READONLY)
    ticket = fetch_ticket(initech, "INITECH-0100")
    assert ticket == {
        "ref": "INITECH-0100",
        "subject": "initech intake 0100",
        "status": "open",
        "tenant": "initech",
    }, f"Seeded ticket INITECH-0100 was not loaded faithfully: {ticket}."


def test_identity_cannot_see_foreign_refs(as_actor):
    client = as_actor(ACME_ADMIN)
    refs = visible_refs(client)
    foreign = [ref for ref in refs if ref.startswith(("GLOBEX-", "INITECH-"))]
    assert foreign == [], (
        "An acme identity must not see tickets whose refs belong to other "
        f"tenants, but these were visible: {foreign[:10]}."
    )


def test_direct_cross_tenant_select_returns_nothing(as_actor):
    ticket = fetch_ticket(as_actor(GLOBEX_ADMIN), "ACME-0001")
    assert ticket is None, (
        "A globex identity must not be able to select the acme ticket "
        f"ACME-0001, but got {ticket}."
    )


def test_direct_cross_tenant_update_has_no_effect(as_actor):
    attacker = as_actor(GLOBEX_ADMIN)
    result, error = try_write(
        attacker,
        "update Ticket filter .ref = <str>$ref set { subject := <str>$subject }",
        ref="ACME-0005",
        subject="pwned",
    )
    assert error is not None or result == [], (
        "A globex identity must not be able to update the acme ticket "
        f"ACME-0005, but the update reported {result}."
    )
    ticket = fetch_ticket(as_actor(ACME_ADMIN), "ACME-0005")
    assert ticket is not None and ticket["subject"] == "acme intake 0005", (
        "ACME-0005 was modified by a foreign identity: " f"{ticket}."
    )


def test_direct_cross_tenant_delete_has_no_effect(as_actor):
    attacker = as_actor(GLOBEX_ADMIN)
    result, error = try_write(
        attacker,
        "delete Ticket filter .ref = <str>$ref",
        ref="ACME-0006",
    )
    assert error is not None or result == [], (
        "A globex identity must not be able to delete the acme ticket "
        f"ACME-0006, but the delete reported {result}."
    )
    ticket = fetch_ticket(as_actor(ACME_ADMIN), "ACME-0006")
    assert ticket is not None, "ACME-0006 was deleted by a foreign identity."


def test_direct_cross_tenant_insert_is_refused(as_actor):
    attacker = as_actor(GLOBEX_ADMIN)
    try_write(
        attacker,
        """
        with
            donor := assert_exists(assert_single((
                select Ticket filter .ref = <str>$donor
            ))),
            target := assert_exists(assert_single((
                select Tenant filter .slug = <str>$slug
            )))
        insert Ticket {
            ref := <str>$ref,
            subject := <str>$subject,
            status := donor.status,
            tenant := target
        }
        """,
        donor="GLOBEX-0001",
        slug="acme",
        ref="ACME-7001",
        subject="injected",
    )
    assert fetch_ticket(as_actor(ACME_ADMIN), "ACME-7001") is None, (
        "A globex identity managed to insert a Ticket into tenant acme "
        "(ACME-7001 exists for the acme identity)."
    )
    assert fetch_ticket(attacker, "ACME-7001") is None, (
        "A globex identity managed to insert ACME-7001; it is visible to the "
        "attacker itself."
    )


def test_direct_tenant_reassignment_is_refused(as_actor):
    owner = as_actor(ACME_ADMIN)
    try_write(
        owner,
        """
        with target := assert_exists(assert_single((
            select Tenant filter .slug = <str>$slug
        )))
        update Ticket filter .ref = <str>$ref set { tenant := target }
        """,
        slug="globex",
        ref="ACME-0007",
    )
    ticket = fetch_ticket(owner, "ACME-0007")
    assert ticket is not None and ticket["tenant"] == "acme", (
        "ACME-0007 must still belong to tenant acme after an attempt to move "
        f"it to globex, but it is now {ticket}."
    )
    assert fetch_ticket(as_actor(GLOBEX_ADMIN), "ACME-0007") is None, (
        "ACME-0007 became visible to the globex identity, so its tenant link "
        "was reassigned."
    )


def test_direct_readonly_role_cannot_write(as_actor):
    readonly = as_actor(ACME_READONLY)
    owner = as_actor(ACME_ADMIN)

    try_write(
        readonly,
        """
        with
            donor := assert_exists(assert_single((
                select Ticket filter .ref = <str>$donor
            ))),
            target := assert_exists(assert_single((
                select Tenant filter .slug = <str>$slug
            )))
        insert Ticket {
            ref := <str>$ref,
            subject := <str>$subject,
            status := donor.status,
            tenant := target
        }
        """,
        donor="ACME-0001",
        slug="acme",
        ref="ACME-7002",
        subject="readonly insert",
    )
    assert fetch_ticket(owner, "ACME-7002") is None, (
        "A readonly identity managed to insert ACME-7002 into its own tenant."
    )

    try_write(
        readonly,
        "update Ticket filter .ref = <str>$ref set { subject := <str>$subject }",
        ref="ACME-0008",
        subject="readonly update",
    )
    ticket = fetch_ticket(owner, "ACME-0008")
    assert ticket is not None and ticket["subject"] == "acme intake 0008", (
        f"A readonly identity modified ACME-0008: {ticket}."
    )

    try_write(readonly, "delete Ticket filter .ref = <str>$ref", ref="ACME-0009")
    assert fetch_ticket(owner, "ACME-0009") is not None, (
        "A readonly identity deleted ACME-0009."
    )


def test_direct_agent_role_cannot_delete(as_actor):
    agent = as_actor(ACME_AGENT)
    try_write(agent, "delete Ticket filter .ref = <str>$ref", ref="ACME-0010")
    assert fetch_ticket(as_actor(ACME_ADMIN), "ACME-0010") is not None, (
        "An agent identity deleted ACME-0010, which only an admin may do."
    )


# ---------------------------------------------------------------------------
# CLI: read paths
# ---------------------------------------------------------------------------


def test_whoami_happy_path(gel_server):
    payload = cli_json("whoami", "--actor", INITECH_READONLY)
    assert payload == {
        "actor": INITECH_READONLY,
        "tenant": "initech",
        "role": "readonly",
        "visible_tickets": 100,
    }, f"Unexpected whoami output: {payload}."


def test_whoami_unknown_actor(gel_server):
    argv = ["whoami", "--actor", UNKNOWN_ACTOR]
    expect_failure(run_cli(*argv), argv, 4, "error: unknown-actor")


def test_list_tickets_shape_and_ordering(gel_server):
    payload = cli_json("list-tickets", "--actor", INITECH_ADMIN)
    assert isinstance(payload, list), "list-tickets must print a JSON array."
    assert len(payload) == 100, (
        f"The initech identity must see 100 tickets, got {len(payload)}."
    )
    for item in payload:
        assert set(item) == {"ref", "subject", "status", "tenant"}, (
            f"Every ticket object must carry exactly the four contract keys: {item}."
        )
        assert item["tenant"] == "initech", (
            f"list-tickets leaked a foreign tenant: {item}."
        )
    refs = [item["ref"] for item in payload]
    assert refs == sorted(refs), "list-tickets must be sorted by ref ascending."
    assert refs[0] == "INITECH-0001" and refs[-1] == "INITECH-0100", (
        f"Unexpected first/last refs in list-tickets: {refs[0]}, {refs[-1]}."
    )
    assert payload[0] == {
        "ref": "INITECH-0001",
        "subject": "initech intake 0001",
        "status": "open",
        "tenant": "initech",
    }, f"Unexpected first list-tickets element: {payload[0]}."


def test_list_tickets_is_deterministic(gel_server):
    first = run_cli("list-tickets", "--actor", INITECH_ADMIN)
    second = run_cli("list-tickets", "--actor", INITECH_ADMIN)
    assert first.returncode == 0 and second.returncode == 0, (
        "list-tickets must keep succeeding when run repeatedly.\n"
        f"first: {first.stderr}\nsecond: {second.stderr}"
    )
    assert first.stdout == second.stdout, (
        "Two consecutive list-tickets runs printed different output."
    )


def test_list_tickets_is_scoped_to_own_tenant(gel_server):
    payload = cli_json("list-tickets", "--actor", GLOBEX_ADMIN)
    assert len(payload) == 200, (
        f"The globex identity must see 200 tickets, got {len(payload)}."
    )
    assert {item["tenant"] for item in payload} == {"globex"}, (
        "list-tickets returned tickets of other tenants for a globex identity."
    )
    leaked = [
        item["ref"]
        for item in payload
        if item["ref"].startswith(("ACME-", "INITECH-"))
    ]
    assert leaked == [], f"list-tickets leaked foreign refs: {leaked[:10]}."


# ---------------------------------------------------------------------------
# CLI: create paths
# ---------------------------------------------------------------------------


def test_create_ticket_happy_path(as_actor):
    payload = cli_json(
        "create-ticket",
        "--actor",
        ACME_ADMIN,
        "--tenant",
        "acme",
        "--ref",
        "ACME-9001",
        "--subject",
        "escalation drill",
    )
    assert payload == {
        "ref": "ACME-9001",
        "subject": "escalation drill",
        "status": "open",
        "tenant": "acme",
    }, f"Unexpected create-ticket output: {payload}."

    listed = cli_json("list-tickets", "--actor", ACME_AGENT)
    assert any(item["ref"] == "ACME-9001" for item in listed), (
        "ACME-9001 is not visible to another actor of the same tenant."
    )
    assert fetch_ticket(as_actor(GLOBEX_ADMIN), "ACME-9001") is None, (
        "ACME-9001 is visible to a globex identity."
    )
    stored = fetch_ticket(as_actor(ACME_ADMIN), "ACME-9001")
    assert stored == {
        "ref": "ACME-9001",
        "subject": "escalation drill",
        "status": "open",
        "tenant": "acme",
    }, f"ACME-9001 was not persisted as promised: {stored}."


def test_create_ticket_duplicate_ref_conflicts(as_actor):
    argv = [
        "create-ticket",
        "--actor",
        ACME_ADMIN,
        "--tenant",
        "acme",
        "--ref",
        "ACME-9001",
        "--subject",
        "escalation drill",
    ]
    expect_failure(run_cli(*argv), argv, 5, "error: conflict")
    rows = as_actor(ACME_ADMIN).query(
        "select count((select Ticket filter .ref = <str>$ref))",
        ref="ACME-9001",
    )
    assert list(rows) == [1], (
        f"Exactly one ACME-9001 must exist in tenant acme, found {list(rows)}."
    )


def test_create_ticket_with_spoofed_tenant_is_denied(as_actor):
    argv = [
        "create-ticket",
        "--actor",
        GLOBEX_ADMIN,
        "--tenant",
        "acme",
        "--ref",
        "ACME-9002",
        "--subject",
        "spoofed tenant",
    ]
    expect_failure(run_cli(*argv), argv, 3, "error: denied")
    assert fetch_ticket(as_actor(ACME_ADMIN), "ACME-9002") is None, (
        "A spoofed --tenant let a globex identity create ACME-9002 in acme."
    )
    assert fetch_ticket(as_actor(GLOBEX_ADMIN), "ACME-9002") is None, (
        "ACME-9002 was created in the caller's own tenant instead of being "
        "rejected."
    )


def test_create_ticket_readonly_role_is_denied(as_actor):
    argv = [
        "create-ticket",
        "--actor",
        ACME_READONLY,
        "--tenant",
        "acme",
        "--ref",
        "ACME-9003",
        "--subject",
        "readonly attempt",
    ]
    expect_failure(run_cli(*argv), argv, 3, "error: denied")
    assert fetch_ticket(as_actor(ACME_ADMIN), "ACME-9003") is None, (
        "A readonly identity created ACME-9003 through the CLI."
    )


def test_create_ticket_agent_role_is_allowed(gel_server):
    payload = cli_json(
        "create-ticket",
        "--actor",
        ACME_AGENT,
        "--tenant",
        "acme",
        "--ref",
        "ACME-9004",
        "--subject",
        "agent created",
    )
    assert payload == {
        "ref": "ACME-9004",
        "subject": "agent created",
        "status": "open",
        "tenant": "acme",
    }, f"Unexpected create-ticket output for an agent: {payload}."


def test_same_ref_may_be_used_by_two_tenants(as_actor):
    payload = cli_json(
        "create-ticket",
        "--actor",
        ACME_ADMIN,
        "--tenant",
        "acme",
        "--ref",
        "GLOBEX-0001",
        "--subject",
        "same ref other tenant",
    )
    assert payload == {
        "ref": "GLOBEX-0001",
        "subject": "same ref other tenant",
        "status": "open",
        "tenant": "acme",
    }, f"Unexpected create-ticket output: {payload}."

    acme_copy = fetch_ticket(as_actor(ACME_ADMIN), "GLOBEX-0001")
    assert acme_copy is not None and acme_copy["subject"] == "same ref other tenant", (
        f"The acme copy of GLOBEX-0001 is wrong: {acme_copy}."
    )
    globex_copy = fetch_ticket(as_actor(GLOBEX_ADMIN), "GLOBEX-0001")
    assert globex_copy is not None and globex_copy["subject"] == "globex intake 0001", (
        f"The globex ticket GLOBEX-0001 was disturbed: {globex_copy}."
    )


# ---------------------------------------------------------------------------
# CLI: update paths
# ---------------------------------------------------------------------------


def test_update_ticket_happy_path(as_actor):
    payload = cli_json(
        "update-ticket",
        "--actor",
        ACME_AGENT,
        "--ref",
        "ACME-9004",
        "--subject",
        "agent updated",
        "--status",
        "pending",
    )
    assert payload == {
        "ref": "ACME-9004",
        "subject": "agent updated",
        "status": "pending",
        "tenant": "acme",
    }, f"Unexpected update-ticket output: {payload}."
    stored = fetch_ticket(as_actor(ACME_ADMIN), "ACME-9004")
    assert stored == payload, f"ACME-9004 was not persisted as promised: {stored}."


def test_update_ticket_cross_tenant_is_denied(as_actor):
    argv = [
        "update-ticket",
        "--actor",
        GLOBEX_ADMIN,
        "--ref",
        "ACME-9004",
        "--subject",
        "cross tenant",
    ]
    expect_failure(run_cli(*argv), argv, 3, "error: denied")
    stored = fetch_ticket(as_actor(ACME_ADMIN), "ACME-9004")
    assert stored is not None and stored["subject"] == "agent updated", (
        f"A globex identity changed the acme ticket ACME-9004: {stored}."
    )


def test_update_ticket_tenant_move_is_denied(as_actor):
    argv = [
        "update-ticket",
        "--actor",
        ACME_ADMIN,
        "--ref",
        "ACME-9004",
        "--tenant",
        "globex",
    ]
    expect_failure(run_cli(*argv), argv, 3, "error: denied")
    stored = fetch_ticket(as_actor(ACME_ADMIN), "ACME-9004")
    assert stored is not None and stored["tenant"] == "acme", (
        f"ACME-9004 was moved out of tenant acme: {stored}."
    )
    assert fetch_ticket(as_actor(GLOBEX_ADMIN), "ACME-9004") is None, (
        "ACME-9004 became visible to the globex identity after a move attempt."
    )


def test_update_ticket_readonly_role_is_denied(as_actor):
    argv = [
        "update-ticket",
        "--actor",
        ACME_READONLY,
        "--ref",
        "ACME-9004",
        "--subject",
        "readonly update",
    ]
    expect_failure(run_cli(*argv), argv, 3, "error: denied")
    stored = fetch_ticket(as_actor(ACME_ADMIN), "ACME-9004")
    assert stored is not None and stored["subject"] == "agent updated", (
        f"A readonly identity changed ACME-9004: {stored}."
    )


def test_missing_and_foreign_refs_are_indistinguishable(as_actor):
    missing = [
        "update-ticket",
        "--actor",
        ACME_ADMIN,
        "--ref",
        "NOSUCH-0001",
        "--subject",
        "ghost",
    ]
    expect_failure(run_cli(*missing), missing, 3, "error: denied")

    foreign = [
        "update-ticket",
        "--actor",
        ACME_ADMIN,
        "--ref",
        "GLOBEX-0002",
        "--subject",
        "ghost",
    ]
    expect_failure(run_cli(*foreign), foreign, 3, "error: denied")
    stored = fetch_ticket(as_actor(GLOBEX_ADMIN), "GLOBEX-0002")
    assert stored is not None and stored["subject"] == "globex intake 0002", (
        f"GLOBEX-0002 was changed by an acme identity: {stored}."
    )


# ---------------------------------------------------------------------------
# CLI: delete paths
# ---------------------------------------------------------------------------


def test_delete_ticket_agent_and_foreign_identities_are_denied(as_actor):
    by_agent = ["delete-ticket", "--actor", ACME_AGENT, "--ref", "ACME-9004"]
    expect_failure(run_cli(*by_agent), by_agent, 3, "error: denied")
    assert fetch_ticket(as_actor(ACME_ADMIN), "ACME-9004") is not None, (
        "ACME-9004 was deleted by an agent identity."
    )

    by_foreigner = ["delete-ticket", "--actor", GLOBEX_ADMIN, "--ref", "ACME-9004"]
    expect_failure(run_cli(*by_foreigner), by_foreigner, 3, "error: denied")
    assert fetch_ticket(as_actor(ACME_ADMIN), "ACME-9004") is not None, (
        "ACME-9004 was deleted by a globex identity."
    )


def test_delete_ticket_admin_then_denied(as_actor):
    payload = cli_json("delete-ticket", "--actor", ACME_ADMIN, "--ref", "ACME-9004")
    assert payload == {"ref": "ACME-9004", "deleted": True}, (
        f"Unexpected delete-ticket output: {payload}."
    )
    assert fetch_ticket(as_actor(ACME_ADMIN), "ACME-9004") is None, (
        "ACME-9004 still exists after a successful delete."
    )
    argv = ["delete-ticket", "--actor", ACME_ADMIN, "--ref", "ACME-9004"]
    expect_failure(run_cli(*argv), argv, 3, "error: denied")


# ---------------------------------------------------------------------------
# concurrency and repeatability
# ---------------------------------------------------------------------------


def test_concurrent_creation_has_exactly_one_winner(as_actor):
    argv = [
        "create-ticket",
        "--actor",
        ACME_ADMIN,
        "--tenant",
        "acme",
        "--ref",
        "ACME-9500",
        "--subject",
        "race",
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(run_cli, *argv, timeout=300) for _ in range(6)]
        results = [future.result() for future in futures]

    codes = sorted(proc.returncode for proc in results)
    winners = [proc for proc in results if proc.returncode == 0]
    losers = [proc for proc in results if proc.returncode != 0]
    assert len(winners) == 1, (
        "Exactly one of the 6 racing create-ticket processes may succeed, but "
        f"{len(winners)} did (exit codes: {codes}); stderr of the batch: "
        f"{[proc.stderr for proc in results]}."
    )
    assert json.loads(winners[0].stdout) == {
        "ref": "ACME-9500",
        "subject": "race",
        "status": "open",
        "tenant": "acme",
    }, f"The winning process printed {winners[0].stdout!r}."
    for proc in losers:
        expect_failure(proc, argv, 5, "error: conflict")

    rows = as_actor(ACME_ADMIN).query(
        "select count((select Ticket filter .ref = <str>$ref))",
        ref="ACME-9500",
    )
    assert list(rows) == [1], (
        f"Exactly one ACME-9500 must exist after the race, found {list(rows)}."
    )


def test_load_seed_is_repeatable(as_actor):
    payload = cli_json("load-seed", "--file", DATASET_PATH)
    assert payload == {"tenants": 3, "actors": 7, "tickets": 600}, (
        f"Unexpected load-seed output: {payload}."
    )

    client = as_actor()
    assert client.query_single("select count(Tenant)") == 3, (
        "load-seed changed the number of Tenant objects."
    )
    assert client.query_single("select count(Actor)") == 7, (
        "load-seed changed the number of Actor objects."
    )

    for email, slug in (
        (ACME_ADMIN, "acme"),
        (GLOBEX_ADMIN, "globex"),
        (INITECH_READONLY, "initech"),
    ):
        expected = dataset_tickets_by_tenant(slug)
        found = fetch_tickets(as_actor(email), expected)
        assert len(found) == EXPECTED_SEED_COUNTS[slug], (
            f"After re-running load-seed, {email} sees {len(found)} of the "
            f"{EXPECTED_SEED_COUNTS[slug]} seeded tickets of tenant {slug}."
        )
        for ref, entry in expected.items():
            assert found[ref] == {
                "ref": entry["ref"],
                "subject": entry["subject"],
                "status": entry["status"],
                "tenant": entry["tenant"],
            }, (
                f"Ticket {ref} does not match the dataset after re-running "
                f"load-seed: {found[ref]}."
            )
