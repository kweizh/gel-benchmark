"""Final-state verification for the gel_json_preferences_merge_ts task.

Everything is verified against the running system: the local Gel 6 server is
introspected/queried with the `gel` CLI, and the preference service is driven
through the JSON-Lines command line contract
(`node_modules/.bin/tsx src/cli.ts` executed inside /home/user/prefsvc).
"""

import concurrent.futures
import copy
import glob
import json
import os
import random
import subprocess
import time
from datetime import datetime

import pytest

PROJECT_DIR = "/home/user/prefsvc"
SCHEMA_DIR = os.path.join(PROJECT_DIR, "dbschema")
MIGRATIONS_DIR = os.path.join(SCHEMA_DIR, "migrations")
START_SCRIPT = "/usr/local/bin/gel-start.sh"
TSX = os.path.join(PROJECT_DIR, "node_modules", ".bin", "tsx")
PROBE_PATH = os.path.join(PROJECT_DIR, ".harbor_probe.ts")

SYSTEM_DEFAULTS = {
    "ui": {
        "theme": "light",
        "density": "comfortable",
        "sidebar": {"visible": True, "width": 280},
        "pinned": [],
    },
    "notifications": {
        "email": {"digest": "daily", "marketing": False},
        "push": {"enabled": False, "quiet_hours": []},
        "batch_size": 25,
    },
    "privacy": {"analytics": True, "share_profile": False},
    "editor": {
        "tab_size": 4,
        "soft_wrap": True,
        "keymap": "default",
        "rulers": [80, 120],
    },
}

SEEDED = {
    "ada@example.com": {
        "ui": {"theme": "dark", "sidebar": {"width": 320}},
        "editor": {"tab_size": 2},
    },
    "linus@example.com": {
        "notifications": {
            "email": {"digest": "weekly"},
            "push": {"enabled": True, "quiet_hours": [22, 7]},
        }
    },
    "grace@example.com": {},
    "alan@example.com": {
        "ui": {"density": "compact", "pinned": ["inbox", "drafts"]},
        "privacy": {"analytics": False},
    },
    "edsger@example.com": {
        "editor": {"keymap": "vim", "rulers": [100], "soft_wrap": False}
    },
    "barbara@example.com": {"ui": {"theme": "solarized"}},
    "dennis@example.com": {
        "notifications": {"batch_size": 5},
        "privacy": {"share_profile": True},
    },
    "hedy@example.com": {
        "ui": {"theme": "light", "sidebar": {"visible": False}},
        "notifications": {"batch_size": 10},
    },
    "katherine@example.com": {},
}

