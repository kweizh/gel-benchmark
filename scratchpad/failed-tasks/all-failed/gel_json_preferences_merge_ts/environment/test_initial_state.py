"""Initial-state verification for the gel_json_preferences_merge_ts task.

Checks the baked environment BEFORE the executor starts working:
the local Gel 6 server, the seeded ``default::PrefUser`` data, the
offline Node project at /home/user/prefsvc and the unimplemented stubs.
"""

import glob
import json
import os
import shutil
import subprocess

import pytest

PROJECT_DIR = "/home/user/prefsvc"
SCHEMA_DIR = os.path.join(PROJECT_DIR, "dbschema")
SCHEMA_FILE = os.path.join(SCHEMA_DIR, "default.gel")
MIGRATIONS_DIR = os.path.join(SCHEMA_DIR, "migrations")
START_SCRIPT = "/usr/local/bin/gel-start.sh"

SEEDED_USERS = {
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
def gel_server():
    """Start the local Gel server (idempotent) and make sure it answers."""
    assert os.path.isfile(START_SCRIPT), f"{START_SCRIPT} is missing."
    proc = _run(["bash", START_SCRIPT], timeout=300)
    assert proc.returncode == 0, (
        f"{START_SCRIPT} failed (rc={proc.returncode}).\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    probe = _run(["gel", "query", "-F", "json-lines", "select 1"], timeout=120)
    assert probe.returncode == 0, (
        "The Gel server does not answer queries after start.\n"
        f"stdout: {probe.stdout}\nstderr: {probe.stderr}"
    )
    return True


def gel_query(query, timeout=180):
    """Run an EdgeQL query and return the decoded result elements."""
    proc = _run(["gel", "query", "-F", "json-lines", query], timeout=timeout)
    assert proc.returncode == 0, (
        f"Query failed: {query}\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def as_doc(value):
    """Decode a json-typed column that may arrive inline or as a JSON string."""
    if isinstance(value, str):
        return json.loads(value)
    return value


def test_gel_cli_available():
    assert shutil.which("gel") is not None, "The `gel` CLI is not on PATH."


def test_node_and_npm_available():
    assert shutil.which("node") is not None, "`node` is not on PATH."
    assert shutil.which("npm") is not None, "`npm` is not on PATH."


def test_connection_environment_variables_present():
    assert os.environ.get("GEL_DSN"), "GEL_DSN is not set in the environment."
    assert os.environ.get(
        "GEL_CLIENT_TLS_SECURITY"
    ), "GEL_CLIENT_TLS_SECURITY is not set in the environment."


def test_project_directory_exists():
    assert os.path.isdir(PROJECT_DIR), f"{PROJECT_DIR} does not exist."


def test_project_files_exist():
    for rel in [
        "gel.toml",
        "package.json",
        "tsconfig.json",
        "dbschema/default.gel",
        "src/prefs.ts",
        "src/cli.ts",
    ]:
        path = os.path.join(PROJECT_DIR, rel)
        assert os.path.isfile(path), f"Expected file {path} is missing."


def test_package_json_is_commonjs_with_typecheck_script():
    with open(os.path.join(PROJECT_DIR, "package.json")) as fh:
        pkg = json.load(fh)
    assert "type" not in pkg, "package.json must not declare a module `type`."
    scripts = pkg.get("scripts", {})
    assert (
        scripts.get("typecheck") == "tsc --noEmit"
    ), "package.json must define the `typecheck` script as `tsc --noEmit`."
    deps = pkg.get("dependencies", {})
    dev = pkg.get("devDependencies", {})
    assert "gel" in deps, "The `gel` client must be a dependency of the project."
    for name in ["tsx", "typescript", "@types/node"]:
        assert name in dev, f"`{name}` must be a devDependency of the project."


def test_tsconfig_is_strict():
    with open(os.path.join(PROJECT_DIR, "tsconfig.json")) as fh:
        cfg = json.load(fh)
    assert (
        cfg.get("compilerOptions", {}).get("strict") is True
    ), "tsconfig.json must enable `strict`."


def test_node_modules_installed_offline():
    for rel in [
        "node_modules/gel/package.json",
        "node_modules/typescript/package.json",
        "node_modules/.bin/tsx",
        "node_modules/@types/node/package.json",
    ]:
        path = os.path.join(PROJECT_DIR, rel)
        assert os.path.exists(path), f"Expected pre-installed dependency {path} is missing."
    with open(os.path.join(PROJECT_DIR, "node_modules/gel/package.json")) as fh:
        gel_pkg = json.load(fh)
    assert gel_pkg.get("version") == "2.2.0", (
        "The installed `gel` npm client must be version 2.2.0, found "
        f"{gel_pkg.get('version')!r}."
    )


def test_tsx_runner_works():
    proc = _run([os.path.join(PROJECT_DIR, "node_modules/.bin/tsx"), "--version"],
                cwd=PROJECT_DIR, timeout=180)
    assert proc.returncode == 0, (
        f"`tsx --version` failed.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


def test_stub_typechecks():
    proc = _run(["npm", "run", "--silent", "typecheck"], cwd=PROJECT_DIR, timeout=300)
    assert proc.returncode == 0, (
        "`npm run typecheck` must already pass on the stub project.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


def test_stub_cli_is_not_implemented():
    proc = _run(
        [os.path.join(PROJECT_DIR, "node_modules/.bin/tsx"), "src/cli.ts"],
        cwd=PROJECT_DIR,
        timeout=180,
        stdin_data='{"op": "read", "email": "ada@example.com"}\n',
    )
    assert proc.returncode != 0, (
        "The stub CLI must fail before the task is solved, but it exited 0 with "
        f"stdout: {proc.stdout}"
    )


def test_schema_file_declares_minimal_prefuser():
    with open(SCHEMA_FILE) as fh:
        sdl = fh.read()
    assert "PrefUser" in sdl, "dbschema/default.gel must declare PrefUser."
    assert "PrefChange" not in sdl, (
        "dbschema/default.gel must NOT declare PrefChange in the initial state."
    )
    assert "version" not in sdl, (
        "dbschema/default.gel must NOT declare a version property in the initial state."
    )


def test_one_migration_file_present():
    files = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql")))
    assert len(files) == 1, (
        f"Expected exactly one baked migration file in {MIGRATIONS_DIR}, found {files}."
    )


def test_migration_applied(gel_server):
    rows = gel_query("select count(schema::Migration)")
    assert rows and rows[0] == 1, (
        f"Expected exactly one applied migration in the branch, got {rows}."
    )


def test_migration_status_is_in_sync(gel_server):
    proc = _run(
        ["gel", "migration", "status", f"--schema-dir={SCHEMA_DIR}"],
        cwd=PROJECT_DIR,
        timeout=180,
    )
    assert proc.returncode == 0, (
        "`gel migration status` must succeed in the initial state.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


def test_prefuser_type_exists_without_version(gel_server):
    rows = gel_query(
        "select schema::ObjectType { name, ptrs := .pointers.name } "
        "filter .name = 'default::PrefUser'"
    )
    assert len(rows) == 1, f"default::PrefUser must exist, introspection returned {rows}."
    names = set(rows[0]["ptrs"])
    assert "email" in names, f"PrefUser must have an `email` property, has {sorted(names)}."
    assert "preferences" in names, (
        f"PrefUser must have a `preferences` property, has {sorted(names)}."
    )
    for absent in ["version", "updated_at", "history"]:
        assert absent not in names, (
            f"PrefUser must NOT have `{absent}` in the initial state; the executor adds it."
        )


def test_prefchange_type_does_not_exist_yet(gel_server):
    rows = gel_query(
        "select count((select schema::ObjectType filter .name = 'default::PrefChange'))"
    )
    assert rows and rows[0] == 0, (
        "default::PrefChange must NOT exist in the initial state; the executor creates it."
    )


def test_nine_users_are_seeded(gel_server):
    rows = gel_query("select count(PrefUser)")
    assert rows and rows[0] == 9, f"Expected 9 seeded PrefUser objects, got {rows}."


def test_seeded_preferences_match(gel_server):
    rows = gel_query("select PrefUser { email, preferences } order by .email")
    found = {row["email"]: as_doc(row["preferences"]) for row in rows}
    assert set(found) == set(SEEDED_USERS), (
        f"Seeded emails mismatch. Expected {sorted(SEEDED_USERS)}, got {sorted(found)}."
    )
    for email, expected in SEEDED_USERS.items():
        assert found[email] == expected, (
            f"Seeded preferences for {email} are {found[email]!r}, expected {expected!r}."
        )
