import json
import os
import shutil
import subprocess

import pytest

PROJECT_DIR = "/home/user/project"
SEED_PATH = os.path.join(PROJECT_DIR, "data", "seed.json")
GEL_START = "/usr/local/bin/gel-start"
GEL_STOP = "/usr/local/bin/gel-stop"

SQL_ENV_VARS = (
    "GEL_SQL_HOST",
    "GEL_SQL_PORT",
    "GEL_SQL_USER",
    "GEL_SQL_PASSWORD",
    "GEL_SQL_DBNAME",
)


@pytest.fixture(scope="session", autouse=True)
def _chdir_to_project():
    """The Gel client resolves the linked project by walking up from the CWD."""
    previous = os.getcwd()
    if os.path.isdir(PROJECT_DIR):
        os.chdir(PROJECT_DIR)
    try:
        yield
    finally:
        os.chdir(previous)


@pytest.fixture(scope="session")
def server():
    """Start the local Gel server (idempotent) and wait for readiness."""
    assert os.path.isfile(GEL_START), f"{GEL_START} is missing from the image."
    proc = subprocess.run([GEL_START], capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, (
        f"{GEL_START} failed with code {proc.returncode}.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    return True


@pytest.fixture(scope="session")
def gel_client(server):
    import gel

    client = gel.create_client()
    try:
        yield client
    finally:
        client.close()


def test_gel_cli_available():
    assert shutil.which("gel") is not None, "The `gel` CLI binary was not found in PATH."


def test_gel_server_binary_available():
    candidates = ["gel-server", "gel-server-6", "gel-server-7"]
    found = [c for c in candidates if shutil.which(c) is not None]
    if not found:
        found = [
            name
            for name in sorted(os.listdir("/usr/bin"))
            if name.startswith("gel-server")
        ]
    assert found, (
        "No gel-server binary was found (looked for gel-server, gel-server-6, "
        "gel-server-7 in PATH and for /usr/bin/gel-server*)."
    )


def test_server_control_scripts_are_executable():
    for path in (GEL_START, GEL_STOP):
        assert os.path.isfile(path), f"Server control script {path} is missing."
        assert os.access(path, os.X_OK), f"Server control script {path} is not executable."


def test_python_gel_client_importable():
    import gel  # noqa: F401


def test_python_psycopg_importable():
    import psycopg  # noqa: F401


def test_project_directory_exists():
    assert os.path.isdir(PROJECT_DIR), f"Project directory {PROJECT_DIR} does not exist."


def test_project_is_a_gel_project():
    toml_path = os.path.join(PROJECT_DIR, "gel.toml")
    assert os.path.isfile(toml_path), f"{toml_path} does not exist; the project is not initialized."


def test_schema_directory_exists_and_has_no_user_schema():
    schema_dir = os.path.join(PROJECT_DIR, "dbschema")
    assert os.path.isdir(schema_dir), f"{schema_dir} does not exist."
    migrations_dir = os.path.join(schema_dir, "migrations")
    existing = []
    if os.path.isdir(migrations_dir):
        existing = [n for n in os.listdir(migrations_dir) if n.endswith(".edgeql")]
    assert not existing, (
        f"Expected no migrations in {migrations_dir} before the task starts, found: {existing}"
    )


def test_solution_entrypoint_not_present_yet():
    entrypoint = os.path.join(PROJECT_DIR, "dualview.py")
    assert not os.path.exists(entrypoint), (
        f"{entrypoint} must be created by the executor, but it already exists."
    )


def test_seed_dataset_present_and_well_formed():
    assert os.path.isfile(SEED_PATH), f"Seed dataset {SEED_PATH} does not exist."
    with open(SEED_PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    assert isinstance(data, dict), "Seed dataset must be a JSON object."
    for key in ("artists", "albums", "tracks"):
        assert key in data, f"Seed dataset is missing the top-level `{key}` array."
        assert isinstance(data[key], list), f"Seed dataset key `{key}` must be an array."
        assert data[key], f"Seed dataset key `{key}` must not be empty."
    for artist in data["artists"]:
        for key in ("handle", "name", "country", "aliases"):
            assert key in artist, f"Artist entry is missing `{key}`: {artist}"
    for album in data["albums"]:
        for key in ("slug", "title", "year", "label"):
            assert key in album, f"Album entry is missing `{key}`: {album}"
    for track in data["tracks"]:
        for key in (
            "slug",
            "title",
            "album",
            "duration_ms",
            "royalty_rate",
            "tags",
            "contributors",
        ):
            assert key in track, f"Track entry is missing `{key}`: {track}"
        assert isinstance(track["royalty_rate"], str), (
            "`royalty_rate` must be given as an exact decimal string: " f"{track}"
        )
        for contributor in track["contributors"]:
            for key in ("artist", "role", "share_bp"):
                assert key in contributor, (
                    f"Contributor entry is missing `{key}`: {contributor}"
                )


def test_sql_protocol_environment_variables_are_set():
    for name in SQL_ENV_VARS:
        value = os.environ.get(name)
        assert value, f"Environment variable {name} must be set in the task environment."
    port = os.environ["GEL_SQL_PORT"]
    assert port.isdigit(), f"GEL_SQL_PORT must be numeric, got {port!r}."


def test_gel_server_reachable_over_binary_protocol(gel_client):
    assert gel_client.query_single("select 1") == 1, (
        "The local Gel server did not answer a trivial EdgeQL query."
    )


def test_gel_server_reachable_over_sql_protocol(server):
    import psycopg

    with psycopg.connect(
        host=os.environ["GEL_SQL_HOST"],
        port=int(os.environ["GEL_SQL_PORT"]),
        user=os.environ["GEL_SQL_USER"],
        password=os.environ["GEL_SQL_PASSWORD"],
        dbname=os.environ["GEL_SQL_DBNAME"],
        connect_timeout=30,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("select 1")
            row = cur.fetchone()
    assert row is not None and row[0] == 1, (
        "The Gel SQL (PostgreSQL wire protocol) adapter did not answer `select 1`."
    )


def test_catalog_module_is_not_defined_yet(gel_client):
    names = gel_client.query(
        """
        select schema::ObjectType { name }
        filter .name like 'catalog::%'
        """
    )
    assert not names, (
        "The `catalog` module must be created by the executor, but object types "
        f"already exist: {[n.name for n in names]}"
    )


def test_migration_status_reports_no_user_migrations(gel_client):
    proc = subprocess.run(
        ["gel", "migration", "status"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )
    combined = (proc.stdout or "") + (proc.stderr or "")
    assert "connection refused" not in combined.lower(), (
        f"`gel migration status` could not reach the server: {combined}"
    )
