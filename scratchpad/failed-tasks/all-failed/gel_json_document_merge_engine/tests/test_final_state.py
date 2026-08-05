"""Final-state verification for the gel_json_document_merge_engine task.

Every check drives the real CLI (`python3 /home/user/settings-engine/settings_engine.py`)
against the real local Gel 7.1 server and inspects the persisted database state with the
Gel Python client.
"""

import concurrent.futures
import json
import os
import subprocess
import time

import pytest

PROJECT_DIR = "/home/user/settings-engine"
SOLUTION_PATH = os.path.join(PROJECT_DIR, "settings_engine.py")
SCHEMA_DIR = os.path.join(PROJECT_DIR, "dbschema")
START_SCRIPT = "/usr/local/bin/gel-start.sh"

READY_TIMEOUT_SEC = 420
CLI_TIMEOUT_SEC = 180
CONCURRENCY = 8
CONCURRENT_WALL_CLOCK_LIMIT_SEC = 300

# --------------------------------------------------------------------------------------
# Baseline values of the mutable fixtures (restored before each mutating check so that the
# whole suite is re-runnable).
# --------------------------------------------------------------------------------------
PATCH_BASE_DOC = {"a": {"b": 1, "c": [1, 2]}, "z": "zed"}
PATCH_USER_DOC = {"a": {"b": 2}}
PATCH_SHADOW_DOC = {"shadow": True}
RACE_BASE_DOC = {"seed": "kept", "counterparts": {"pre": 1}}
RACE_USER_DOC = {}

# Seeded, read-only fixtures used for regression checks.
BASE_GLOBAL_DOC = {
    "theme": "light",
    "limits": {"cpu": 2, "mem": 512, "nested": {"deep": {"a": 1, "b": 2}}},
    "features": ["a", "b"],
    "flags": {"beta": False, "gamma": True},
    "keep": "global",
    "": "empty-key-global",
    "1": "one",
    "2": "two",
    "10": "ten",
    "id": "global-id",
    "__type__": "global-type",
}
USER_ALICE_DOC = {
    "theme": "solar",
    "limits": {"cpu": 8},
    "keep": None,
    "extra": {"x": {"y": {"z": True}}},
    "id": 5,
}
GROUP_ENGINEERING_DOC = {
    "theme": "dark",
    "limits": {"mem": 1024, "nested": {"deep": {"b": None, "c": 3}}},
    "features": ["c"],
    "flags": None,
    "日本語": "group",
    "emoji🙂": "group",
}
USER_BOB_DOC = {
    "limits": {},
    "features": {},
    "flags": {"beta": {}},
    "theme": {"nested": True},
    "": None,
    "missing": None,
    "1": None,
}

# --------------------------------------------------------------------------------------
# Expected envelopes
# --------------------------------------------------------------------------------------
EXPECTED_ACME_ALICE = {
    "applied_layers": ["base.global", "user.alice", "group.engineering"],
    "deleted_paths": [["flags"], ["keep"], ["limits", "nested", "deep", "b"]],
    "document": {
        "": "empty-key-global",
        "1": "one",
        "10": "ten",
        "2": "two",
        "__type__": "global-type",
        "emoji🙂": "group",
        "extra": {"x": {"y": {"z": True}}},
        "features": ["c"],
        "id": 5,
        "limits": {"cpu": 8, "mem": 1024, "nested": {"deep": {"a": 1, "c": 3}}},
        "theme": "dark",
        "日本語": "group",
    },
    "revision": 3,
    "slug": "acme-alice",
}

EXPECTED_ACME_BOB = {
    "applied_layers": ["base.global", "user.bob"],
    "deleted_paths": [[""], ["1"]],
    "document": {
        "10": "ten",
        "2": "two",
        "__type__": "global-type",
        "features": {},
        "flags": {"beta": {}, "gamma": True},
        "id": "global-id",
        "keep": "global",
        "limits": {"cpu": 2, "mem": 512, "nested": {"deep": {"a": 1, "b": 2}}},
        "theme": {"nested": True},
    },
    "revision": 3,
    "slug": "acme-bob",
}

EXPECTED_SOLO_EMPTY = {
    "applied_layers": ["base.empty"],
    "deleted_paths": [],
    "document": {},
    "revision": 0,
    "slug": "solo-empty",
}

