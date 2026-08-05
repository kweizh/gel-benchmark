import os
import shutil
import subprocess

PROJECT_DIR = "/home/user/shiftops"
SCHEMA_FILE = os.path.join(PROJECT_DIR, "dbschema", "default.gel")
ENGINE_FILE = os.path.join(PROJECT_DIR, "shiftops_engine.py")


def test_gel_cli_available():
    assert shutil.which("gel") is not None, "The `gel` CLI was not found in PATH."


def test_gel_cli_runs():
    proc = subprocess.run(
        ["gel", "--version"], capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, (
        f"`gel --version` failed with exit code {proc.returncode}: "
        f"{proc.stdout}{proc.stderr}"
    )
    assert "Gel CLI" in proc.stdout, (
        f"Unexpected output from `gel --version`: {proc.stdout!r}"
    )


def test_gel_start_helper_available():
    assert shutil.which("gel-start") is not None, (
        "The `gel-start` helper used to start the local Gel server "
        "was not found in PATH."
    )


def test_gel_python_client_importable():
    import gel  # noqa: F401

    assert hasattr(gel, "create_client"), (
        "The `gel` Python client library is installed but does not expose "
        "`create_client`."
    )


def test_pytest_available():
    import pytest  # noqa: F401

    assert pytest is not None, "pytest is not importable in the environment."


def test_gel_instance_env_var_is_set():
    assert os.environ.get("GEL_INSTANCE") == "shiftdb", (
        "Environment variable GEL_INSTANCE is expected to be 'shiftdb', got "
        f"{os.environ.get('GEL_INSTANCE')!r}."
    )


def test_project_directory_exists():
    assert os.path.isdir(PROJECT_DIR), (
        f"Project directory {PROJECT_DIR} does not exist."
    )


def test_gel_toml_exists():
    gel_toml = os.path.join(PROJECT_DIR, "gel.toml")
    assert os.path.isfile(gel_toml), (
        f"Expected the scaffolded Gel project manifest at {gel_toml}."
    )


def test_dbschema_directory_exists():
    dbschema = os.path.join(PROJECT_DIR, "dbschema")
    assert os.path.isdir(dbschema), (
        f"Expected the scaffolded schema directory at {dbschema}."
    )


def test_default_schema_file_exists_and_is_empty_of_task_types():
    assert os.path.isfile(SCHEMA_FILE), (
        f"Expected the scaffolded schema file at {SCHEMA_FILE}."
    )
    content = open(SCHEMA_FILE, encoding="utf-8").read()
    assert "module default" in content, (
        f"{SCHEMA_FILE} is expected to declare `module default`."
    )
    for type_name in ("Worker", "Shift", "AvailabilityWindow"):
        assert type_name not in content, (
            f"{SCHEMA_FILE} unexpectedly already declares `{type_name}`; the "
            "initial state must not contain the solution schema."
        )


def test_project_is_linked_to_the_local_instance():
    proc = subprocess.run(
        ["gel", "project", "info", "--instance-name"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        "`gel project info --instance-name` failed inside "
        f"{PROJECT_DIR}: {proc.stdout}{proc.stderr}"
    )
    assert "shiftdb" in proc.stdout, (
        "The Gel project is expected to be linked to the instance 'shiftdb', "
        f"got: {proc.stdout!r}"
    )


def test_engine_module_not_present_yet():
    assert not os.path.exists(ENGINE_FILE), (
        f"{ENGINE_FILE} must not exist in the initial state; it is the "
        "artifact the task asks for."
    )
