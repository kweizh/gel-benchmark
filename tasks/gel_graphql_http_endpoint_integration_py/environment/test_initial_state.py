"""Initial-state verification for the gel_graphql_http_endpoint_integration_py task.

Validates the environment that exists BEFORE the executor starts working.
"""

import json
import os
import shutil
import subprocess

import pytest

PROJECT_DIR = "/home/user/gateway"
SEED_FILE = os.path.join(PROJECT_DIR, "data", "seed.json")
GEL_HOST = "127.0.0.1"
GEL_PORT = "5656"


@pytest.fixture(scope="session")
def gel_server():
    """Start the local Gel server (idempotent) and wait until it answers queries."""
    starter = shutil.which("start-gel-server.sh")
    assert starter is not None, "start-gel-server.sh was not found in PATH."
    proc = subprocess.run(
        [starter],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, (
        "start-gel-server.sh failed to start the local Gel server.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    probe = subprocess.run(
        ["gel", "query", "select 1"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert probe.returncode == 0, (
        "The Gel server is not answering queries after start-gel-server.sh.\n"
        f"stdout: {probe.stdout}\nstderr: {probe.stderr}"
    )
    return True


def _gel_json(query, timeout=120):
    proc = subprocess.run(
        ["gel", "query", "-F", "json", query],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    assert proc.returncode == 0, (
        f"`gel query -F json {query!r}` failed.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    return json.loads(proc.stdout)


def test_gel_cli_available():
    assert shutil.which("gel") is not None, "The `gel` CLI binary was not found in PATH."


def test_python3_available():
    assert shutil.which("python3") is not None, "python3 was not found in PATH."


def test_gel_python_client_importable():
    import gel  # noqa: F401


def test_start_script_available():
    assert (
        shutil.which("start-gel-server.sh") is not None
    ), "The helper start-gel-server.sh was not found in PATH."


def test_connection_environment_variables_present():
    assert os.environ.get("GEL_HOST") == GEL_HOST, (
        "Environment variable GEL_HOST must be preconfigured to "
        f"{GEL_HOST!r} (found {os.environ.get('GEL_HOST')!r})."
    )
    assert os.environ.get("GEL_PORT") == GEL_PORT, (
        "Environment variable GEL_PORT must be preconfigured to "
        f"{GEL_PORT!r} (found {os.environ.get('GEL_PORT')!r})."
    )
    assert os.environ.get("GEL_BRANCH") == "main", (
        "Environment variable GEL_BRANCH must be preconfigured to 'main' "
        f"(found {os.environ.get('GEL_BRANCH')!r})."
    )


def test_project_directory_exists():
    assert os.path.isdir(PROJECT_DIR), f"Project directory {PROJECT_DIR} does not exist."


def test_no_gel_toml_in_project():
    gel_toml = os.path.join(PROJECT_DIR, "gel.toml")
    assert not os.path.exists(gel_toml), (
        f"{gel_toml} must not exist in the initial environment; connection settings "
        "are provided through environment variables."
    )


def test_seed_fixture_exists_and_has_expected_shape():
    assert os.path.isfile(SEED_FILE), f"Seed fixture {SEED_FILE} does not exist."
    with open(SEED_FILE, encoding="utf-8") as handle:
        seed = json.load(handle)
    assert isinstance(seed, dict), f"{SEED_FILE} must contain a JSON object."
    assert "teams" in seed and "services" in seed, (
        f"{SEED_FILE} must contain the keys 'teams' and 'services'."
    )
    assert len(seed["teams"]) == 3, (
        f"{SEED_FILE} must describe exactly 3 teams, found {len(seed['teams'])}."
    )
    assert len(seed["services"]) == 9, (
        f"{SEED_FILE} must describe exactly 9 services, found {len(seed['services'])}."
    )
    for team in seed["teams"]:
        assert {"name", "region"} <= set(team), (
            f"Every team in {SEED_FILE} must have 'name' and 'region' keys, got {team}."
        )
    for service in seed["services"]:
        assert {"name", "tier", "active", "team"} <= set(service), (
            "Every service in the seed fixture must have 'name', 'tier', 'active' and "
            f"'team' keys, got {service}."
        )


def test_solution_files_not_present_yet():
    for relative in ("gateway.py", "report.py", "dbschema/default.gel", "out/report.json"):
        path = os.path.join(PROJECT_DIR, relative)
        assert not os.path.exists(path), (
            f"{path} must not exist before the task starts; the executor has to create it."
        )


def test_gel_server_is_reachable(gel_server):
    result = _gel_json("select 1")
    assert result == [1], f"Unexpected response from the Gel server: {result!r}."


def test_gel_server_is_version_7(gel_server):
    result = _gel_json("select sys::get_version().major")
    assert result and result[0] == 7, (
        f"The local Gel server must be major version 7, got {result!r}."
    )


def test_graphql_extension_not_enabled_yet(gel_server):
    names = _gel_json("select schema::Extension { name }")
    enabled = {entry["name"] for entry in names}
    assert "graphql" not in enabled, (
        "The 'graphql' extension must not be enabled yet; enabling it is part of the task."
    )


def test_domain_types_not_defined_yet(gel_server):
    names = _gel_json(
        "select schema::ObjectType { name } filter .name in {'default::Team', 'default::Service'}"
    )
    assert names == [], (
        "The object types default::Team and default::Service must not exist yet; "
        f"found {names!r}."
    )


def test_no_migrations_applied_yet(gel_server):
    result = _gel_json("select count(schema::Migration)")
    assert result == [0], (
        f"The database must have no applied migrations at the start, got {result!r}."
    )