PATCH_P1 = {
    "a": {"c": None, "d": {"e": [9]}},
    "new": {"deep": {"x": 1}},
    "z": None,
}
PATCH_P1_STORED = {
    "a": {"b": 2, "c": None, "d": {"e": [9]}},
    "new": {"deep": {"x": 1}},
    "z": None,
}
PATCH_P1_ENVELOPE_DOCUMENT = {
    "a": {"b": 2, "d": {"e": [9]}},
    "new": {"deep": {"x": 1}},
}
PATCH_P1_DELETED_PATHS = [["a", "c"], ["z"]]

PATCH_P2 = {"shadow2": {"k": False}}
PATCH_SHADOW_AFTER_P2 = {"shadow": True, "shadow2": {"k": False}}
PATCH_TARGET_BASELINE_DOCUMENT = {"a": {"b": 2, "c": [1, 2]}, "z": "zed"}


def canonical(value) -> str:
    """The exact serialization the task description requires."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


# --------------------------------------------------------------------------------------
# Server / client fixtures
# --------------------------------------------------------------------------------------
def _start_server() -> None:
    assert os.path.isfile(START_SCRIPT), f"{START_SCRIPT} is missing from the image."
    subprocess.run(
        ["/bin/sh", START_SCRIPT],
        check=False,
        capture_output=True,
        timeout=READY_TIMEOUT_SEC,
    )


def _wait_until_ready():
    import gel  # noqa: PLC0415

    deadline = time.time() + READY_TIMEOUT_SEC
    last_error = None
    while time.time() < deadline:
        cl = None
        try:
            cl = gel.create_client(timeout=30)
            cl.query_single("select 1")
            return cl
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if cl is not None:
                try:
                    cl.close()
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(3)
    raise AssertionError(f"The local Gel server never became ready: {last_error!r}")


@pytest.fixture(scope="session")
def client():
    """Starts the local Gel server if needed and yields a connected client."""
    _start_server()
    cl = _wait_until_ready()
    try:
        yield cl
    finally:
        try:
            cl.close()
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------
def run_cli(client, *args, timeout=CLI_TIMEOUT_SEC):
    """Runs the task CLI from the project directory; returns (rc, stdout, stderr)."""
    assert client is not None
    assert os.path.isfile(SOLUTION_PATH), f"{SOLUTION_PATH} does not exist."
    proc = subprocess.run(
        ["python3", SOLUTION_PATH, *args],
        cwd=PROJECT_DIR,
        capture_output=True,
        timeout=timeout,
    )
    return (
        proc.returncode,
        proc.stdout.decode("utf-8", errors="replace"),
        proc.stderr.decode("utf-8", errors="replace"),
    )


def assert_envelope(stdout: str, expected: dict, context: str) -> None:
    expected_line = canonical(expected)
    got = stdout
    assert got.count("\n") <= 1, (
        f"{context}: stdout must be a single line, got {got.count(chr(10)) + 1} lines: {got!r}"
    )
    assert got.rstrip("\n") == expected_line, (
        f"{context}: unexpected stdout.\nExpected: {expected_line}\nActual:   {got.rstrip(chr(10))}"
    )


def query_json(client, query: str, **kwargs):
    return json.loads(client.query_json(query, **kwargs))


def get_layer(client, key: str) -> dict:
    rows = query_json(
        client,
        "select SettingsLayer { key, tier, active, revision, doc } filter .key = <str>$k",
        k=key,
    )
    assert len(rows) == 1, f"Expected exactly one SettingsLayer with key {key!r}, got {len(rows)}."
    row = dict(rows[0])
    row["doc"] = json.loads(row["doc"]) if isinstance(row["doc"], str) else row["doc"]
    return row


def set_layer(client, key: str, doc, revision: int, active: bool = True) -> None:
    client.query(
        """
        update SettingsLayer filter .key = <str>$k set {
            doc := to_json(<str>$d),
            revision := <int64>$r,
            active := <bool>$a
        }
        """,
        k=key,
        d=json.dumps(doc, ensure_ascii=False),
        r=revision,
        a=active,
    )


def reset_patch_fixtures(client) -> None:
    set_layer(client, "patch.base", PATCH_BASE_DOC, 0, True)
    set_layer(client, "patch.user", PATCH_USER_DOC, 0, True)
    set_layer(client, "patch.shadow", PATCH_SHADOW_DOC, 0, False)


def reset_race_fixtures(client) -> None:
    set_layer(client, "race.base", RACE_BASE_DOC, 0, True)
    set_layer(client, "race.user", RACE_USER_DOC, 0, True)


def write_json_file(name: str, value) -> str:
    path = os.path.join(PROJECT_DIR, name)
    if os.path.exists(path):
        os.remove(path)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False))
    return path


def write_raw_file(name: str, text: str) -> str:
    path = os.path.join(PROJECT_DIR, name)
    if os.path.exists(path):
        os.remove(path)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


def create_dynamic_fixtures(client) -> None:
    """(Re)creates the record/layers that only exist at verification time."""
    client.execute("delete SettingsRecord filter .slug = 'zz-dynamic'")
    client.execute("delete SettingsLayer filter .key like 'dyn.%'")
    for key, tier, doc, active, revision in (
        ("dyn.mid", "group", {"p": {"q": 9}, "only": "mid"}, True, 7),
        ("dyn.base", "global", {"p": {"q": 1, "r": 2}, "arr": [1, 2, 3]}, True, 4),
        ("dyn.off", "user", {"nope": 1}, False, 100),
        ("dyn.top", "user", {"p": {"q": 42}}, False, 1),
    ):
        client.query(
            """
            insert SettingsLayer {
                key := <str>$k,
                tier := <str>$t,
                doc := to_json(<str>$d),
                active := <bool>$a,
                revision := <int64>$r
            }
            """,
            k=key,
            t=tier,
            d=json.dumps(doc, ensure_ascii=False),
            a=active,
            r=revision,
        )
    client.execute(
        """
        insert SettingsRecord {
            slug := 'zz-dynamic',
            label := 'Dynamic',
            layers := assert_distinct((
                with pairs := {
                    ('dyn.mid', 5), ('dyn.base', 100), ('dyn.off', 7), ('dyn.top', 200)
                }
                for pair in pairs union (
                    select SettingsLayer { @precedence := pair.1 } filter .key = pair.0
                )
            ))
        }
        """
    )


# --------------------------------------------------------------------------------------
# 0. Entrypoint
# --------------------------------------------------------------------------------------
def test_cli_entrypoint_exists(client):
    assert client is not None
    assert os.path.isfile(SOLUTION_PATH), (
        f"The CLI entrypoint {SOLUTION_PATH} does not exist."
    )


# --------------------------------------------------------------------------------------
# 1. Layered resolution with out-of-tier precedence, deletions, unicode, odd keys
# --------------------------------------------------------------------------------------
def test_resolve_layered_record_acme_alice(client):
    rc, out, err = run_cli(client, "resolve", "--slug", "acme-alice")
    assert rc == 0, f"resolve --slug acme-alice must exit 0, got {rc}. stderr={err!r}"
    assert_envelope(out, EXPECTED_ACME_ALICE, "resolve acme-alice")


def test_resolve_acme_alice_raw_serialization(client):
    rc, out, err = run_cli(client, "resolve", "--slug", "acme-alice")
    assert rc == 0, f"resolve --slug acme-alice must exit 0, got {rc}. stderr={err!r}"
    line = out.rstrip("\n")
    assert out.count("\n") <= 1, f"stdout must contain a single line, got: {out!r}"
    pos_1 = line.index('"1":')
    pos_10 = line.index('"10":')
    pos_2 = line.index('"2":')
    assert pos_1 < pos_10 < pos_2, (
        "numeric-string keys must be ordered by Unicode code point "
        f'("1", "10", "2"), got positions {pos_1}, {pos_10}, {pos_2} in {line!r}'
    )
    assert "日本語" in line, "the non-ASCII key 日本語 must be written literally"
    assert "emoji🙂" in line, "the non-ASCII key emoji🙂 must be written literally"
    assert "\\u" not in line, f"no \\uXXXX escapes are allowed in the output: {line!r}"
    assert ", " not in line, f"no whitespace is allowed after ',': {line!r}"
    assert ": " not in line, f"no whitespace is allowed after ':': {line!r}"


# --------------------------------------------------------------------------------------
# 2. Empty objects, object-over-scalar/array conflicts, nulls for absent keys
# --------------------------------------------------------------------------------------
def test_resolve_edge_case_record_acme_bob(client):
    rc, out, err = run_cli(client, "resolve", "--slug", "acme-bob")
    assert rc == 0, f"resolve --slug acme-bob must exit 0, got {rc}. stderr={err!r}"
    assert_envelope(out, EXPECTED_ACME_BOB, "resolve acme-bob")


# --------------------------------------------------------------------------------------
# 3./4. Empty documents and records without applicable layers
# --------------------------------------------------------------------------------------
def test_resolve_empty_document_record(client):
    rc, out, err = run_cli(client, "resolve", "--slug", "solo-empty")
    assert rc == 0, f"resolve --slug solo-empty must exit 0, got {rc}. stderr={err!r}"
    assert_envelope(out, EXPECTED_SOLO_EMPTY, "resolve solo-empty")


@pytest.mark.parametrize("slug", ["ghost-town", "orphan-record"])
def test_resolve_records_without_active_layers(client, slug):
    rc, out, err = run_cli(client, "resolve", "--slug", slug)
    assert rc == 0, f"resolve --slug {slug} must exit 0, got {rc}. stderr={err!r}"
    expected = {
        "applied_layers": [],
        "deleted_paths": [],
        "document": {},
        "revision": 0,
        "slug": slug,
    }
    assert_envelope(out, expected, f"resolve {slug}")


# --------------------------------------------------------------------------------------
# 5. Bulk seeded records
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("index", [0, 157, 299])
def test_resolve_filler_records(client, index):
    slug = f"fill-{index:04d}"
    expected = {
        "applied_layers": [f"{slug}.g", f"{slug}.u"],
        "deleted_paths": [],
        "document": {"g": {"i": index, "x": True}, "u": {"i": index}},
        "revision": 0,
        "slug": slug,
    }
    rc, out, err = run_cli(client, "resolve", "--slug", slug)
    assert rc == 0, f"resolve --slug {slug} must exit 0, got {rc}. stderr={err!r}"
    assert_envelope(out, expected, f"resolve {slug}")


# --------------------------------------------------------------------------------------
# 6. Live database state (anti-cheat): data created/changed after the agent finished
# --------------------------------------------------------------------------------------
def test_resolve_reads_live_database_state(client):
    create_dynamic_fixtures(client)

    expected_first = {
        "applied_layers": ["dyn.mid", "dyn.base"],
        "deleted_paths": [],
        "document": {"arr": [1, 2, 3], "only": "mid", "p": {"q": 1, "r": 2}},
        "revision": 11,
        "slug": "zz-dynamic",
    }
    rc, out, err = run_cli(client, "resolve", "--slug", "zz-dynamic")
    assert rc == 0, f"resolve --slug zz-dynamic must exit 0, got {rc}. stderr={err!r}"
    assert_envelope(out, expected_first, "resolve zz-dynamic (first pass)")

    client.execute("update SettingsLayer filter .key = 'dyn.off' set { active := true }")
    set_layer(client, "dyn.base", {"p": None, "arr": []}, 4, True)

    expected_second = {
        "applied_layers": ["dyn.mid", "dyn.off", "dyn.base"],
        "deleted_paths": [["p"]],
        "document": {"arr": [], "nope": 1, "only": "mid"},
        "revision": 111,
        "slug": "zz-dynamic",
    }
    rc, out, err = run_cli(client, "resolve", "--slug", "zz-dynamic")
    assert rc == 0, f"resolve --slug zz-dynamic must exit 0, got {rc}. stderr={err!r}"
    assert_envelope(out, expected_second, "resolve zz-dynamic (after live changes)")

    # A later layer re-creates the removed key: the removal must stay reported.
    client.execute("update SettingsLayer filter .key = 'dyn.top' set { active := true }")
    expected_third = {
        "applied_layers": ["dyn.mid", "dyn.off", "dyn.base", "dyn.top"],
        "deleted_paths": [["p"]],
        "document": {"arr": [], "nope": 1, "only": "mid", "p": {"q": 42}},
        "revision": 112,
        "slug": "zz-dynamic",
    }
    rc, out, err = run_cli(client, "resolve", "--slug", "zz-dynamic")
    assert rc == 0, f"resolve --slug zz-dynamic must exit 0, got {rc}. stderr={err!r}"
    assert_envelope(out, expected_third, "resolve zz-dynamic (deleted key re-created later)")


# --------------------------------------------------------------------------------------
# 7. resolve is a pure read path
# --------------------------------------------------------------------------------------
def test_resolve_is_deterministic_and_read_only(client):
    before = get_layer(client, "base.global")
    rc1, out1, err1 = run_cli(client, "resolve", "--slug", "acme-alice")
    rc2, out2, err2 = run_cli(client, "resolve", "--slug", "acme-alice")
    assert rc1 == 0 and rc2 == 0, (
        f"repeated resolve calls must exit 0, got {rc1} and {rc2}. "
        f"stderr1={err1!r} stderr2={err2!r}"
    )
    assert out1 == out2, (
        f"repeated resolve calls must produce byte-identical stdout:\n{out1!r}\n{out2!r}"
    )
    after = get_layer(client, "base.global")
    assert after["revision"] == 3, (
        f"resolve must not write: base.global.revision changed to {after['revision']}."
    )
    assert after["doc"] == before["doc"] == BASE_GLOBAL_DOC, (
        "resolve must not modify base.global.doc."
    )
    alice = get_layer(client, "user.alice")
    assert alice["revision"] == 0, (
        f"resolve must not write: user.alice.revision is {alice['revision']}, expected 0."
    )


# --------------------------------------------------------------------------------------
# 8. patch: recursive merge with stored nulls, revision bump, isolation
# --------------------------------------------------------------------------------------
def test_patch_merges_and_stores_nulls(client):
    reset_patch_fixtures(client)
    write_json_file("p1.json", PATCH_P1)

    rc, out, err = run_cli(
        client, "patch", "--slug", "patch-target", "--layer", "patch.user", "--file", "p1.json"
    )
    assert rc == 0, f"patch must exit 0, got {rc}. stderr={err!r}"
    expected = {
        "applied_layers": ["patch.base", "patch.user"],
        "deleted_paths": PATCH_P1_DELETED_PATHS,
        "document": PATCH_P1_ENVELOPE_DOCUMENT,
        "revision": 1,
        "slug": "patch-target",
    }
    assert_envelope(out, expected, "patch patch.user")

    stored = get_layer(client, "patch.user")
    assert stored["doc"] == PATCH_P1_STORED, (
        f"patch.user.doc in the database is {stored['doc']!r}, expected {PATCH_P1_STORED!r}"
    )
    assert stored["revision"] == 1, (
        f"patch.user.revision must be 1 after one patch, got {stored['revision']}."
    )
    assert stored["active"] is True, "patch must not change the layer's active flag."

    base = get_layer(client, "patch.base")
    assert base["doc"] == PATCH_BASE_DOC, (
        f"patch.base.doc must be untouched, got {base['doc']!r}"
    )
    assert base["revision"] == 0, (
        f"patch.base.revision must stay 0, got {base['revision']}."
    )
    shadow = get_layer(client, "patch.shadow")
    assert shadow["doc"] == PATCH_SHADOW_DOC and shadow["revision"] == 0, (
        f"patch.shadow must be untouched, got {shadow!r}"
    )
    chain = sorted(
        layer["key"]
        for layer in query_json(
            client,
            "select SettingsRecord { layers: { key } } filter .slug = 'patch-target'",
        )[0]["layers"]
    )
    assert chain == ["patch.base", "patch.shadow", "patch.user"], (
        f"the chain of patch-target must not change, got {chain}"
    )


# --------------------------------------------------------------------------------------
# 9. patch is idempotent in content, but always bumps the revision
# --------------------------------------------------------------------------------------
def test_patch_repeated_application(client):
    reset_patch_fixtures(client)
    write_json_file("p1.json", PATCH_P1)
    args = (
        "patch",
        "--slug",
        "patch-target",
        "--layer",
        "patch.user",
        "--file",
        "p1.json",
    )

    rc1, out1, err1 = run_cli(client, *args)
    assert rc1 == 0, f"first patch must exit 0, got {rc1}. stderr={err1!r}"
    doc_after_first = get_layer(client, "patch.user")["doc"]

    rc2, out2, err2 = run_cli(client, *args)
    assert rc2 == 0, f"second patch must exit 0, got {rc2}. stderr={err2!r}"
    expected = {
        "applied_layers": ["patch.base", "patch.user"],
        "deleted_paths": PATCH_P1_DELETED_PATHS,
        "document": PATCH_P1_ENVELOPE_DOCUMENT,
        "revision": 2,
        "slug": "patch-target",
    }
    assert_envelope(out2, expected, "second patch patch.user")
    assert json.loads(out1.rstrip("\n"))["revision"] == 1, (
        f"the first patch must report revision 1, got: {out1!r}"
    )

    stored = get_layer(client, "patch.user")
    assert stored["doc"] == doc_after_first == PATCH_P1_STORED, (
        f"re-applying the same patch must not change the stored document, got {stored['doc']!r}"
    )
    assert stored["revision"] == 2, (
        f"patch.user.revision must be 2 after two patches, got {stored['revision']}."
    )


# --------------------------------------------------------------------------------------
# 10. Patching an inactive layer changes only that layer
# --------------------------------------------------------------------------------------
def test_patch_inactive_layer_does_not_affect_resolution(client):
    reset_patch_fixtures(client)
    write_json_file("p2.json", PATCH_P2)

    rc, out, err = run_cli(
        client, "patch", "--slug", "patch-target", "--layer", "patch.shadow", "--file", "p2.json"
    )
    assert rc == 0, f"patching an inactive layer must exit 0, got {rc}. stderr={err!r}"
    expected = {
        "applied_layers": ["patch.base", "patch.user"],
        "deleted_paths": [],
        "document": PATCH_TARGET_BASELINE_DOCUMENT,
        "revision": 0,
        "slug": "patch-target",
    }
    assert_envelope(out, expected, "patch patch.shadow")

    shadow = get_layer(client, "patch.shadow")
    assert shadow["doc"] == PATCH_SHADOW_AFTER_P2, (
        f"patch.shadow.doc is {shadow['doc']!r}, expected {PATCH_SHADOW_AFTER_P2!r}"
    )
    assert shadow["revision"] == 1, (
        f"patch.shadow.revision must be 1, got {shadow['revision']}."
    )
    assert shadow["active"] is False, "patching must not activate an inactive layer."

    user = get_layer(client, "patch.user")
    assert user["doc"] == PATCH_USER_DOC and user["revision"] == 0, (
        f"patch.user must be untouched, got {user!r}"
    )


# --------------------------------------------------------------------------------------
# 11.-14. Error classes, precedence and absence of side effects
# --------------------------------------------------------------------------------------
def test_unknown_record_exits_3(client):
    reset_patch_fixtures(client)
    write_json_file("p1.json", PATCH_P1)

    rc, out, err = run_cli(client, "resolve", "--slug", "nope-nope")
    assert rc == 3, f"resolve for an unknown slug must exit 3, got {rc}. stderr={err!r}"
    assert out == "", f"stdout must be empty on failure, got {out!r}"
    assert err.strip() != "", "a diagnostic must be written to stderr on failure"

    rc, out, err = run_cli(
        client, "patch", "--slug", "nope-nope", "--layer", "patch.user", "--file", "p1.json"
    )
    assert rc == 3, f"patch for an unknown slug must exit 3, got {rc}. stderr={err!r}"
    assert out == "", f"stdout must be empty on failure, got {out!r}"
    assert err.strip() != "", "a diagnostic must be written to stderr on failure"

    user = get_layer(client, "patch.user")
    assert user["revision"] == 0 and user["doc"] == PATCH_USER_DOC, (
        f"a failed patch must not touch the database, got {user!r}"
    )


def test_error_precedence_record_before_layer_and_file(client):
    rc, out, err = run_cli(
        client,
        "patch",
        "--slug",
        "nope-nope",
        "--layer",
        "no.such.layer",
        "--file",
        os.path.join(PROJECT_DIR, "does-not-exist.json"),
    )
    assert rc == 3, (
        f"the unknown-record error must win over the layer/file errors, got exit {rc}. "
        f"stderr={err!r}"
    )
    assert out == "", f"stdout must be empty on failure, got {out!r}"


def test_layer_outside_chain_exits_4(client):
    reset_patch_fixtures(client)
    write_json_file("p1.json", PATCH_P1)

    for layer in ("base.global", "no.such.layer"):
        rc, out, err = run_cli(
            client, "patch", "--slug", "patch-target", "--layer", layer, "--file", "p1.json"
        )
        assert rc == 4, (
            f"patching layer {layer!r} which is not in the chain of patch-target must exit 4, "
            f"got {rc}. stderr={err!r}"
        )
        assert out == "", f"stdout must be empty on failure, got {out!r}"
        assert err.strip() != "", "a diagnostic must be written to stderr on failure"

    base_global = get_layer(client, "base.global")
    assert base_global["revision"] == 3 and base_global["doc"] == BASE_GLOBAL_DOC, (
        f"base.global must not be modified by a rejected patch, got {base_global!r}"
    )
    user = get_layer(client, "patch.user")
    assert user["revision"] == 0 and user["doc"] == PATCH_USER_DOC, (
        f"patch.user must not be modified by a rejected patch, got {user!r}"
    )


def test_invalid_patch_documents_exit_5(client):
    reset_patch_fixtures(client)
    write_raw_file("bad1.json", '{"a": ')
    write_raw_file("bad2.json", "[1, 2, 3]")
    write_raw_file("bad3.json", '"just a string"')
    missing = os.path.join(PROJECT_DIR, "does-not-exist.json")
    if os.path.exists(missing):
        os.remove(missing)

    for target in ("bad1.json", "bad2.json", "bad3.json", missing):
        rc, out, err = run_cli(
            client, "patch", "--slug", "patch-target", "--layer", "patch.user", "--file", target
        )
        assert rc == 5, (
            f"an invalid patch document ({target}) must exit 5, got {rc}. stderr={err!r}"
        )
        assert out == "", f"stdout must be empty on failure, got {out!r}"
        assert err.strip() != "", "a diagnostic must be written to stderr on failure"

    user = get_layer(client, "patch.user")
    assert user["revision"] == 0 and user["doc"] == PATCH_USER_DOC, (
        f"a rejected patch must leave the database unchanged, got {user!r}"
    )


# --------------------------------------------------------------------------------------
# 15. Concurrency invariants
# --------------------------------------------------------------------------------------
def test_concurrent_patches_lose_nothing(client):
    reset_race_fixtures(client)
    for index in range(CONCURRENCY):
        write_json_file(
            f"race_{index}.json",
            {f"k{index}": {"n": index}, "shared": {f"s{index}": index}},
        )

    def invoke(index):
        return run_cli(
            client,
            "patch",
            "--slug",
            "race-target",
            "--layer",
            "race.user",
            "--file",
            f"race_{index}.json",
            timeout=CONCURRENT_WALL_CLOCK_LIMIT_SEC,
        )

    started = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        results = list(pool.map(invoke, range(CONCURRENCY)))
    elapsed = time.time() - started

    for index, (rc, out, err) in enumerate(results):
        assert rc == 0, (
            f"concurrent patch #{index} must exit 0, got {rc}. stdout={out!r} stderr={err!r}"
        )
    assert elapsed < CONCURRENT_WALL_CLOCK_LIMIT_SEC, (
        f"{CONCURRENCY} concurrent patches took {elapsed:.1f}s, which exceeds the allowed "
        f"{CONCURRENT_WALL_CLOCK_LIMIT_SEC}s"
    )

    expected_doc: dict = {f"k{i}": {"n": i} for i in range(CONCURRENCY)}
    expected_doc["shared"] = {f"s{i}": i for i in range(CONCURRENCY)}

    stored = get_layer(client, "race.user")
    assert stored["revision"] == CONCURRENCY, (
        f"race.user.revision must be exactly {CONCURRENCY} after {CONCURRENCY} concurrent "
        f"patches, got {stored['revision']} (lost or duplicated updates)"
    )
    assert stored["doc"] == expected_doc, (
        f"race.user.doc lost concurrent updates.\nExpected: {expected_doc!r}\n"
        f"Actual:   {stored['doc']!r}"
    )

    base = get_layer(client, "race.base")
    assert base["doc"] == RACE_BASE_DOC and base["revision"] == 0, (
        f"race.base must be untouched by the concurrent patches, got {base!r}"
    )

    expected_document: dict = dict(expected_doc)
    expected_document["counterparts"] = {"pre": 1}
    expected_document["seed"] = "kept"
    expected_envelope = {
        "applied_layers": ["race.base", "race.user"],
        "deleted_paths": [],
        "document": expected_document,
        "revision": CONCURRENCY,
        "slug": "race-target",
    }
    rc, out, err = run_cli(client, "resolve", "--slug", "race-target")
    assert rc == 0, f"resolve --slug race-target must exit 0, got {rc}. stderr={err!r}"
    assert_envelope(out, expected_envelope, "resolve race-target after concurrent patches")


# --------------------------------------------------------------------------------------
# 16. Regression: the rest of the seeded catalog is intact
# --------------------------------------------------------------------------------------
def test_seeded_catalog_is_intact(client):
    records = client.query_single(
        "select count((select SettingsRecord filter not .slug like 'zz-%'))"
    )
    layers = client.query_single(
        "select count((select SettingsLayer filter not .key like 'dyn.%'))"
    )
    assert records == 307, f"Expected 307 seeded SettingsRecord objects, found {records}."
    assert layers == 914, f"Expected 914 seeded SettingsLayer objects, found {layers}."

    rows = query_json(
        client,
        "select SettingsRecord { slug, label, layers: { key } } filter .slug = 'acme-alice'",
    )
    assert len(rows) == 1, "The seeded record acme-alice disappeared."
    assert rows[0]["label"] == "Acme / Alice", (
        f"acme-alice.label must stay 'Acme / Alice', got {rows[0]['label']!r}"
    )
    chain = sorted(layer["key"] for layer in rows[0]["layers"])
    assert chain == ["base.global", "group.engineering", "override.disabled", "user.alice"], (
        f"the chain of acme-alice changed: {chain}"
    )

    for key, expected_doc, expected_rev in (
        ("base.global", BASE_GLOBAL_DOC, 3),
        ("user.alice", USER_ALICE_DOC, 0),
        ("group.engineering", GROUP_ENGINEERING_DOC, 0),
        ("user.bob", USER_BOB_DOC, 0),
        ("base.empty", {}, 0),
        ("race.base", RACE_BASE_DOC, 0),
    ):
        layer = get_layer(client, key)
        assert layer["doc"] == expected_doc, (
            f"{key}.doc was modified.\nExpected: {expected_doc!r}\nActual:   {layer['doc']!r}"
        )
        assert layer["revision"] == expected_rev, (
            f"{key}.revision must stay {expected_rev}, got {layer['revision']}."
        )

    disabled = get_layer(client, "override.disabled")
    assert disabled["revision"] == 41, (
        f"override.disabled.revision must stay 41, got {disabled['revision']}."
    )
    assert disabled["active"] is False, "override.disabled must stay inactive."

    dirty_filler = client.query_single(
        "select count((select SettingsLayer filter .key like 'fill-%' and .revision != 0))"
    )
    assert dirty_filler == 0, (
        f"{dirty_filler} filler layers have a non-zero revision; they must not be touched."
    )

    inactive_filler = client.query_single(
        "select count((select SettingsLayer filter .key like 'fill-%.x' and .active))"
    )
    assert inactive_filler == 0, (
        f"{inactive_filler} 'fill-*.x' layers became active; they must stay inactive."
    )


# --------------------------------------------------------------------------------------
# 17. The seeded project/migration state still works through the Gel CLI
# --------------------------------------------------------------------------------------
def test_migration_status_still_in_sync(client):
    assert client is not None
    proc = subprocess.run(
        ["gel", "migration", "status", "--schema-dir", SCHEMA_DIR],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, (
        "gel migration status must still succeed: "
        f"rc={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )


def test_gel_cli_query_still_works(client):
    assert client is not None
    proc = subprocess.run(
        ["gel", "query", "select 1"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, (
        f"gel query failed: rc={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert "1" in proc.stdout, f"Unexpected CLI output: {proc.stdout!r}"
