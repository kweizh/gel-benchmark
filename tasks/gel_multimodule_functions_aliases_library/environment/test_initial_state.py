import json
import os
import shutil
import subprocess

import pytest

PROJECT_DIR = "/home/user/pricing"
DBSCHEMA_DIR = os.path.join(PROJECT_DIR, "dbschema")
GEL_TOML = os.path.join(PROJECT_DIR, "gel.toml")


def _run(args, cwd=PROJECT_DIR, timeout=180):
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.fixture(scope="session")
def server():
    """Start the local Gel server (idempotent) and make sure it accepts queries."""
    assert shutil.which("gel-start") is not None, (
        "gel-start helper script not found in PATH; the local Gel server cannot be started."
    )
    proc = _run(["gel-start"], timeout=300)
    assert proc.returncode == 0, (
        "gel-start failed to bring up the local Gel server.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    return True


def test_gel_cli_available():
    assert shutil.which("gel") is not None, "gel CLI binary not found in PATH."


def test_gel_server_binary_available():
    assert shutil.which("gel-server") is not None, (
        "gel-server binary not found in PATH; a local Gel server must be installed in the container."
    )


def test_gel_python_client_importable():
    import gel  # noqa: F401


def test_project_directory_exists():
    assert os.path.isdir(PROJECT_DIR), f"Project directory {PROJECT_DIR} does not exist."


def test_gel_toml_exists_and_is_a_project_manifest():
    assert os.path.isfile(GEL_TOML), f"Project manifest {GEL_TOML} does not exist."
    content = open(GEL_TOML).read()
    assert "[instance]" in content, (
        f"{GEL_TOML} does not contain an [instance] table; it is not a usable Gel project manifest."
    )


def test_dbschema_directory_exists():
    assert os.path.isdir(DBSCHEMA_DIR), f"Schema directory {DBSCHEMA_DIR} does not exist."


def test_connection_environment_variables_are_exported():
    assert os.environ.get("GEL_DSN"), (
        "GEL_DSN is not exported in the environment; clients would not know how to connect."
    )
    tls = os.environ.get("GEL_CLIENT_TLS_SECURITY") or os.environ.get("GEL_CLIENT_SECURITY")
    assert tls, (
        "Neither GEL_CLIENT_TLS_SECURITY nor GEL_CLIENT_SECURITY is exported; the local "
        "self-signed certificate could not be accepted."
    )


def test_local_server_accepts_queries(server):
    proc = _run(["gel", "query", "select 1 + 1"])
    assert proc.returncode == 0, (
        f"Could not query the local Gel instance.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "2" in proc.stdout, f"Unexpected query output from the local instance: {proc.stdout!r}"


def test_task_modules_do_not_exist_yet(server):
    proc = _run(
        [
            "gel",
            "query",
            "--output-format=json",
            "select schema::Module.name",
        ]
    )
    assert proc.returncode == 0, (
        f"Failed to introspect modules.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    modules = json.loads(proc.stdout)
    for name in ("util", "billing", "reports"):
        assert name not in modules, (
            f"Module {name!r} already exists in the instance; the task work must not be pre-applied."
        )


def test_schema_library_files_are_not_present_yet():
    for filename in ("util.gel", "billing.gel", "reports.gel"):
        path = os.path.join(DBSCHEMA_DIR, filename)
        assert not os.path.exists(path), (
            f"{path} already exists; the solution must not be pre-created."
        )


def test_no_migrations_have_been_created_yet():
    migrations_dir = os.path.join(DBSCHEMA_DIR, "migrations")
    if os.path.isdir(migrations_dir):
        existing = [f for f in os.listdir(migrations_dir) if f.endswith(".edgeql")]
        assert existing == [], (
            f"Migration files already exist in {migrations_dir}: {existing}"
        )