ERROR_NAMES = {
    "MALFORMED_PATCH": "MalformedPatchError",
    "UNKNOWN_USER": "UnknownUserError",
    "UNKNOWN_NAMESPACE": "UnknownNamespaceError",
    "TYPE_MISMATCH": "TypeMismatchError",
    "STALE_VERSION": "StaleVersionError",
}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _run(args, cwd=None, timeout=180, stdin_data=None):
    return subprocess.run(
        args,
        cwd=cwd,
        input=stdin_data,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.fixture(scope="session")
def server():
    """Start the local Gel server (idempotent) before any DB/CLI interaction."""
    proc = _run(["bash", START_SCRIPT], timeout=300)
    assert proc.returncode == 0, (
        f"{START_SCRIPT} failed (rc={proc.returncode}).\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    probe = _run(["gel", "query", "-F", "json-lines", "select 1"], timeout=120)
    assert probe.returncode == 0, (
        "The Gel server does not answer queries.\n"
        f"stdout: {probe.stdout}\nstderr: {probe.stderr}"
    )
    return True


def gel_query(query, timeout=180):
    proc = _run(["gel", "query", "-F", "json-lines", query], timeout=timeout)
    assert proc.returncode == 0, (
        f"EdgeQL query failed: {query}\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def as_doc(value):
    """Decode a json-typed column that may arrive inline or as a JSON string."""
    if isinstance(value, str):
        return json.loads(value)
    return value


def canon(value):
    """Canonical JSON text, so `true` and `1` never compare equal."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def same(left, right):
    return canon(left) == canon(right)


def cli(requests_, timeout=180):
    """Feed JSON Lines requests to the service CLI and return parsed responses."""
    payload = "".join(json.dumps(req) + "\n" for req in requests_)
    proc = _run([os.path.join("node_modules", ".bin", "tsx"), os.path.join("src", "cli.ts")],
                cwd=PROJECT_DIR, timeout=timeout, stdin_data=payload)
    assert proc.returncode == 0, (
        "`node_modules/.bin/tsx src/cli.ts` must exit 0.\n"
        f"rc={proc.returncode}\nrequests: {payload}\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr[-4000:]}"
    )
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert len(lines) == len(requests_), (
        f"Expected exactly {len(requests_)} response line(s), got {len(lines)}.\n"
        f"requests: {payload}\nstdout: {proc.stdout}\nstderr: {proc.stderr[-2000:]}"
    )
    responses = []
    for line in lines:
        try:
            responses.append(json.loads(line))
        except json.JSONDecodeError as exc:
            pytest.fail(f"Response line is not valid JSON ({exc}): {line!r}")
    for resp in responses:
        assert isinstance(resp, dict), f"Every response must be a JSON object, got {resp!r}"
    return responses


def assert_error(resp, code, op=None, email=None):
    assert resp.get("ok") is False, f"Expected a failed response, got {resp!r}"
    assert set(resp) == {"ok", "op", "email", "error"}, (
        f"A failed response must have exactly the keys ok/op/email/error, got {sorted(resp)}"
    )
    if op is not None:
        assert resp.get("op") == op, f"Expected op {op!r} echoed back, got {resp.get('op')!r}"
    if email is not None:
        assert resp.get("email") == email, (
            f"Expected email {email!r} echoed back, got {resp.get('email')!r}"
        )
    err = resp.get("error")
    assert isinstance(err, dict), f"`error` must be an object, got {err!r}"
    assert set(err) == {"code", "name", "message"}, (
        f"`error` must have exactly the keys code/name/message, got {sorted(err)}"
    )
    assert err.get("code") == code, (
        f"Expected error code {code!r}, got {err.get('code')!r} (response: {resp!r})"
    )
    assert err.get("name") == ERROR_NAMES[code], (
        f"Expected error name {ERROR_NAMES[code]!r} for code {code}, got {err.get('name')!r}"
    )
    assert isinstance(err.get("message"), str) and err["message"].strip(), (
        f"`error.message` must be a non-empty string, got {err.get('message')!r}"
    )


def assert_read(resp, email):
    assert resp.get("ok") is True, f"Expected a successful read, got {resp!r}"
    assert set(resp) == {"ok", "op", "email", "version", "preferences", "effective"}, (
        f"A read response must have exactly ok/op/email/version/preferences/effective, "
        f"got {sorted(resp)}"
    )
    assert resp["op"] == "read", f"Expected op 'read', got {resp['op']!r}"
    assert resp["email"] == email, f"Expected email {email!r}, got {resp['email']!r}"
    assert isinstance(resp["version"], int) and not isinstance(resp["version"], bool), (
        f"`version` must be an integer, got {resp['version']!r}"
    )
    return resp


def assert_patch(resp, email):
    assert resp.get("ok") is True, f"Expected a successful patch, got {resp!r}"
    assert set(resp) == {"ok", "op", "email", "version", "changed", "preferences"}, (
        f"A patch response must have exactly ok/op/email/version/changed/preferences, "
        f"got {sorted(resp)}"
    )
    assert resp["op"] == "patch", f"Expected op 'patch', got {resp['op']!r}"
    assert resp["email"] == email, f"Expected email {email!r}, got {resp['email']!r}"
    assert isinstance(resp["changed"], bool), f"`changed` must be a boolean, got {resp['changed']!r}"
    assert isinstance(resp["version"], int) and not isinstance(resp["version"], bool), (
        f"`version` must be an integer, got {resp['version']!r}"
    )
    return resp


def assert_history(resp, email):
    assert resp.get("ok") is True, f"Expected a successful history request, got {resp!r}"
    assert set(resp) == {"ok", "op", "email", "entries"}, (
        f"A history response must have exactly ok/op/email/entries, got {sorted(resp)}"
    )
    assert resp["op"] == "history", f"Expected op 'history', got {resp['op']!r}"
    assert resp["email"] == email, f"Expected email {email!r}, got {resp['email']!r}"
    entries = resp["entries"]
    assert isinstance(entries, list), f"`entries` must be an array, got {entries!r}"
    for entry in entries:
        assert isinstance(entry, dict) and set(entry) == {
            "version",
            "patch",
            "previous",
            "current",
        }, f"Each history entry must have exactly version/patch/previous/current, got {entry!r}"
    versions = [entry["version"] for entry in entries]
    assert versions == sorted(versions), f"History must be ordered by ascending version: {versions}"
    return entries


def stored_doc(email):
    rows = gel_query(
        "select PrefUser { preferences, version } filter .email = " f"'{email}'"
    )
    assert len(rows) == 1, f"Expected exactly one PrefUser for {email}, got {rows!r}"
    return as_doc(rows[0]["preferences"]), rows[0]["version"]


# ---- independent reference implementations (oracle) ----------------------
def ref_merge(target, patch):
    """RFC 7386 JSON Merge Patch."""
    if isinstance(patch, dict):
        result = dict(target) if isinstance(target, dict) else {}
        for key, value in patch.items():
            if value is None:
                result.pop(key, None)
            else:
                result[key] = ref_merge(result.get(key), value)
        return result
    return copy.deepcopy(patch)


def ref_effective(stored):
    """System defaults layered under the stored document."""

    def layer(default, value):
        if isinstance(default, dict) and isinstance(value, dict):
            out = copy.deepcopy(default)
            for key, val in value.items():
                out[key] = layer(out[key], val) if key in out else copy.deepcopy(val)
            return out
        return copy.deepcopy(value)

    return layer(SYSTEM_DEFAULTS, stored)


PROBE_SOURCE = r"""
const fs = require("fs");

function loadModule() {
  try {
    return require("./src/prefs.ts");
  } catch (err) {
    if (err && err.code === "MODULE_NOT_FOUND") {
      return require("./src/prefs");
    }
    throw err;
  }
}

const mod = loadModule();
const input = fs.readFileSync(0, "utf8");

for (const line of input.split("\n")) {
  if (!line.trim()) continue;
  const testCase = JSON.parse(line);
  let out;
  try {
    if (testCase.kind === "merge") {
      const before = JSON.stringify([testCase.target, testCase.patch]);
      const value = mod.mergePatch(testCase.target, testCase.patch);
      out = {
        ok: true,
        value: value === undefined ? "__undefined__" : value,
        mutated: JSON.stringify([testCase.target, testCase.patch]) !== before,
      };
    } else if (testCase.kind === "effective") {
      out = { ok: true, value: mod.effectivePreferences(testCase.stored) };
    } else if (testCase.kind === "exports") {
      const kinds = {};
      for (const name of testCase.names) kinds[name] = typeof mod[name];
      const errors = {};
      for (const name of testCase.errorNames) {
        try {
          const instance = new mod[name]("boom");
          errors[name] = {
            code: instance.code,
            ctor: instance.constructor.name,
            isBase: instance instanceof mod.PreferenceError,
            isError: instance instanceof Error,
          };
        } catch (err) {
          errors[name] = { failed: String((err && err.message) || err) };
        }
      }
      out = { ok: true, value: { kinds: kinds, errors: errors, defaults: mod.SYSTEM_DEFAULTS } };
    } else {
      out = { ok: false, error: "unknown probe case" };
    }
  } catch (err) {
    out = { ok: false, error: String((err && err.message) || err) };
  }
  process.stdout.write(JSON.stringify(out) + "\n");
}
"""


@pytest.fixture(scope="session")
def probe():
    """Load src/prefs.ts in-process and evaluate its pure exported functions."""
    with open(PROBE_PATH, "w") as handle:
        handle.write(PROBE_SOURCE)

    def run_cases(cases):
        payload = "".join(json.dumps(case) + "\n" for case in cases)
        proc = _run(
            [os.path.join("node_modules", ".bin", "tsx"), ".harbor_probe.ts"],
            cwd=PROJECT_DIR,
            timeout=240,
            stdin_data=payload,
        )
        assert proc.returncode == 0, (
            "Loading src/prefs.ts and calling its exported pure functions failed.\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr[-4000:]}"
        )
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        assert len(lines) == len(cases), (
            f"Expected {len(cases)} probe result(s), got {len(lines)}: {proc.stdout!r}"
        )
        return [json.loads(line) for line in lines]

    try:
        yield run_cases
    finally:
        if os.path.exists(PROBE_PATH):
            os.remove(PROBE_PATH)


def parse_ts(text):
    assert isinstance(text, str) and text, f"Expected a datetime string, got {text!r}"
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


# --------------------------------------------------------------------------
# 1. project state
# --------------------------------------------------------------------------
def test_project_layout_and_typecheck(server):
    for rel in ["src/prefs.ts", "src/cli.ts"]:
        path = os.path.join(PROJECT_DIR, rel)
        assert os.path.isfile(path), f"Required file {path} is missing."

    with open(os.path.join(PROJECT_DIR, "package.json")) as handle:
        pkg = json.load(handle)
    assert "type" not in pkg, (
        "package.json must stay CommonJS: the `type` field must not be added."
    )
    assert pkg.get("scripts", {}).get("typecheck") == "tsc --noEmit", (
        "The `typecheck` script must still be `tsc --noEmit`, got "
        f"{pkg.get('scripts', {}).get('typecheck')!r}"
    )

    with open(os.path.join(PROJECT_DIR, "tsconfig.json")) as handle:
        tsconfig = json.load(handle)
    assert tsconfig.get("compilerOptions", {}).get("strict") is True, (
        "tsconfig.json must keep `strict: true`."
    )

    proc = _run(["npm", "run", "--silent", "typecheck"], cwd=PROJECT_DIR, timeout=300)
    assert proc.returncode == 0, (
        "`npm run typecheck` must exit 0.\n"
        f"stdout: {proc.stdout[-4000:]}\nstderr: {proc.stderr[-4000:]}"
    )


def test_migrations_created_and_applied(server):
    files = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql")))
    assert len(files) >= 2, (
        "At least two migration files are expected in "
        f"{MIGRATIONS_DIR} (the baked one plus yours), found {files}."
    )
    rows = gel_query("select count(schema::Migration)")
    assert rows and rows[0] == len(files), (
        f"The branch has {rows} applied migrations but {len(files)} migration files exist "
        f"({[os.path.basename(f) for f in files]}); every file must be applied."
    )
    proc = _run(
        ["gel", "migration", "status", f"--schema-dir={SCHEMA_DIR}"],
        cwd=PROJECT_DIR,
        timeout=180,
    )
    assert proc.returncode == 0, (
        "`gel migration status` must succeed (schema, migration files and database in "
        f"sync).\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


# --------------------------------------------------------------------------
# 2. schema
# --------------------------------------------------------------------------
def test_prefuser_schema_shape(server):
    rows = gel_query(
        "select schema::ObjectType { name, ptrs := .pointers.name } "
        "filter .name = 'default::PrefUser'"
    )
    assert len(rows) == 1, f"default::PrefUser must exist, introspection returned {rows!r}"
    names = set(rows[0]["ptrs"])
    for expected in ["email", "preferences", "version", "updated_at", "history"]:
        assert expected in names, (
            f"default::PrefUser must expose `{expected}`; it has {sorted(names)}."
        )

    constraints = gel_query(
        "select (select schema::Property filter .source.name = 'default::PrefUser' "
        "and .name = 'email').constraints.name"
    )
    assert "std::exclusive" in constraints, (
        f"PrefUser.email must keep its exclusive constraint, found {constraints!r}"
    )

    rewrites = gel_query(
        "select count((select schema::Rewrite filter .subject.name = 'version' "
        "and .subject.source.name = 'default::PrefUser'))"
    )
    assert rewrites and rewrites[0] >= 1, (
        "PrefUser.version must be maintained by the database itself; no schema rewrite "
        f"was found for it (count={rewrites!r})."
    )


def test_prefchange_schema_shape(server):
    rows = gel_query(
        "select schema::ObjectType { "
        "name, pointers: { name, [is schema::Link].target: { name } } "
        "} filter .name = 'default::PrefChange'"
    )
    assert len(rows) == 1, f"default::PrefChange must exist, introspection returned {rows!r}"
    pointers = {ptr["name"]: ptr for ptr in rows[0]["pointers"]}
    for expected in ["version", "patch", "previous", "current", "applied_at", "user"]:
        assert expected in pointers, (
            f"default::PrefChange must expose `{expected}`; it has {sorted(pointers)}."
        )
    user_target = pointers["user"].get("target")
    assert isinstance(user_target, dict) and user_target.get("name") == "default::PrefUser", (
        "PrefChange.user must be a link whose target is default::PrefUser, got "
        f"{user_target!r}"
    )

    constraints = gel_query(
        "select schema::ObjectType { cons := (select .constraints { name, subjectexpr }) } "
        "filter .name = 'default::PrefChange'"
    )
    assert constraints, "Could not introspect the constraints of default::PrefChange."
    exclusives = [
        con
        for con in constraints[0]["cons"]
        if con.get("name") == "std::exclusive" and con.get("subjectexpr")
    ]
    assert exclusives, (
        "default::PrefChange must declare an object-level exclusive constraint, found "
        f"{constraints[0]['cons']!r}"
    )
    matching = [
        con
        for con in exclusives
        if "user" in con["subjectexpr"] and "version" in con["subjectexpr"]
    ]
    assert matching, (
        "The exclusive constraint on default::PrefChange must cover the (user, version) "
        f"pair, found {[con['subjectexpr'] for con in exclusives]!r}"
    )


def test_prefchange_version_is_unique_per_user(server):
    insert_query = (
        "insert PrefChange { "
        "user := (select PrefUser filter .email = 'barbara@example.com'), "
        "version := 9999, patch := to_json('{}'), previous := to_json('{}'), "
        "current := to_json('{}') }"
    )
    try:
        first = _run(["gel", "query", "-F", "json-lines", insert_query], timeout=180)
        assert first.returncode == 0, (
            "Inserting a PrefChange with only user/version/patch/previous/current must "
            f"work.\nstdout: {first.stdout}\nstderr: {first.stderr}"
        )
        second = _run(["gel", "query", "-F", "json-lines", insert_query], timeout=180)
        assert second.returncode != 0, (
            "Recording the same (user, version) pair twice must be rejected by the "
            f"exclusive constraint, but the second insert succeeded: {second.stdout}"
        )
        combined = (second.stdout + second.stderr).lower()
        assert "violat" in combined or "exclusive" in combined, (
            "The second insert failed, but not with a constraint violation: "
            f"{second.stdout} {second.stderr}"
        )
    finally:
        cleanup = _run(
            ["gel", "query", "-F", "json-lines", "delete PrefChange filter .version = 9999"],
            timeout=180,
        )
        assert cleanup.returncode == 0, (
            f"Failed to clean up the probe PrefChange rows: {cleanup.stderr}"
        )


def test_seeded_data_survived_the_migration(server):
    counted = gel_query("select count(PrefUser)")
    assert counted and counted[0] == 9, (
        f"All nine seeded PrefUser objects must still exist, got {counted!r}"
    )
    for email in ["dennis@example.com", "katherine@example.com"]:
        doc, version = stored_doc(email)
        assert same(doc, SEEDED[email]), (
            f"The seeded preferences of {email} must be untouched: expected "
            f"{SEEDED[email]!r}, found {doc!r}"
        )
        assert version == 0, f"{email} was never patched, so version must be 0, got {version}"


# --------------------------------------------------------------------------
# 3. pure exported functions
# --------------------------------------------------------------------------
MERGE_CASES = [
    ({"a": {"b": 1, "c": 2}}, {"a": {"c": 3}}, {"a": {"b": 1, "c": 3}}),
    ({"a": {"b": 1, "c": 2}}, {"a": {"b": None}}, {"a": {"c": 2}}),
    ({"a": {"b": 1}}, {"a": {"z": None}}, {"a": {"b": 1}}),
    ({"list": [1, 2, 3], "k": "v"}, {"list": [9]}, {"list": [9], "k": "v"}),
    ({"list": [1, 2]}, {"list": {"0": 5}}, {"list": {"0": 5}}),
    ({"a": 1}, {"a": {"deep": {"deeper": True}}}, {"a": {"deep": {"deeper": True}}}),
    (
        {"a": {"b": {"c": {"d": 1, "e": 2}}}},
        {"a": {"b": {"c": {"e": None, "f": 3}}}},
        {"a": {"b": {"c": {"d": 1, "f": 3}}}},
    ),
    ({}, {"ui": {}}, {"ui": {}}),
    ({"ui": {"theme": "dark"}}, {"ui": {}}, {"ui": {"theme": "dark"}}),
    ({"a": {"b": 1}}, {"a": None}, {}),
    ({"n": 1}, {}, {"n": 1}),
]


def test_pure_merge_patch_matrix(probe):
    cases = [
        {"kind": "merge", "target": target, "patch": patch}
        for target, patch, _ in MERGE_CASES
    ]
    results = probe(cases)
    for (target, patch, expected), result in zip(MERGE_CASES, results):
        assert result.get("ok") is True, (
            f"mergePatch({target!r}, {patch!r}) raised: {result.get('error')!r}"
        )
        assert same(result["value"], expected), (
            f"mergePatch({target!r}, {patch!r}) returned {result['value']!r}, "
            f"expected {expected!r}"
        )


def test_pure_merge_patch_does_not_mutate_arguments(probe):
    cases = [
        {"kind": "merge", "target": target, "patch": patch}
        for target, patch, _ in MERGE_CASES
    ]
    results = probe(cases)
    for (target, patch, _expected), result in zip(MERGE_CASES, results):
        assert result.get("mutated") is False, (
            f"mergePatch must be pure, but it mutated its arguments for target={target!r} "
            f"patch={patch!r}"
        )


EFFECTIVE_CASES = [
    {},
    {"ui": {"theme": "dark", "sidebar": {"width": 320}}, "editor": {"tab_size": 2}},
    {"editor": {"rulers": [100]}},
    {"labs": {"beta": True}},
]


def test_pure_effective_preferences(probe):
    results = probe([{"kind": "effective", "stored": doc} for doc in EFFECTIVE_CASES])
    for stored, result in zip(EFFECTIVE_CASES, results):
        assert result.get("ok") is True, (
            f"effectivePreferences({stored!r}) raised: {result.get('error')!r}"
        )
        expected = ref_effective(stored)
        assert same(result["value"], expected), (
            f"effectivePreferences({stored!r}) returned {result['value']!r}, "
            f"expected {expected!r}"
        )

    layered = results[1]["value"]
    assert layered["ui"]["density"] == "comfortable", "Defaults must survive layering."
    assert layered["ui"]["sidebar"]["visible"] is True, "Nested defaults must be merged."
    assert layered["ui"]["sidebar"]["width"] == 320, "Stored nested values must win."
    assert same(layered["ui"]["pinned"], []), "Default arrays must survive layering."
    assert layered["editor"]["tab_size"] == 2, "Stored scalars must win."
    assert same(layered["editor"]["rulers"], [80, 120]), "Default arrays must be kept."
    assert same(results[2]["value"]["editor"]["rulers"], [100]), (
        "Arrays must be replaced wholesale, never merged with the default."
    )
    assert same(results[3]["value"]["labs"], {"beta": True}), (
        "Namespaces that exist only in the stored document must be preserved."
    )


def test_module_exports_and_error_taxonomy(probe):
    names = [
        "SYSTEM_DEFAULTS",
        "mergePatch",
        "effectivePreferences",
        "applyPatch",
        "readPreferences",
        "readHistory",
        "PreferenceError",
        "MalformedPatchError",
        "UnknownUserError",
        "UnknownNamespaceError",
        "TypeMismatchError",
        "StaleVersionError",
    ]
    error_names = [
        "MalformedPatchError",
        "UnknownUserError",
        "UnknownNamespaceError",
        "TypeMismatchError",
        "StaleVersionError",
    ]
    result = probe([{"kind": "exports", "names": names, "errorNames": error_names}])[0]
    assert result.get("ok") is True, f"Inspecting the module exports failed: {result!r}"
    value = result["value"]

    for func in [
        "mergePatch",
        "effectivePreferences",
        "applyPatch",
        "readPreferences",
        "readHistory",
    ]:
        assert value["kinds"].get(func) == "function", (
            f"src/prefs.ts must export `{func}` as a function, got "
            f"{value['kinds'].get(func)!r}"
        )
    for cls in ["PreferenceError"] + error_names:
        assert value["kinds"].get(cls) == "function", (
            f"src/prefs.ts must export the class `{cls}`, got {value['kinds'].get(cls)!r}"
        )
    assert same(value["defaults"], SYSTEM_DEFAULTS), (
        f"SYSTEM_DEFAULTS must equal the specified defaults document, got {value['defaults']!r}"
    )

    code_by_name = {name: code for code, name in ERROR_NAMES.items()}
    for cls in error_names:
        info = value["errors"].get(cls)
        assert isinstance(info, dict) and "failed" not in info, (
            f"`new {cls}(\"boom\")` must work, got {info!r}"
        )
        assert info.get("isError") is True, f"{cls} must extend Error."
        assert info.get("isBase") is True, f"{cls} must extend PreferenceError."
        assert info.get("ctor") == cls, (
            f"The class name of {cls} instances must be {cls!r}, got {info.get('ctor')!r}"
        )
        assert info.get("code") == code_by_name[cls], (
            f"{cls} instances must report code {code_by_name[cls]!r}, got {info.get('code')!r}"
        )


# --------------------------------------------------------------------------
# 4. reads
# --------------------------------------------------------------------------
def test_cli_read_and_effective_layering(server):
    responses = cli(
        [
            {"op": "read", "email": "grace@example.com"},
            {"op": "read", "email": "hedy@example.com"},
        ]
    )
    grace = assert_read(responses[0], "grace@example.com")
    assert grace["version"] == 0, f"grace must still be at version 0, got {grace['version']}"
    assert same(grace["preferences"], {}), (
        f"grace's stored document must be empty, got {grace['preferences']!r}"
    )
    assert same(grace["effective"], SYSTEM_DEFAULTS), (
        "With an empty stored document the effective document must equal the system "
        f"defaults, got {grace['effective']!r}"
    )

    hedy = assert_read(responses[1], "hedy@example.com")
    effective = hedy["effective"]
    assert effective["ui"]["sidebar"]["visible"] is False, (
        f"Stored nested value must win, got {effective['ui']['sidebar']!r}"
    )
    assert effective["ui"]["sidebar"]["width"] == 280, (
        f"Default nested value must be layered in, got {effective['ui']['sidebar']!r}"
    )
    assert effective["ui"]["theme"] == "light", f"Unexpected theme: {effective['ui']!r}"
    assert effective["notifications"]["batch_size"] == 10, (
        f"Stored batch_size must win, got {effective['notifications']!r}"
    )
    assert effective["notifications"]["email"]["digest"] == "daily", (
        f"Default digest must be layered in, got {effective['notifications']['email']!r}"
    )
    assert same(effective, ref_effective(SEEDED["hedy@example.com"])), (
        f"Unexpected effective document for hedy: {effective!r}"
    )


# --------------------------------------------------------------------------
# 5. patch semantics through the CLI
# --------------------------------------------------------------------------
ADA_PATCHES = [
    {"ui": {"sidebar": {"visible": False}}},
    {"ui": {"theme": None}, "editor": {"soft_wrap": False}},
    {"ui": {"pinned": ["a", "b"]}},
    {"ui": {"pinned": ["c"]}},
]


def test_cli_patch_matrix_for_ada(server):
    email = "ada@example.com"
    requests_ = [
        {"op": "patch", "email": email, "patch": patch} for patch in ADA_PATCHES
    ]
    requests_.append({"op": "read", "email": email})
    responses = cli(requests_)

    expected_doc = copy.deepcopy(SEEDED[email])
    for index, patch in enumerate(ADA_PATCHES):
        expected_doc = ref_merge(expected_doc, patch)
        resp = assert_patch(responses[index], email)
        assert resp["changed"] is True, f"Patch {patch!r} must change the document: {resp!r}"
        assert resp["version"] == index + 1, (
            f"After patch #{index + 1} the version must be {index + 1}, got {resp['version']}"
        )
        assert same(resp["preferences"], expected_doc), (
            f"After patch {patch!r} the stored document must be {expected_doc!r}, got "
            f"{resp['preferences']!r}"
        )

    assert "theme" not in expected_doc["ui"], "sanity: the reference must have deleted ui.theme"
    assert same(expected_doc["ui"]["pinned"], ["c"]), "sanity: arrays are replaced wholesale"

    final = assert_read(responses[-1], email)
    assert final["version"] == 4, f"ada must end at version 4, got {final['version']}"
    assert same(final["preferences"], expected_doc), (
        f"Read-back document mismatch: {final['preferences']!r} != {expected_doc!r}"
    )
    assert final["effective"]["ui"]["density"] == "comfortable", (
        f"Defaults must still be layered under ada's values: {final['effective']['ui']!r}"
    )
    assert same(final["effective"], ref_effective(expected_doc)), (
        f"Unexpected effective document for ada: {final['effective']!r}"
    )

    db_doc, db_version = stored_doc(email)
    assert same(db_doc, expected_doc), (
        f"The document persisted in Gel is {db_doc!r}, expected {expected_doc!r}"
    )
    assert db_version == 4, f"The persisted version must be 4, got {db_version}"


def test_cli_history_chain_for_ada(server):
    email = "ada@example.com"
    entries = assert_history(cli([{"op": "history", "email": email}])[0], email)
    assert [entry["version"] for entry in entries] == [1, 2, 3, 4], (
        f"ada must have exactly the history versions 1..4, got "
        f"{[entry['version'] for entry in entries]!r}"
    )
    assert same(entries[0]["previous"], SEEDED[email]), (
        f"The first history entry must record the seeded document as `previous`, got "
        f"{entries[0]['previous']!r}"
    )
    expected_doc = copy.deepcopy(SEEDED[email])
    for entry, patch in zip(entries, ADA_PATCHES):
        assert same(entry["patch"], patch), (
            f"History entry {entry['version']} must record the patch {patch!r}, got "
            f"{entry['patch']!r}"
        )
        assert same(entry["previous"], expected_doc), (
            f"History entry {entry['version']} has previous {entry['previous']!r}, expected "
            f"{expected_doc!r}"
        )
        expected_doc = ref_merge(expected_doc, patch)
        assert same(entry["current"], expected_doc), (
            f"History entry {entry['version']} has current {entry['current']!r}, expected "
            f"{expected_doc!r}"
        )
    db_doc, _ = stored_doc(email)
    assert same(entries[-1]["current"], db_doc), (
        "The last history entry must match the stored document, "
        f"{entries[-1]['current']!r} != {db_doc!r}"
    )
    counted = gel_query(
        "select PrefUser { c := count(.history) } filter .email = 'ada@example.com'"
    )
    assert counted and counted[0]["c"] == 4, (
        f"PrefUser.history must expose the four change records, got {counted!r}"
    )


def test_cli_noop_patch_is_idempotent_for_grace(server):
    email = "grace@example.com"
    patch = {"ui": {"theme": "dark"}}
    responses = cli(
        [
            {"op": "patch", "email": email, "patch": {}},
            {"op": "patch", "email": email, "patch": patch},
            {"op": "patch", "email": email, "patch": patch},
            {"op": "history", "email": email},
        ]
    )
    empty = assert_patch(responses[0], email)
    assert empty["changed"] is False, f"An empty patch must be a no-op, got {empty!r}"
    assert empty["version"] == 0, f"A no-op must not move the version, got {empty['version']}"
    assert same(empty["preferences"], {}), f"Unexpected document: {empty['preferences']!r}"

    first = assert_patch(responses[1], email)
    assert first["changed"] is True, f"The first real patch must change the document: {first!r}"
    assert first["version"] == 1, f"Expected version 1, got {first['version']}"
    assert same(first["preferences"], patch), f"Unexpected document: {first['preferences']!r}"

    repeat = assert_patch(responses[2], email)
    assert repeat["changed"] is False, (
        f"Re-applying an identical patch must be a no-op, got {repeat!r}"
    )
    assert repeat["version"] == 1, (
        f"Re-applying an identical patch must not move the version, got {repeat['version']}"
    )
    assert same(repeat["preferences"], patch), f"Unexpected document: {repeat['preferences']!r}"

    entries = assert_history(responses[3], email)
    assert len(entries) == 1, f"Only the one real change may be recorded, got {entries!r}"
    assert entries[0]["version"] == 1, f"Unexpected history version: {entries[0]!r}"
    assert same(entries[0]["previous"], {}), f"Unexpected previous: {entries[0]['previous']!r}"
    assert same(entries[0]["current"], patch), f"Unexpected current: {entries[0]['current']!r}"

    db_doc, db_version = stored_doc(email)
    assert same(db_doc, patch) and db_version == 1, (
        f"Persisted state for grace is {db_doc!r} at version {db_version}"
    )


def test_cli_empty_namespace_object_is_created_for_katherine(server):
    email = "katherine@example.com"
    responses = cli(
        [
            {"op": "patch", "email": email, "patch": {"ui": {}}},
            {"op": "patch", "email": email, "patch": {"ui": {}}},
        ]
    )
    first = assert_patch(responses[0], email)
    assert first["changed"] is True, (
        f"Merging an empty object into a missing key adds that key: {first!r}"
    )
    assert first["version"] == 1, f"Expected version 1, got {first['version']}"
    assert same(first["preferences"], {"ui": {}}), f"Unexpected document: {first['preferences']!r}"

    second = assert_patch(responses[1], email)
    assert second["changed"] is False, f"The repeat must be a no-op, got {second!r}"
    assert second["version"] == 1, f"The version must stay 1, got {second['version']}"
    assert same(second["preferences"], {"ui": {}}), (
        f"Unexpected document: {second['preferences']!r}"
    )

    db_doc, db_version = stored_doc(email)
    assert same(db_doc, {"ui": {}}) and db_version == 1, (
        f"Persisted state for katherine is {db_doc!r} at version {db_version}"
    )


# --------------------------------------------------------------------------
# 6. rejections
# --------------------------------------------------------------------------
REJECTIONS = [
    ({"experiments": {"beta": True}}, "UNKNOWN_NAMESPACE"),
    ({"experiments": None}, "UNKNOWN_NAMESPACE"),
    ({"editor": {"tab_size": "4"}}, "TYPE_MISMATCH"),
    ({"ui": {"sidebar": {"visible": "yes"}}}, "TYPE_MISMATCH"),
    ({"ui": {"pinned": {"first": 1}}}, "TYPE_MISMATCH"),
    ({"ui": {"sidebar": [1, 2]}}, "TYPE_MISMATCH"),
    ({"notifications": {"push": {"enabled": 1}}}, "TYPE_MISMATCH"),
]


def test_cli_rejects_invalid_patches_without_side_effects(server):
    email = "alan@example.com"
    accepted = {"editor": {"nickname": "vimmy", "tab_size": 8}}
    requests_ = [{"op": "patch", "email": email, "patch": patch} for patch, _ in REJECTIONS]
    requests_.append({"op": "patch", "email": "nobody@example.com", "patch": {"ui": {}}})
    requests_.append({"op": "read", "email": email})
    requests_.append({"op": "history", "email": email})
    requests_.append({"op": "patch", "email": email, "patch": accepted})
    responses = cli(requests_)

    for index, (patch, code) in enumerate(REJECTIONS):
        assert_error(responses[index], code, op="patch", email=email)
        detail = responses[index]["error"]
        assert detail["code"] == code, f"Patch {patch!r} must be rejected with {code}: {detail!r}"

    assert_error(
        responses[len(REJECTIONS)], "UNKNOWN_USER", op="patch", email="nobody@example.com"
    )

    read = assert_read(responses[len(REJECTIONS) + 1], email)
    assert read["version"] == 0, (
        f"Rejected patches must not move the version, got {read['version']}"
    )
    assert same(read["preferences"], SEEDED[email]), (
        f"Rejected patches must not touch the document, got {read['preferences']!r}"
    )

    entries = assert_history(responses[len(REJECTIONS) + 2], email)
    assert entries == [], f"Rejected patches must not create history entries, got {entries!r}"

    final = assert_patch(responses[-1], email)
    assert final["changed"] is True, f"The free-form deep key must be accepted: {final!r}"
    assert final["version"] == 1, f"Expected version 1 after the accepted patch, got {final!r}"
    expected = ref_merge(SEEDED[email], accepted)
    assert same(final["preferences"], expected), (
        f"Unexpected document {final['preferences']!r}, expected {expected!r}"
    )

    db_doc, db_version = stored_doc(email)
    assert same(db_doc, expected), f"Persisted document mismatch: {db_doc!r} != {expected!r}"
    assert db_version == 1, f"Persisted version must be 1, got {db_version}"
    assert db_doc["editor"]["nickname"] == "vimmy", (
        f"Unknown deep keys must be stored verbatim, got {db_doc['editor']!r}"
    )
    counted = gel_query(
        "select count((select PrefChange filter .user.email = 'alan@example.com'))"
    )
    assert counted and counted[0] == 1, (
        f"Exactly one change must be recorded for alan, got {counted!r}"
    )


def test_cli_rejects_malformed_requests(server):
    email = "alan@example.com"
    malformed = [
        {"op": "patch", "email": email},
        {"op": "patch", "email": email, "patch_text": "{oops"},
        {"op": "patch", "email": email, "patch": [1, 2]},
        {"op": "patch", "email": email, "patch": {}, "patch_text": "{}"},
        {"op": "patch", "email": email, "patch": {"ui": {}}, "expected_version": "1"},
        {"op": "purge", "email": email},
    ]
    responses = cli(malformed + [{"op": "read", "email": email}])
    for request, response in zip(malformed, responses):
        assert_error(response, "MALFORMED_PATCH", email=email)
        assert response["op"] == request["op"], (
            f"The received op must be echoed back for {request!r}, got {response!r}"
        )

    read = assert_read(responses[-1], email)
    assert read["version"] == 1, (
        f"Malformed requests must not change anything; version must still be 1, got "
        f"{read['version']}"
    )
    expected = ref_merge(SEEDED[email], {"editor": {"nickname": "vimmy", "tab_size": 8}})
    assert same(read["preferences"], expected), (
        f"Malformed requests must not touch the document, got {read['preferences']!r}"
    )


def test_cli_rejects_stale_expected_version(server):
    email = "linus@example.com"
    patch = {"notifications": {"batch_size": 50}}
    responses = cli(
        [
            {"op": "patch", "email": email, "patch": patch, "expected_version": 5},
            {"op": "patch", "email": email, "patch": patch, "expected_version": 0},
            {
                "op": "patch",
                "email": email,
                "patch": {"notifications": {"batch_size": 60}},
                "expected_version": 0,
            },
            {
                "op": "patch",
                "email": email,
                "patch": {"nope": {"x": 1}},
                "expected_version": 7,
            },
            {"op": "history", "email": email},
            {"op": "read", "email": email},
        ]
    )
    assert_error(responses[0], "STALE_VERSION", op="patch", email=email)

    applied = assert_patch(responses[1], email)
    assert applied["changed"] is True, f"The matching expected_version must apply: {applied!r}"
    assert applied["version"] == 1, f"Expected version 1, got {applied['version']}"

    assert_error(responses[2], "STALE_VERSION", op="patch", email=email)
    assert_error(responses[3], "UNKNOWN_NAMESPACE", op="patch", email=email)

    entries = assert_history(responses[4], email)
    assert len(entries) == 1 and entries[0]["version"] == 1, (
        f"Only the single applied patch may be recorded, got {entries!r}"
    )

    expected = ref_merge(SEEDED[email], patch)
    read = assert_read(responses[5], email)
    assert read["version"] == 1, f"Expected version 1, got {read['version']}"
    assert same(read["preferences"], expected), (
        f"Unexpected document {read['preferences']!r}, expected {expected!r}"
    )
    assert read["preferences"]["notifications"]["batch_size"] == 50, (
        f"batch_size must be 50, got {read['preferences']['notifications']!r}"
    )
    assert read["preferences"]["notifications"]["email"]["digest"] == "weekly", (
        "The seeded nested values must survive the merge, got "
        f"{read['preferences']['notifications']!r}"
    )

    db_doc, db_version = stored_doc(email)
    assert same(db_doc, expected) and db_version == 1, (
        f"Persisted state for linus is {db_doc!r} at version {db_version}"
    )


def test_cli_dry_run_does_not_persist(server):
    email = "hedy@example.com"
    patch = {"ui": {"theme": "dark", "sidebar": {"width": 400}}}
    responses = cli(
        [
            {"op": "patch", "email": email, "patch": patch, "dry_run": True},
            {"op": "read", "email": email},
            {"op": "history", "email": email},
            {
                "op": "patch",
                "email": email,
                "patch": {"editor": {"tab_size": True}},
                "dry_run": True,
            },
        ]
    )
    dry = assert_patch(responses[0], email)
    assert dry["changed"] is True, f"The dry run must report the pending change: {dry!r}"
    assert dry["version"] == 0, (
        f"A dry run must report the unchanged current version, got {dry['version']}"
    )
    expected = ref_merge(SEEDED[email], patch)
    assert same(dry["preferences"], expected), (
        f"The dry run must report {expected!r}, got {dry['preferences']!r}"
    )
    assert dry["preferences"]["ui"]["theme"] == "dark", f"Unexpected: {dry['preferences']!r}"
    assert same(dry["preferences"]["ui"]["sidebar"], {"visible": False, "width": 400}), (
        f"Unexpected nested merge result: {dry['preferences']['ui']!r}"
    )

    read = assert_read(responses[1], email)
    assert read["version"] == 0, f"A dry run must not move the version, got {read['version']}"
    assert same(read["preferences"], SEEDED[email]), (
        f"A dry run must not persist anything, got {read['preferences']!r}"
    )
    assert assert_history(responses[2], email) == [], (
        "A dry run must not create history entries."
    )
    assert_error(responses[3], "TYPE_MISMATCH", op="patch", email=email)

    db_doc, db_version = stored_doc(email)
    assert same(db_doc, SEEDED[email]) and db_version == 0, (
        f"Persisted state for hedy changed during the dry run: {db_doc!r} v{db_version}"
    )


# --------------------------------------------------------------------------
# 7. concurrency
# --------------------------------------------------------------------------
CONCURRENT_PATCHES = [
    {"ui": {"theme": "dark"}},
    {"ui": {"density": "compact"}},
    {"notifications": {"batch_size": 50}},
    {"editor": {"tab_size": 8}},
    {"privacy": {"analytics": False}},
    {"editor": {"keymap": "emacs"}},
]


def test_concurrent_patches_are_serialized(server):
    email = "edsger@example.com"

    def apply(patch):
        return cli([{"op": "patch", "email": email, "patch": patch}], timeout=300)[0]

    started = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(CONCURRENT_PATCHES)) as pool:
        results = list(pool.map(apply, CONCURRENT_PATCHES))
    elapsed = time.time() - started
    assert elapsed < 300, f"The concurrent patches took too long ({elapsed:.1f}s)."

    for patch, resp in zip(CONCURRENT_PATCHES, results):
        checked = assert_patch(resp, email)
        assert checked["changed"] is True, (
            f"Every concurrent patch changes the document; {patch!r} reported {checked!r}"
        )

    expected = copy.deepcopy(SEEDED[email])
    for patch in CONCURRENT_PATCHES:
        expected = ref_merge(expected, patch)

    db_doc, db_version = stored_doc(email)
    assert same(db_doc, expected), (
        "No concurrent update may be lost: the stored document is "
        f"{db_doc!r}, expected {expected!r}"
    )
    assert db_version == len(CONCURRENT_PATCHES), (
        f"The version must have advanced to {len(CONCURRENT_PATCHES)}, got {db_version}"
    )
    assert db_doc["editor"]["rulers"] == [100], (
        f"Untouched seeded values must survive, got {db_doc['editor']!r}"
    )
    assert db_doc["editor"]["soft_wrap"] is False, (
        f"Untouched seeded values must survive, got {db_doc['editor']!r}"
    )
    assert db_doc["editor"]["tab_size"] == 8 and db_doc["editor"]["keymap"] == "emacs", (
        f"Both concurrent editor patches must be present, got {db_doc['editor']!r}"
    )

    entries = assert_history(cli([{"op": "history", "email": email}])[0], email)
    assert [entry["version"] for entry in entries] == list(
        range(1, len(CONCURRENT_PATCHES) + 1)
    ), f"The history versions must be gap-free 1..6, got {[e['version'] for e in entries]!r}"
    assert same(entries[0]["previous"], SEEDED[email]), (
        f"The chain must start at the seeded document, got {entries[0]['previous']!r}"
    )
    for previous_entry, entry in zip(entries, entries[1:]):
        assert same(entry["previous"], previous_entry["current"]), (
            f"History entry {entry['version']} does not chain onto "
            f"{previous_entry['version']}: {entry['previous']!r} != {previous_entry['current']!r}"
        )
    assert same(entries[-1]["current"], db_doc), (
        f"The last history entry must equal the stored document: {entries[-1]['current']!r}"
    )
    applied_patches = [canon(entry["patch"]) for entry in entries]
    assert sorted(applied_patches) == sorted(canon(p) for p in CONCURRENT_PATCHES), (
        f"Each concurrent patch must be recorded exactly once, got {applied_patches!r}"
    )
    counted = gel_query(
        "select count((select PrefChange filter .user.email = 'edsger@example.com'))"
    )
    assert counted and counted[0] == len(CONCURRENT_PATCHES), (
        f"Exactly {len(CONCURRENT_PATCHES)} PrefChange rows must exist, got {counted!r}"
    )


# --------------------------------------------------------------------------
# 8. database-maintained bookkeeping
# --------------------------------------------------------------------------
def test_database_maintains_version_and_updated_at(server):
    email = "barbara@example.com"
    before = gel_query(
        f"select PrefUser {{ version, updated_at }} filter .email = '{email}'"
    )
    assert len(before) == 1, f"Expected one PrefUser for {email}, got {before!r}"
    old_version = before[0]["version"]
    old_stamp = parse_ts(before[0]["updated_at"])

    update = (
        "select (update PrefUser filter .email = " f"'{email}'"
        " set { preferences := to_json('{\"ui\": {\"theme\": \"dark\"}}'), version := 99 }"
        ") { version }"
    )
    rows = gel_query(update)
    assert rows, f"The bare EdgeQL update returned nothing: {rows!r}"

    after = gel_query(
        f"select PrefUser {{ version, updated_at, preferences }} filter .email = '{email}'"
    )
    assert len(after) == 1, f"Expected one PrefUser for {email}, got {after!r}"
    new_version = after[0]["version"]
    new_stamp = parse_ts(after[0]["updated_at"])

    assert new_version == old_version + 1, (
        "The database must increment version by exactly 1 and ignore the client-supplied "
        f"value 99: version went from {old_version} to {new_version}"
    )
    assert new_stamp > old_stamp, (
        f"updated_at must be refreshed on every update: {old_stamp} -> {new_stamp}"
    )
    assert same(as_doc(after[0]["preferences"]), {"ui": {"theme": "dark"}}), (
        f"The bare update should have replaced the document, got {after[0]['preferences']!r}"
    )


# --------------------------------------------------------------------------
# 9. randomized sequence against an independent oracle
# --------------------------------------------------------------------------
PATCH_POOL = [
    {"ui": {"theme": "dark"}},
    {"ui": {"theme": "light"}},
    {"ui": {"sidebar": {"width": 360}}},
    {"ui": {"sidebar": {"visible": True}}},
    {"ui": {"sidebar": None}},
    {"ui": {"pinned": ["inbox"]}},
    {"ui": {"pinned": ["inbox", "sent"]}},
    {"ui": {"density": None}},
    {"notifications": {"batch_size": 40}},
    {"notifications": {"email": {"digest": "hourly", "marketing": True}}},
    {"notifications": {"push": {"enabled": True, "quiet_hours": [23, 6]}}},
    {"notifications": {"push": None}},
    {"privacy": {"analytics": False, "share_profile": True}},
    {"editor": {"tab_size": 8}},
    {"editor": {"rulers": [72]}},
    {"editor": {"soft_wrap": None}},
    {"editor": {"labs": {"experimental": {"flag": True}}}},
    {},
]


def test_randomized_patch_sequence_matches_reference(server):
    email = "hedy@example.com"
    rng = random.Random(20260805)
    patches = [copy.deepcopy(rng.choice(PATCH_POOL)) for _ in range(10)]

    start = assert_read(cli([{"op": "read", "email": email}])[0], email)
    document = copy.deepcopy(start["preferences"])
    version = start["version"]

    expected_steps = []
    expected_history = []
    for patch in patches:
        merged = ref_merge(document, patch)
        changed = not same(merged, document)
        if changed:
            version += 1
            expected_history.append(
                {
                    "version": version,
                    "patch": copy.deepcopy(patch),
                    "previous": copy.deepcopy(document),
                    "current": copy.deepcopy(merged),
                }
            )
            document = merged
        expected_steps.append((version, changed, copy.deepcopy(document)))

    requests_ = [{"op": "patch", "email": email, "patch": patch} for patch in patches]
    requests_.append({"op": "read", "email": email})
    requests_.append({"op": "history", "email": email})
    responses = cli(requests_, timeout=300)

    for index, (patch, (exp_version, exp_changed, exp_doc)) in enumerate(
        zip(patches, expected_steps)
    ):
        resp = assert_patch(responses[index], email)
        assert resp["changed"] is exp_changed, (
            f"Step {index + 1} ({patch!r}): expected changed={exp_changed}, got {resp!r}"
        )
        assert resp["version"] == exp_version, (
            f"Step {index + 1} ({patch!r}): expected version {exp_version}, got "
            f"{resp['version']}"
        )
        assert same(resp["preferences"], exp_doc), (
            f"Step {index + 1} ({patch!r}): expected document {exp_doc!r}, got "
            f"{resp['preferences']!r}"
        )

    read = assert_read(responses[-2], email)
    assert read["version"] == version, (
        f"Final version must be {version}, got {read['version']}"
    )
    assert same(read["preferences"], document), (
        f"Final stored document must be {document!r}, got {read['preferences']!r}"
    )
    assert same(read["effective"], ref_effective(document)), (
        f"Final effective document must be {ref_effective(document)!r}, got "
        f"{read['effective']!r}"
    )

    entries = assert_history(responses[-1], email)
    assert len(entries) == len(expected_history), (
        f"Expected {len(expected_history)} history entries, got {len(entries)}: "
        f"{[e['version'] for e in entries]!r}"
    )
    for expected_entry, entry in zip(expected_history, entries):
        assert entry["version"] == expected_entry["version"], (
            f"History version mismatch: {entry!r} != {expected_entry!r}"
        )
        for field in ["patch", "previous", "current"]:
            assert same(entry[field], expected_entry[field]), (
                f"History entry {entry['version']} field `{field}` is {entry[field]!r}, "
                f"expected {expected_entry[field]!r}"
            )

    db_doc, db_version = stored_doc(email)
    assert same(db_doc, document) and db_version == version, (
        f"Persisted state is {db_doc!r} at version {db_version}, expected {document!r} "
        f"at version {version}"
    )
