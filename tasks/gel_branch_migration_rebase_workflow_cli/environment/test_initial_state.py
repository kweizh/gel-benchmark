import json
import os
import shutil
import stat
import subprocess

import pytest

PROJECT_DIR = "/home/user/branchlab"
DBSCHEMA_DIR = os.path.join(PROJECT_DIR, "dbschema")
MIGRATIONS_DIR = os.path.join(DBSCHEMA_DIR, "migrations")
FIXTURES_DIR = "/opt/task-fixtures"


def _run(argv, timeout=600):
    """Run a command with the Gel project as the working directory."""
    env = dict(os.environ)
    if os.geteuid() == 0:
        # the project link and instance credentials were created for root at build time
        env["HOME"] = "/root"
    return subprocess.run(
        argv,
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def _query(query, branch=None, output_format="json"):
    argv = ["gel"]
    if branch:
        argv += ["-b", branch]
    argv += ["query", "-F", output_format, query]
    proc = _run(argv)
    assert proc.returncode == 0, "gel query failed: {}\n{}".format(query, proc.stderr)
    return json.loads(proc.stdout)


@pytest.fixture(scope="session")
def gel_server():
    """Guarantee the local Gel instance is up before any CLI/database check runs."""
    proc = _run(["gel-start"], timeout=600)
    assert proc.returncode == 0, "gel-start failed to bring up instance 'devinst': {}{}".format(
        proc.stdout, proc.stderr
    )
    return True


def test_gel_cli_available():
    assert shutil.which("gel") is not None, "The `gel` CLI binary was not found in PATH."


def test_gel_start_helper_available():
    assert shutil.which("gel-start") is not None, (
        "The `gel-start` helper used to boot the local Gel instance was not found in PATH."
    )


def test_project_directory_exists():
    assert os.path.isdir(PROJECT_DIR), "Project directory {} does not exist.".format(PROJECT_DIR)


def test_project_manifest_exists():
    manifest = os.path.join(PROJECT_DIR, "gel.toml")
    assert os.path.isfile(manifest), "Gel project manifest {} does not exist.".format(manifest)


def test_schema_source_file_exists():
    schema = os.path.join(DBSCHEMA_DIR, "default.gel")
    assert os.path.isfile(schema), "Schema source file {} does not exist.".format(schema)
    content = open(schema).read()
    assert "type Author" in content, "Initial schema should declare the `Author` object type."
    assert "type Article" in content, "Initial schema should declare the `Article` object type."


def test_initial_schema_has_no_feature_objects():
    content = open(os.path.join(DBSCHEMA_DIR, "default.gel")).read()
    assert "Tag" not in content, "Initial schema must not contain the `Tag` type yet."
    assert "review_state" not in content, (
        "Initial schema must not contain the `review_state` property yet."
    )


def test_exactly_one_initial_migration_file():
    assert os.path.isdir(MIGRATIONS_DIR), "Migrations directory {} does not exist.".format(
        MIGRATIONS_DIR
    )
    files = sorted(f for f in os.listdir(MIGRATIONS_DIR) if f.endswith(".edgeql"))
    assert len(files) == 1, "Expected exactly one pre-existing migration file, found: {}".format(
        files
    )


def test_project_is_a_git_repository():
    assert os.path.isdir(os.path.join(PROJECT_DIR, ".git")), (
        "Project directory {} is expected to be a git repository.".format(PROJECT_DIR)
    )


def test_build_time_fixtures_exist():
    for name in ("initial_articles.json", "initial_authors.json", "initial_migration.txt"):
        path = os.path.join(FIXTURES_DIR, name)
        assert os.path.isfile(path), "Build-time fixture {} is missing.".format(path)
    articles = json.load(open(os.path.join(FIXTURES_DIR, "initial_articles.json")))
    authors = json.load(open(os.path.join(FIXTURES_DIR, "initial_authors.json")))
    assert len(articles) == 12, "Expected 12 seeded articles in the build-time snapshot."
    assert len(authors) == 4, "Expected 4 seeded authors in the build-time snapshot."


def test_deliverables_do_not_exist_yet():
    for name in ("reconcile.sh", "reconcile-report.json"):
        path = os.path.join(PROJECT_DIR, name)
        assert not os.path.exists(path), (
            "{} must not exist before the task starts.".format(path)
        )


def test_instance_starts_and_is_reachable(gel_server):
    assert _query("select 1") == [1], "The local Gel instance did not answer a trivial query."


def test_only_main_branch_exists(gel_server):
    branches = sorted(_query("select sys::Branch.name"))
    assert branches == ["main"], "Expected only the `main` branch initially, found {}.".format(
        branches
    )


def test_active_branch_is_main(gel_server):
    assert _query("select sys::get_current_branch()") == ["main"], (
        "The project's active branch should be `main` initially."
    )


def test_single_applied_migration(gel_server):
    count = _query("select count(schema::Migration)")
    assert count == [1], "Expected exactly one applied migration on `main`, found {}.".format(count)
    expected = open(os.path.join(FIXTURES_DIR, "initial_migration.txt")).read().strip()
    names = _query("select schema::Migration.name")
    assert names == [expected], (
        "The applied migration on `main` should be {}, found {}.".format(expected, names)
    )


def test_seed_data_present(gel_server):
    assert _query("select count(Author)") == [4], "Expected 4 seeded `Author` objects."
    assert _query("select count(Article)") == [12], "Expected 12 seeded `Article` objects."


def test_feature_schema_absent(gel_server):
    tags = _query("select schema::ObjectType.name filter schema::ObjectType.name = 'default::Tag'")
    assert tags == [], "The `Tag` object type must not exist before the task starts."
    props = _query(
        "select schema::Property.name filter schema::Property.name = 'review_state'"
    )
    assert props == [], "The `review_state` property must not exist before the task starts."


def test_migration_status_is_in_sync(gel_server):
    proc = _run(["gel", "migration", "status", "--quiet"])
    assert proc.returncode == 0, (
        "`gel migration status` should report an in-sync project initially, exit={} {}".format(
            proc.returncode, proc.stderr
        )
    )


def test_project_directory_is_writable_by_user():
    st = os.stat(PROJECT_DIR)
    assert bool(st.st_mode & stat.S_IWUSR), "Project directory must be writable."
