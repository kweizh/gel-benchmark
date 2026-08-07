"""Initial-state verification for the gel_delegated_constraints_inheritance_ts task.

These checks run BEFORE the executor starts working. They only assert facts that
the task description promises are already true in the environment.
"""

import json
import os
import shutil
import subprocess

import pytest

PROJECT_DIR = "/home/user/assetreg"
SRC_DIR = os.path.join(PROJECT_DIR, "src")
DBSCHEMA_DIR = os.path.join(PROJECT_DIR, "dbschema")
NODE_MODULES = os.path.join(PROJECT_DIR, "node_modules")
ENSURE_SERVER = "/usr/local/bin/gel-ensure-server.sh"


@pytest.fixture(scope="session")
def gel_server() -> str:
    """Start the bundled local Gel server (idempotent) and wait until it answers."""
    proc = subprocess.run(
        ["bash", ENSURE_SERVER],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, (
        "Failed to start the bundled local Gel server via "
        f"{ENSURE_SERVER}.\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    return ENSURE_SERVER


def test_gel_cli_available() -> None:
    assert shutil.which("gel") is not None, (
        "The `gel` CLI binary was not found in PATH; the task environment must ship it."
    )


def test_node_and_npm_available() -> None:
    assert shutil.which("node") is not None, "`node` was not found in PATH."
    assert shutil.which("npm") is not None, "`npm` was not found in PATH."


def test_ensure_server_helper_exists() -> None:
    assert os.path.isfile(ENSURE_SERVER), (
        f"The Gel server bootstrap helper {ENSURE_SERVER} is missing from the image."
    )


def test_project_directory_exists() -> None:
    assert os.path.isdir(PROJECT_DIR), (
        f"Project directory {PROJECT_DIR} does not exist."
    )


def test_project_scaffolding_exists() -> None:
    assert os.path.isfile(os.path.join(PROJECT_DIR, "gel.toml")), (
        f"{PROJECT_DIR}/gel.toml is missing; the Gel project scaffold must be present."
    )
    assert os.path.isdir(DBSCHEMA_DIR), (
        f"{DBSCHEMA_DIR} is missing; the Gel schema directory must be present."
    )
    assert os.path.isdir(SRC_DIR), (
        f"{SRC_DIR} is missing; the TypeScript source directory must be present."
    )


def test_no_migrations_applied_yet() -> None:
    migrations_dir = os.path.join(DBSCHEMA_DIR, "migrations")
    existing = []
    if os.path.isdir(migrations_dir):
        existing = [n for n in os.listdir(migrations_dir) if n.endswith(".edgeql")]
    assert not existing, (
        "The executor is expected to create the migration(s); the initial "
        f"environment must not already contain any migration file, found: {existing}"
    )


def test_solution_artifacts_absent() -> None:
    run_script = os.path.join(PROJECT_DIR, "run-ingest.sh")
    assert not os.path.exists(run_script), (
        f"{run_script} must be created by the executor, not shipped in the image."
    )


def test_package_json_present() -> None:
    package_json = os.path.join(PROJECT_DIR, "package.json")
    assert os.path.isfile(package_json), f"{package_json} is missing."
    with open(package_json, encoding="utf-8") as handle:
        data = json.load(handle)
    deps = {}
    deps.update(data.get("dependencies") or {})
    deps.update(data.get("devDependencies") or {})
    assert "gel" in deps, (
        "package.json must declare the `gel` npm client as a dependency."
    )


def test_npm_dependencies_preinstalled() -> None:
    assert os.path.isdir(NODE_MODULES), (
        f"{NODE_MODULES} is missing; npm dependencies must be pre-installed at build time."
    )
    gel_pkg = os.path.join(NODE_MODULES, "gel", "package.json")
    assert os.path.isfile(gel_pkg), (
        "The `gel` npm client is not pre-installed in "
        f"{NODE_MODULES}; the environment is offline so it must be baked into the image."
    )


def test_typescript_runtime_preinstalled() -> None:
    typescript_pkg = os.path.join(NODE_MODULES, "typescript", "package.json")
    tsx_pkg = os.path.join(NODE_MODULES, "tsx", "package.json")
    assert os.path.isfile(typescript_pkg), (
        "The `typescript` package must be pre-installed for offline use."
    )
    assert os.path.isfile(tsx_pkg), (
        "The `tsx` TypeScript runner must be pre-installed for offline use."
    )


def test_gel_connection_env_vars_present() -> None:
    for name in ("GEL_HOST", "GEL_PORT", "GEL_USER", "GEL_PASSWORD"):
        assert os.environ.get(name), (
            f"Environment variable {name} must be preconfigured for the local Gel instance."
        )


def test_local_gel_server_is_reachable(gel_server: str) -> None:
    proc = subprocess.run(
        ["gel", "query", "-F", "json", "select 1"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        "`gel query` could not reach the local Gel instance.\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert json.loads(proc.stdout.strip()) == [1], (
        f"Unexpected response from the local Gel instance: {proc.stdout!r}"
    )


def test_database_has_no_user_types_yet(gel_server: str) -> None:
    proc = subprocess.run(
        [
            "gel",
            "query",
            "-F",
            "json",
            "select count(schema::ObjectType filter .name like 'default::%')",
        ],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"Failed to introspect the database.\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert json.loads(proc.stdout.strip()) == [0], (
        "The initial database must not contain any user-defined object type in "
        f"module `default`; got {proc.stdout!r}"
    )
