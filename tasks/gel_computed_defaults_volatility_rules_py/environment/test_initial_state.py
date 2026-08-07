import glob
import os
import shutil

PROJECT_DIR = "/home/user/gelproj"
SCHEMA_DIR = os.path.join(PROJECT_DIR, "dbschema")
START_SCRIPT = "/usr/local/bin/gel-start.sh"


def test_gel_cli_available():
    assert shutil.which("gel") is not None, "The `gel` CLI binary was not found in PATH."


def test_gel_server_binary_available():
    candidates = [c for c in ("gel-server", "gel-server-6") if shutil.which(c)]
    if not candidates:
        candidates = sorted(glob.glob("/usr/bin/gel-server*"))
    assert candidates, (
        "No local Gel server binary found (looked for `gel-server`, `gel-server-6` "
        "in PATH and /usr/bin/gel-server*)."
    )


def test_gel_python_client_importable():
    import gel  # noqa: F401

    assert gel is not None, "The Gel Python client (`gel`) could not be imported."


def test_start_script_present_and_executable():
    assert os.path.isfile(START_SCRIPT), f"Helper script {START_SCRIPT} does not exist."
    assert os.access(START_SCRIPT, os.X_OK), f"Helper script {START_SCRIPT} is not executable."


def test_project_directory_exists():
    assert os.path.isdir(PROJECT_DIR), f"Project directory {PROJECT_DIR} does not exist."


def test_schema_directory_exists():
    assert os.path.isdir(SCHEMA_DIR), f"Schema directory {SCHEMA_DIR} does not exist."


def test_project_manifest_exists():
    manifest = os.path.join(PROJECT_DIR, "gel.toml")
    assert os.path.isfile(manifest), f"Gel project manifest {manifest} does not exist."
    with open(manifest, encoding="utf-8") as handle:
        content = handle.read()
    assert "server-version" in content, (
        "Gel project manifest gel.toml does not declare a `server-version` entry."
    )


def test_schema_file_present_but_empty_of_types():
    schema_files = sorted(glob.glob(os.path.join(SCHEMA_DIR, "*.gel")))
    assert schema_files, f"No *.gel schema file found under {SCHEMA_DIR}."
    combined = ""
    for path in schema_files:
        with open(path, encoding="utf-8") as handle:
            combined += handle.read()
    assert "module default" in combined, (
        "The starter schema does not declare `module default`."
    )
    assert "type Sample" not in combined, (
        "The starter schema already declares the Sample type; the initial state must be empty."
    )


def test_connection_environment_variables_present():
    for name in (
        "GEL_HOST",
        "GEL_PORT",
        "GEL_USER",
        "GEL_BRANCH",
        "GEL_CLIENT_TLS_SECURITY",
    ):
        assert os.environ.get(name), f"Environment variable {name} is not set."


def test_solution_artifacts_absent():
    for name in ("main.py", "semantics.py", "report.json"):
        path = os.path.join(PROJECT_DIR, name)
        assert not os.path.exists(path), (
            f"{path} must not exist before the task starts; it is part of the solution."
        )


def test_migrations_not_created_yet():
    migrations = glob.glob(os.path.join(SCHEMA_DIR, "migrations", "*.edgeql"))
    assert not migrations, (
        "Migration files already exist in the initial state; the executor must create them."
    )
