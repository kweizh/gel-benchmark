import glob
import json
import os
import shutil
import subprocess

import pytest

PROJECT_DIR = "/home/user/catalog-sync"
GEL_START = "/usr/local/bin/gel-start.sh"
SERVER_BIN_CANDIDATES = ("gel-server", "gel-server-6", "gel-server-7")


def _resolve_server_binary():
    for name in SERVER_BIN_CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    matches = sorted(glob.glob("/usr/bin/gel-server*"))
    return matches[0] if matches else None


def _gel_query(query, fmt="json"):
    proc = subprocess.run(
        ["gel", "query", "-F", fmt, query],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"gel query failed for {query!r}: rc={proc.returncode} "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    return proc.stdout


@pytest.fixture(scope="session")
def gel_instance():
    proc = subprocess.run(
        ["bash", GEL_START],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, (
        "Failed to start the local Gel instance with "
        f"{GEL_START}: rc={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    return True


def test_gel_cli_available():
    assert shutil.which("gel") is not None, "The 'gel' CLI was not found in PATH."


def test_gel_server_binary_available():
    assert _resolve_server_binary() is not None, (
        "No gel-server binary could be resolved (tried "
        f"{', '.join(SERVER_BIN_CANDIDATES)} and /usr/bin/gel-server*)."
    )


def test_gel_start_script_is_executable():
    assert os.path.isfile(GEL_START), f"Startup helper {GEL_START} does not exist."
    assert os.access(GEL_START, os.X_OK), f"Startup helper {GEL_START} is not executable."


def test_node_and_npm_available():
    assert shutil.which("node") is not None, "The 'node' binary was not found in PATH."
    assert shutil.which("npm") is not None, "The 'npm' binary was not found in PATH."


def test_node_major_version_is_pinned():
    proc = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"'node --version' failed: {proc.stderr!r}"
    version = proc.stdout.strip()
    assert version.startswith("v22."), f"Expected Node.js 22.x, got {version!r}."


def test_project_directory_exists():
    assert os.path.isdir(PROJECT_DIR), f"Project directory {PROJECT_DIR} does not exist."


def test_gel_toml_exists():
    path = os.path.join(PROJECT_DIR, "gel.toml")
    assert os.path.isfile(path), f"Gel project file {path} does not exist."


def test_dbschema_default_gel_exists():
    path = os.path.join(PROJECT_DIR, "dbschema", "default.gel")
    assert os.path.isfile(path), f"Schema file {path} does not exist."


def test_package_json_pins_gel_client():
    path = os.path.join(PROJECT_DIR, "package.json")
    assert os.path.isfile(path), f"{path} does not exist."
    with open(path) as handle:
        manifest = json.load(handle)
    deps = {}
    deps.update(manifest.get("dependencies") or {})
    deps.update(manifest.get("devDependencies") or {})
    assert "gel" in deps, "package.json does not declare the 'gel' client dependency."
    assert "typescript" in deps, "package.json does not declare the 'typescript' dependency."


def test_node_modules_preinstalled_offline():
    gel_pkg = os.path.join(PROJECT_DIR, "node_modules", "gel", "package.json")
    assert os.path.isfile(gel_pkg), (
        f"The 'gel' npm client is not pre-installed at {gel_pkg}; the task environment is offline."
    )
    with open(gel_pkg) as handle:
        assert json.load(handle).get("version") == "2.2.0", (
            "Pre-installed 'gel' npm client is not the pinned 2.2.0 release."
        )
    tsc = os.path.join(PROJECT_DIR, "node_modules", ".bin", "tsc")
    assert os.path.exists(tsc), f"TypeScript compiler is not pre-installed at {tsc}."


def test_tsconfig_exists():
    path = os.path.join(PROJECT_DIR, "tsconfig.json")
    assert os.path.isfile(path), f"{path} does not exist."


def test_example_batch_file_exists():
    path = os.path.join(PROJECT_DIR, "examples", "batch-example.json")
    assert os.path.isfile(path), f"Example batch file {path} does not exist."
    with open(path) as handle:
        payload = json.load(handle)
    assert isinstance(payload, list) and payload, (
        f"Example batch file {path} must contain a non-empty JSON array."
    )


def test_catalog_schema_not_yet_declared():
    path = os.path.join(PROJECT_DIR, "dbschema", "default.gel")
    with open(path) as handle:
        content = handle.read()
    for type_name in ("Product", "Category", "Tag"):
        assert f"type {type_name}" not in content, (
            f"dbschema/default.gel already declares object type {type_name}; "
            "the task must start without the catalog schema."
        )


def test_no_migrations_applied_yet():
    matches = glob.glob(os.path.join(PROJECT_DIR, "dbschema", "migrations", "*.edgeql"))
    assert matches == [], f"Unexpected pre-existing migration files: {matches}"


def test_sync_cli_not_implemented_yet():
    assert not os.path.exists(os.path.join(PROJECT_DIR, "dist", "sync.js")), (
        "dist/sync.js already exists; the task must not be pre-solved."
    )
    assert not os.path.exists(os.path.join(PROJECT_DIR, "src", "sync.ts")), (
        "src/sync.ts already exists; the task must not be pre-solved."
    )


def test_instance_starts_and_answers_queries(gel_instance):
    output = _gel_query("select 1", fmt="json")
    assert "1" in output, f"Unexpected response from the local Gel instance: {output!r}"


def test_project_is_linked_to_local_instance(gel_instance):
    proc = subprocess.run(
        ["gel", "project", "info"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        "The project directory is not linked to a Gel instance: "
        f"rc={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )


def test_database_has_no_catalog_types_yet(gel_instance):
    output = _gel_query(
        "select schema::ObjectType { name } "
        "filter .name in {'default::Product', 'default::Category', 'default::Tag'}"
    )
    found = json.loads(output)
    assert found == [], f"Catalog object types already exist in the database: {found}"
