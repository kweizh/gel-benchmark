import glob
import json
import os
import shutil
import subprocess

import pytest

PROJECT_DIR = "/home/user/vault"
SCHEMA_FILE = os.path.join(PROJECT_DIR, "dbschema", "default.gel")
MIGRATIONS_DIR = os.path.join(PROJECT_DIR, "dbschema", "migrations")
START_SCRIPT = "/usr/local/bin/start-gel.sh"


def _gel_query(*queries):
    """Run EdgeQL statements in one CLI session and return the rows of the last one.

    Statements are executed in order inside a single session, so session-scoped
    statements (``configure session ...``, ``set global ...``) can be passed first.
    """
    cmd = ["gel", "query", "--output-format=json-lines"]
    cmd.extend(queries)
    proc = subprocess.run(
        cmd, cwd=PROJECT_DIR, capture_output=True, text=True, timeout=120
    )
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
    return rows


@pytest.fixture(scope="session")
def server():
    """Ensure the local Gel server is running before any database-dependent check."""
    proc = subprocess.run(
        [START_SCRIPT], capture_output=True, text=True, timeout=300
    )
    assert proc.returncode == 0, (
        f"{START_SCRIPT} failed (exit {proc.returncode}):\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    return True


def test_gel_cli_available():
    assert shutil.which("gel") is not None, "gel CLI binary not found in PATH."


def test_node_and_npm_available():
    assert shutil.which("node") is not None, "node binary not found in PATH."
    assert shutil.which("npm") is not None, "npm binary not found in PATH."


def test_start_script_is_executable():
    assert os.path.isfile(START_SCRIPT), f"{START_SCRIPT} does not exist."
    assert os.access(START_SCRIPT, os.X_OK), f"{START_SCRIPT} is not executable."


def test_project_directory_exists():
    assert os.path.isdir(PROJECT_DIR), f"Project directory {PROJECT_DIR} does not exist."
    assert os.path.isfile(
        os.path.join(PROJECT_DIR, "gel.toml")
    ), f"{PROJECT_DIR}/gel.toml does not exist."


def test_baseline_schema_file_exists_with_expected_types():
    assert os.path.isfile(SCHEMA_FILE), f"{SCHEMA_FILE} does not exist."
    with open(SCHEMA_FILE) as handle:
        content = handle.read()
    for type_name in ("Actor", "Workspace", "Membership", "Document", "ActivityLog"):
        assert f"type {type_name}" in content, (
            f"Baseline schema {SCHEMA_FILE} is missing object type {type_name}."
        )


def test_baseline_schema_has_no_policies_or_globals():
    content = open(SCHEMA_FILE).read()
    assert "access policy" not in content, (
        f"{SCHEMA_FILE} already declares an access policy; the task must start unsolved."
    )
    assert "current_actor_id" not in content, (
        f"{SCHEMA_FILE} already declares the current_actor_id global; "
        "the task must start unsolved."
    )


def test_baseline_migration_present():
    migrations = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql")))
    assert len(migrations) == 1, (
        f"Expected exactly one baseline migration in {MIGRATIONS_DIR}, found {migrations}."
    )
    assert os.path.basename(migrations[0]).startswith("00001-"), (
        f"Baseline migration should be the 00001-* file, found {migrations[0]}."
    )


def test_npm_dependencies_are_preinstalled():
    gel_pkg = os.path.join(PROJECT_DIR, "node_modules", "gel", "package.json")
    assert os.path.isfile(gel_pkg), (
        "The gel npm client is not pre-installed in /home/user/vault/node_modules."
    )
    with open(gel_pkg) as handle:
        assert json.load(handle)["version"] == "2.2.0", (
            "Expected the pre-installed gel npm client to be version 2.2.0."
        )
    tsc = os.path.join(PROJECT_DIR, "node_modules", ".bin", "tsc")
    assert os.path.exists(tsc), (
        "The TypeScript compiler is not pre-installed in /home/user/vault/node_modules."
    )


def test_package_json_has_no_build_script():
    with open(os.path.join(PROJECT_DIR, "package.json")) as handle:
        pkg = json.load(handle)
    assert "build" not in pkg.get("scripts", {}), (
        "package.json already defines a build script; the task must start unsolved."
    )


def test_cli_sources_and_build_output_absent():
    for path in (
        os.path.join(PROJECT_DIR, "src", "cli.ts"),
        os.path.join(PROJECT_DIR, "src", "service.ts"),
        os.path.join(PROJECT_DIR, "dist", "cli.js"),
    ):
        assert not os.path.exists(path), (
            f"{path} already exists; the task must start unsolved."
        )


def test_server_starts_and_branch_is_seeded(server):
    counts = _gel_query(
        "select { docs := count(Document), actors := count(Actor), "
        "workspaces := count(Workspace), memberships := count(Membership), "
        "logs := count(ActivityLog) }"
    )
    assert counts == [
        {
            "docs": 4,
            "actors": 5,
            "workspaces": 4,
            "memberships": 6,
            "logs": 0,
        }
    ], f"Unexpected seeded row counts: {counts}"


def test_seeded_documents_are_readable_without_any_actor_context(server):
    titles = _gel_query("select Document { title } order by .title")
    assert [row["title"] for row in titles] == [
        "alpha-archive-2019",
        "alpha-notes",
        "alpha-roadmap",
        "beta-charter",
    ], f"Unexpected seeded documents: {titles}"


def test_database_has_no_access_policies_yet(server):
    policies = _gel_query(
        "select schema::ObjectType { name, policy_count := count(.access_policies) } "
        "filter .name in {'default::Document', 'default::Actor', "
        "'default::Workspace', 'default::Membership', 'default::ActivityLog'} "
        "order by .name"
    )
    for row in policies:
        assert row["policy_count"] == 0, (
            f"{row['name']} already has access policies; the task must start unsolved."
        )


def test_database_has_no_actor_global_yet(server):
    globals_ = _gel_query(
        "select schema::Global { name } filter .name = 'default::current_actor_id'"
    )
    assert globals_ == [], (
        "The current_actor_id global already exists; the task must start unsolved."
    )


def test_migration_history_is_in_sync(server):
    proc = subprocess.run(
        ["gel", "migration", "status"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"gel migration status failed:\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert "up to date" in (proc.stdout + proc.stderr).lower(), (
        f"Baseline migration history is not in sync:\n{proc.stdout}\n{proc.stderr}"
    )
