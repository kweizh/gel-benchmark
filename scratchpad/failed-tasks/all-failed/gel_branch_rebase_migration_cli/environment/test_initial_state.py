import glob
import json
import os
import re
import shutil
import subprocess

import pytest

PROJECT_DIR = "/home/user/crm"
MIGRATIONS_DIR = os.path.join(PROJECT_DIR, "dbschema", "migrations")
SCHEMA_FILE = os.path.join(PROJECT_DIR, "dbschema", "default.gel")


def run(args, cwd=PROJECT_DIR, timeout=180):
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.fixture(scope="session")
def gel_server():
    """Make sure the local Gel server for the project is up and reachable."""
    start = shutil.which("gel-start.sh")
    assert start is not None, "gel-start.sh not found in PATH."
    proc = run([start], timeout=300)
    assert proc.returncode == 0, (
        f"gel-start.sh failed (exit {proc.returncode}).\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    probe = run(["gel", "query", "-F", "json", "select 1"])
    assert probe.returncode == 0, (
        "Could not query the Gel instance from the project directory.\n"
        f"stdout: {probe.stdout}\nstderr: {probe.stderr}"
    )
    return True


def gel_json(query, branch=None):
    args = ["gel", "query"]
    if branch is not None:
        args += ["--branch", branch]
    args += ["-F", "json", query]
    proc = run(args)
    assert proc.returncode == 0, (
        f"Query failed ({' '.join(args)}):\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    return json.loads(proc.stdout)


def migration_history(branch):
    proc = run(["gel", "migration", "log", "--from-db", "--branch", branch])
    assert proc.returncode == 0, (
        f"gel migration log --from-db --branch {branch} failed:\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def test_gel_cli_available():
    assert shutil.which("gel") is not None, "The gel CLI was not found in PATH."


def test_gel_server_binary_available():
    resolved = shutil.which("gel-server") or shutil.which("gel-server-6")
    if resolved is None:
        candidates = sorted(glob.glob("/usr/bin/gel-server*"))
        resolved = candidates[0] if candidates else None
    assert resolved is not None, "No gel-server binary found (gel-server / gel-server-6)."


def test_support_tools_available():
    for tool in ("git", "python3", "pytest"):
        assert shutil.which(tool) is not None, f"{tool} not found in PATH."


def test_project_directory_layout():
    assert os.path.isdir(PROJECT_DIR), f"{PROJECT_DIR} does not exist."
    assert os.path.isfile(os.path.join(PROJECT_DIR, "gel.toml")), (
        f"{PROJECT_DIR}/gel.toml is missing."
    )
    assert os.path.isfile(SCHEMA_FILE), f"{SCHEMA_FILE} is missing."
    assert os.path.isdir(MIGRATIONS_DIR), f"{MIGRATIONS_DIR} is missing."


def test_only_initial_migration_on_disk():
    files = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql")))
    assert len(files) == 1, (
        f"Expected exactly 1 migration file on disk initially, found {len(files)}: {files}"
    )
    assert os.path.basename(files[0]).startswith("00001-"), (
        f"The only migration file on disk should have index 00001, found {files[0]}"
    )


def test_schema_file_still_uses_full_name():
    content = open(SCHEMA_FILE, encoding="utf-8").read()
    assert "full_name" in content, f"{SCHEMA_FILE} should still declare full_name."
    assert "first_name" not in content, (
        f"{SCHEMA_FILE} must not declare first_name before the task is solved."
    )
    assert "last_name" not in content, (
        f"{SCHEMA_FILE} must not declare last_name before the task is solved."
    )


def test_report_script_not_present_yet():
    script = os.path.join(PROJECT_DIR, "branch_report.py")
    assert not os.path.exists(script), (
        f"{script} must not exist before the task is solved."
    )


def test_git_repository_is_on_diverged_feature_branch():
    head = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    assert head.returncode == 0, f"git rev-parse failed: {head.stderr}"
    assert head.stdout.strip() == "split_names", (
        f"Expected the checked out git branch to be split_names, got {head.stdout.strip()!r}."
    )
    branches = run(["git", "for-each-ref", "--format=%(refname:short)", "refs/heads"])
    names = set(branches.stdout.split())
    assert {"main", "split_names"} <= names, (
        f"Expected git branches main and split_names, found {sorted(names)}."
    )
    main_tip = run(["git", "rev-parse", "main"]).stdout.strip()
    feature_tip = run(["git", "rev-parse", "split_names"]).stdout.strip()
    assert main_tip and feature_tip and main_tip != feature_tip, (
        "git branches main and split_names must be diverged initially."
    )
    status = run(["git", "status", "--porcelain"])
    assert status.stdout.strip() == "", (
        f"The git working tree should be clean initially, got:\n{status.stdout}"
    )


def test_instance_has_three_branches(gel_server):
    names = sorted(gel_json("select sys::Branch.name"))
    assert names == ["main", "split_names", "stale_prototype"], (
        f"Expected branches main, split_names and stale_prototype, found {names}."
    )
    listing = run(["gel", "branch", "list"])
    assert listing.returncode == 0, f"gel branch list failed: {listing.stderr}"
    plain = re.sub(r"\x1b\[[0-9;]*m", "", listing.stdout)
    for expected in ("main", "split_names", "stale_prototype"):
        assert expected in plain, f"{expected} missing from gel branch list output."


def test_current_branch_is_the_feature_branch(gel_server):
    current = gel_json("select sys::get_current_branch()")
    assert current == ["split_names"], (
        f"The project's current branch should be split_names, got {current}."
    )


def test_migration_histories_are_diverged(gel_server):
    main_history = migration_history("main")
    feature_history = migration_history("split_names")
    assert len(main_history) == 2, (
        f"Branch main should have 2 applied migrations initially, got {main_history}."
    )
    assert len(feature_history) == 1, (
        f"Branch split_names should have 1 applied migration initially, got {feature_history}."
    )
    assert feature_history == main_history[:1], (
        "split_names should share only the initial migration with main, got "
        f"{feature_history} vs {main_history}."
    )


def test_main_branch_holds_unconverted_production_data(gel_server):
    count = gel_json("select count(Contact)", branch="main")
    assert count == [12], f"Branch main should hold 12 contacts initially, got {count}."
    rows = gel_json(
        "select Contact { email, full_name, domain } order by .email", branch="main"
    )
    assert len(rows) == 12, f"Expected 12 rows on main, got {len(rows)}."
    for row in rows:
        assert row.get("full_name"), f"Contact {row} should still have a full_name."
        assert row.get("domain"), f"Contact {row} should already have a domain."


def test_main_branch_has_no_split_name_properties(gel_server):
    pointers = gel_json(
        "select schema::ObjectType { pointers: { name } } "
        "filter .name = 'default::Contact'",
        branch="main",
    )
    assert pointers, "default::Contact not found on branch main."
    names = {p["name"] for p in pointers[0]["pointers"]}
    assert "full_name" in names, f"full_name missing from Contact on main: {sorted(names)}"
    assert "first_name" not in names, "first_name must not exist on main before solving."
    assert "last_name" not in names, "last_name must not exist on main before solving."


def test_feature_branch_holds_only_sandbox_contacts(gel_server):
    rows = gel_json(
        "select Contact { email, full_name } order by .email", branch="split_names"
    )
    assert len(rows) == 3, (
        f"Branch split_names should hold 3 throwaway contacts initially, got {rows}."
    )
    for row in rows:
        assert row["email"].endswith("@sandbox.test"), (
            f"Unexpected contact on split_names: {row}"
        )


def test_feature_branch_has_no_domain_property_yet(gel_server):
    pointers = gel_json(
        "select schema::ObjectType { pointers: { name } } "
        "filter .name = 'default::Contact'",
        branch="split_names",
    )
    assert pointers, "default::Contact not found on branch split_names."
    names = {p["name"] for p in pointers[0]["pointers"]}
    assert "full_name" in names, (
        f"full_name missing from Contact on split_names: {sorted(names)}"
    )
    assert "domain" not in names, (
        "split_names must not have the domain property before the rebase."
    )
