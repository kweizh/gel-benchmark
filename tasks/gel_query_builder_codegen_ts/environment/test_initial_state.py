import json
import os
import shutil
import subprocess

import pytest

PROJECT_DIR = "/home/user/catalog"
SCHEMA_FILE = os.path.join(PROJECT_DIR, "dbschema", "default.gel")
MIGRATIONS_DIR = os.path.join(PROJECT_DIR, "dbschema", "migrations")
BUILDER_DIR = os.path.join(PROJECT_DIR, "dbschema", "edgeql-js")
DATA_FILE = os.path.join(PROJECT_DIR, "data", "resources.json")


def run(args, cwd=PROJECT_DIR, timeout=180):
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.fixture(scope="session")
def server():
    proc = run(["gel-start.sh"], cwd="/tmp", timeout=300)
    assert proc.returncode == 0, (
        f"gel-start.sh failed to start the local Gel server: {proc.stdout}\n{proc.stderr}"
    )
    return True


def test_gel_cli_available():
    assert shutil.which("gel") is not None, "The 'gel' CLI binary was not found in PATH."


def test_node_toolchain_available():
    assert shutil.which("node") is not None, "The 'node' binary was not found in PATH."
    assert shutil.which("npx") is not None, "The 'npx' binary was not found in PATH."


def test_gel_start_helper_available():
    assert shutil.which("gel-start.sh") is not None, (
        "The 'gel-start.sh' helper script was not found in PATH."
    )


def test_project_directory_exists():
    assert os.path.isdir(PROJECT_DIR), f"Project directory {PROJECT_DIR} does not exist."


def test_project_files_exist():
    for rel in ["gel.toml", "package.json", "tsconfig.json", "src/cli.ts"]:
        path = os.path.join(PROJECT_DIR, rel)
        assert os.path.isfile(path), f"Expected project file {path} to exist."


def test_tsconfig_is_strict():
    with open(os.path.join(PROJECT_DIR, "tsconfig.json")) as handle:
        tsconfig = json.load(handle)
    assert tsconfig.get("compilerOptions", {}).get("strict") is True, (
        "tsconfig.json must start with compilerOptions.strict set to true."
    )


def test_npm_dependencies_are_preinstalled():
    for rel in [
        "node_modules/gel/package.json",
        "node_modules/@gel/generate/package.json",
        "node_modules/typescript/package.json",
        "node_modules/tsx/package.json",
    ]:
        path = os.path.join(PROJECT_DIR, rel)
        assert os.path.isfile(path), (
            f"Expected pre-installed npm dependency {path} (the task must be solvable offline)."
        )


def test_schema_file_only_defines_author():
    with open(SCHEMA_FILE) as handle:
        schema = handle.read()
    assert "type Author" in schema, f"{SCHEMA_FILE} should already define the Author type."
    for missing in ["Resource", "Article", "Video"]:
        assert missing not in schema, (
            f"{SCHEMA_FILE} must not already define '{missing}' - the executor has to add it."
        )


def test_initial_migration_is_present():
    assert os.path.isdir(MIGRATIONS_DIR), f"Expected {MIGRATIONS_DIR} to exist."
    migrations = sorted(
        name for name in os.listdir(MIGRATIONS_DIR) if name.endswith(".edgeql")
    )
    assert len(migrations) == 1, (
        f"Expected exactly one seeded migration in {MIGRATIONS_DIR}, found: {migrations}"
    )


def test_seeded_query_builder_is_stale():
    assert os.path.isfile(os.path.join(BUILDER_DIR, "index.ts")), (
        f"Expected a pre-generated query builder at {BUILDER_DIR}/index.ts."
    )
    default_module = os.path.join(BUILDER_DIR, "modules", "default.ts")
    assert os.path.isfile(default_module), f"Expected {default_module} to exist."
    with open(default_module) as handle:
        generated = handle.read()
    assert "Author" in generated, "The pre-generated query builder should know about Author."
    for missing in ["Article", "Video", "Resource"]:
        assert missing not in generated, (
            f"The pre-generated query builder must not already contain '{missing}'."
        )


def test_catalog_data_file():
    assert os.path.isfile(DATA_FILE), f"Expected catalog input file {DATA_FILE} to exist."
    with open(DATA_FILE) as handle:
        entries = json.load(handle)
    assert isinstance(entries, list) and len(entries) == 9, (
        f"Expected {DATA_FILE} to hold a list of 9 catalog entries."
    )
    kinds = {entry["kind"] for entry in entries}
    assert kinds == {"article", "video"}, (
        f"Expected catalog entries of kind 'article' and 'video', found: {sorted(kinds)}"
    )


def test_cli_stub_is_not_implemented():
    proc = run(["npx", "tsx", "src/cli.ts", "load"])
    assert proc.returncode != 0, (
        "The seeded src/cli.ts stub must not already implement the 'load' subcommand."
    )


def test_authors_are_seeded_and_catalog_is_empty(server):
    proc = run(
        ["gel", "query", "-F", "json", "select count(Author)"],
        timeout=300,
    )
    assert proc.returncode == 0, f"Failed to query the local Gel instance: {proc.stderr}"
    assert json.loads(proc.stdout) == [5], (
        f"Expected 5 seeded Author objects, got: {proc.stdout}"
    )

    proc = run(["gel", "query", "-F", "json", "select count(Resource)"], timeout=300)
    assert proc.returncode != 0, (
        "The 'Resource' type must not exist in the initial database schema."
    )


def test_migration_status_is_in_sync(server):
    proc = run(["gel", "migration", "status"], timeout=300)
    assert proc.returncode == 0, (
        f"The seeded database should start in sync with its migrations: {proc.stdout}\n{proc.stderr}"
    )
