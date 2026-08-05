import glob
import importlib
import json
import os
import shutil
import socket
import subprocess
import time

import pytest

PROJECT_DIR = "/home/user/harvest_api"
QUERIES_DIR = os.path.join(PROJECT_DIR, "app", "queries")
SERVICE_HOST = "127.0.0.1"
SERVICE_PORT = 8099

QUERY_FILES = [
    "list_region_growers.edgeql",
    "get_batch_detail.edgeql",
    "record_inspection.edgeql",
    "region_totals.edgeql",
]

SCHEMA_TYPES = ["Region", "Grower", "Batch", "Defect", "Inspection"]


def _start_gel_server():
    """Start the local Gel server (idempotent) and fail loudly if it does not come up."""
    assert shutil.which("gel-start") is not None, (
        "The 'gel-start' helper is not available in PATH; the image cannot start "
        "the local Gel server."
    )
    proc = subprocess.run(
        ["gel-start"],
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert proc.returncode == 0, (
        "'gel-start' failed to start the local Gel server.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


@pytest.fixture(scope="session")
def client():
    """A connected blocking Gel client; guarantees the server is running and ready."""
    _start_gel_server()
    gel = importlib.import_module("gel")
    c = gel.create_client()
    deadline = time.time() + 240
    last_error = None
    ready = False
    while time.time() < deadline:
        try:
            c.ensure_connected()
            assert c.query_single("select 1") == 1
            ready = True
            break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(2)
    if not ready:
        pytest.fail(
            "The local Gel server did not become ready within 240 seconds: "
            f"{last_error!r}"
        )
    try:
        yield c
    finally:
        c.close()


def test_gel_cli_available():
    gel_cli = shutil.which("gel")
    assert gel_cli is not None, "The 'gel' CLI is not available in PATH."
    proc = subprocess.run([gel_cli, "--version"], capture_output=True, text=True)
    assert proc.returncode == 0, (
        f"'gel --version' failed: stdout={proc.stdout} stderr={proc.stderr}"
    )
    assert "7." in proc.stdout, (
        f"Expected a Gel 7.x CLI, got: {proc.stdout.strip()!r}"
    )


def test_gel_python_client_importable():
    gel = importlib.import_module("gel")
    assert hasattr(gel, "create_client"), (
        "The installed 'gel' Python package does not expose create_client()."
    )
    assert hasattr(gel, "create_async_client"), (
        "The installed 'gel' Python package does not expose create_async_client()."
    )


def test_supporting_python_packages_importable():
    for module_name in ["pytest", "requests"]:
        importlib.import_module(module_name)


def test_project_directory_exists():
    assert os.path.isdir(PROJECT_DIR), (
        f"Project directory {PROJECT_DIR} does not exist."
    )


def test_project_is_a_gel_project():
    toml_path = os.path.join(PROJECT_DIR, "gel.toml")
    assert os.path.isfile(toml_path), f"{toml_path} does not exist."
    content = open(toml_path).read()
    assert "server-version" in content, (
        f"{toml_path} does not declare a server-version."
    )
    assert "7.1" in content, (
        f"{toml_path} does not pin Gel 7.1: {content!r}"
    )


def test_schema_file_present_with_expected_types():
    schema_path = os.path.join(PROJECT_DIR, "dbschema", "default.gel")
    assert os.path.isfile(schema_path), f"{schema_path} does not exist."
    content = open(schema_path).read()
    for type_name in SCHEMA_TYPES:
        assert f"type {type_name}" in content, (
            f"Schema {schema_path} does not define object type {type_name}."
        )
    for prop in ["kilograms", "harvested_on", "certifications", "severity", "passed"]:
        assert prop in content, (
            f"Schema {schema_path} does not mention the property {prop}."
        )


def test_migrations_present():
    migrations_dir = os.path.join(PROJECT_DIR, "dbschema", "migrations")
    assert os.path.isdir(migrations_dir), f"{migrations_dir} does not exist."
    migrations = sorted(glob.glob(os.path.join(migrations_dir, "*.edgeql")))
    assert len(migrations) >= 1, (
        f"No migration files found in {migrations_dir}."
    )


def test_schema_checksum_recorded():
    checksum_path = "/opt/task/schema.sha256"
    assert os.path.isfile(checksum_path), (
        f"The recorded schema checksum {checksum_path} is missing."
    )
    assert open(checksum_path).read().strip(), (
        f"The recorded schema checksum {checksum_path} is empty."
    )


def test_app_package_skeleton_present():
    for rel in ["app/__init__.py", "app/queries/__init__.py"]:
        path = os.path.join(PROJECT_DIR, rel)
        assert os.path.isfile(path), f"{path} does not exist."
    assert os.path.isdir(QUERIES_DIR), f"{QUERIES_DIR} does not exist."


def test_query_files_not_present_yet():
    for name in QUERY_FILES:
        path = os.path.join(QUERIES_DIR, name)
        assert not os.path.exists(path), (
            f"{path} already exists but must be written by the executor."
        )


def test_no_generated_modules_yet():
    py_files = sorted(
        os.path.basename(p) for p in glob.glob(os.path.join(QUERIES_DIR, "*.py"))
    )
    assert py_files == ["__init__.py"], (
        f"{QUERIES_DIR} should only contain __init__.py initially, found: {py_files}"
    )


def test_solution_artifacts_not_present_yet():
    for rel in ["regenerate.sh", "app/server.py"]:
        path = os.path.join(PROJECT_DIR, rel)
        assert not os.path.exists(path), (
            f"{path} already exists but must be created by the executor."
        )


def test_service_port_is_free():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(2)
        result = sock.connect_ex((SERVICE_HOST, SERVICE_PORT))
    assert result != 0, (
        f"Something is already listening on {SERVICE_HOST}:{SERVICE_PORT}."
    )


def test_connection_environment_configured():
    assert os.environ.get("GEL_DSN"), (
        "GEL_DSN is not set in the environment; clients would not know how to "
        "reach the local Gel instance."
    )


def test_gel_server_starts_and_answers_queries(client):
    assert client.query_single("select 1 + 1") == 2, (
        "The local Gel server did not answer a trivial query."
    )


def test_migrations_are_applied(client):
    gel_cli = shutil.which("gel")
    assert gel_cli is not None, "The 'gel' CLI is not available in PATH."
    proc = subprocess.run(
        [gel_cli, "migration", "status"],
        capture_output=True,
        text=True,
        cwd=PROJECT_DIR,
        timeout=180,
    )
    combined = f"{proc.stdout}\n{proc.stderr}"
    assert proc.returncode == 0, (
        f"'gel migration status' failed in {PROJECT_DIR}: {combined}"
    )
    assert "up to date" in combined.lower(), (
        f"The branch is not up to date with dbschema/migrations: {combined}"
    )


def test_schema_types_exist_in_database(client):
    names = json.loads(
        client.query_json(
            """
            select schema::ObjectType.name
            filter schema::ObjectType.name like 'default::%'
            """
        )
    )
    for type_name in SCHEMA_TYPES:
        assert f"default::{type_name}" in names, (
            f"Object type default::{type_name} is missing from the database. "
            f"Found: {names}"
        )


def test_seeded_object_counts(client):
    counts = json.loads(
        client.query_single_json(
            """
            select {
              regions := count(Region),
              growers := count(Grower),
              batches := count(Batch),
              inspections := count(Inspection),
              defects := count(Defect),
            }
            """
        )
    )
    assert counts["regions"] == 3, f"Expected 3 Region objects, got {counts}"
    assert counts["growers"] == 12, f"Expected 12 Grower objects, got {counts}"
    assert counts["batches"] == 60, f"Expected 60 Batch objects, got {counts}"
    assert counts["inspections"] == 0, (
        f"Expected no Inspection objects initially, got {counts}"
    )
    assert counts["defects"] == 0, (
        f"Expected no Defect objects initially, got {counts}"
    )


def test_seeded_regions(client):
    regions = json.loads(
        client.query_json("select Region { code, name } order by .code")
    )
    assert regions == [
        {"code": "EAS", "name": "Eastern Coast"},
        {"code": "NOR", "name": "Northern Highlands"},
        {"code": "SOU", "name": "Southern Valley"},
    ], f"Unexpected seeded regions: {regions}"


def test_seeded_grower_sample(client):
    grower = json.loads(
        client.query_single_json(
            """
            select Grower { slug, name, region: { code } }
            filter .slug = 'grower-01'
            """
        )
    )
    assert grower == {
        "slug": "grower-01",
        "name": "Grower 01",
        "region": {"code": "NOR"},
    }, f"Unexpected seeded grower-01: {grower}"


def test_seeded_batch_samples(client):
    batch = json.loads(
        client.query_single_json(
            """
            select Batch {
              code, kilograms, harvested_on, certifications,
              grower: { slug }
            }
            filter .code = 'BLK-102'
            """
        )
    )
    assert batch["kilograms"] == 210, f"Unexpected kilograms for BLK-102: {batch}"
    assert batch["harvested_on"] == "2025-02-01", (
        f"Unexpected harvested_on for BLK-102: {batch}"
    )
    assert sorted(batch["certifications"]) == ["fairtrade", "organic"], (
        f"Unexpected certifications for BLK-102: {batch}"
    )
    assert batch["grower"] == {"slug": "grower-01"}, (
        f"Unexpected grower for BLK-102: {batch}"
    )

    empty_certs = json.loads(
        client.query_single_json(
            "select Batch { kilograms, certifications } filter .code = 'BLK-505'"
        )
    )
    assert empty_certs["kilograms"] == 550, (
        f"Unexpected kilograms for BLK-505: {empty_certs}"
    )
    assert empty_certs["certifications"] == [], (
        f"BLK-505 should carry no certifications: {empty_certs}"
    )


def test_seeded_region_totals(client):
    totals = json.loads(
        client.query_json(
            """
            select Region {
              code,
              growers := count(.<region[is Grower]),
              batches := count(.<region[is Grower].<grower[is Batch]),
              kilograms := sum(.<region[is Grower].<grower[is Batch].kilograms),
            }
            order by .code
            """
        )
    )
    by_code = {row["code"]: row for row in totals}
    assert set(by_code) == {"EAS", "NOR", "SOU"}, f"Unexpected regions: {totals}"
    for code, expected_kg in [("EAS", 7500), ("NOR", 7100), ("SOU", 7300)]:
        row = by_code[code]
        assert row["growers"] == 4, f"Region {code} should have 4 growers: {row}"
        assert row["batches"] == 20, f"Region {code} should have 20 batches: {row}"
        assert abs(row["kilograms"] - expected_kg) < 1e-6, (
            f"Region {code} should total {expected_kg} kilograms: {row}"
        )
