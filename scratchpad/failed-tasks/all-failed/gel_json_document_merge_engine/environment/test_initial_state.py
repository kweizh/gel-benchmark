"""Initial-state checks for the gel_json_document_merge_engine task.

These tests verify the environment that exists BEFORE the executor starts working:
a running-able local Gel 7.1 server, an initialized project directory, the applied
migration, and the seeded settings catalog.
"""

import json
import os
import shutil
import subprocess
import time

import pytest

PROJECT_DIR = "/home/user/settings-engine"
SCHEMA_DIR = os.path.join(PROJECT_DIR, "dbschema")
GEL_TOML = os.path.join(PROJECT_DIR, "gel.toml")
START_SCRIPT = "/usr/local/bin/gel-start.sh"
SOLUTION_PATH = os.path.join(PROJECT_DIR, "settings_engine.py")

READY_TIMEOUT_SEC = 420


def _start_server() -> None:
    assert os.path.isfile(START_SCRIPT), f"{START_SCRIPT} is missing from the image."
    subprocess.run(
        ["/bin/sh", START_SCRIPT],
        check=False,
        capture_output=True,
        text=True,
        timeout=READY_TIMEOUT_SEC,
    )


def _wait_until_ready():
    import gel  # noqa: PLC0415

    deadline = time.time() + READY_TIMEOUT_SEC
    last_error = None
    while time.time() < deadline:
        client = None
        try:
            client = gel.create_client(timeout=30)
            client.query_single("select 1")
            return client
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if client is not None:
                try:
                    client.close()
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(3)
    raise AssertionError(f"Gel server never became ready: {last_error!r}")


@pytest.fixture(scope="session")
def client():
    """Guarantees the local Gel server is up; yields a connected client."""
    _start_server()
    cl = _wait_until_ready()
    try:
        yield cl
    finally:
        try:
            cl.close()
        except Exception:  # noqa: BLE001
            pass


def _query_json(client, query: str, **kwargs):
    return json.loads(client.query_json(query, **kwargs))


def _layer(client, key: str):
    rows = _query_json(
        client,
        "select SettingsLayer { key, tier, doc, active, revision } filter .key = <str>$k",
        k=key,
    )
    assert len(rows) == 1, f"Seeded SettingsLayer {key!r} is missing."
    row = rows[0]
    row["doc"] = json.loads(row["doc"]) if isinstance(row["doc"], str) else row["doc"]
    return row


def test_gel_cli_available():
    assert shutil.which("gel") is not None, "The 'gel' CLI is not available in PATH."


def test_python_gel_client_importable():
    import gel  # noqa: PLC0415

    assert gel is not None, "The Python 'gel' client library is not importable."


def test_python3_available():
    assert shutil.which("python3") is not None, "python3 is not available in PATH."


def test_start_script_exists():
    assert os.path.isfile(START_SCRIPT), f"{START_SCRIPT} does not exist."
    assert os.access(START_SCRIPT, os.X_OK), f"{START_SCRIPT} is not executable."


def test_project_directory_exists():
    assert os.path.isdir(PROJECT_DIR), f"Project directory {PROJECT_DIR} does not exist."


def test_gel_toml_exists():
    assert os.path.isfile(GEL_TOML), f"{GEL_TOML} does not exist."


def test_schema_directory_and_migration_exist():
    assert os.path.isdir(SCHEMA_DIR), f"{SCHEMA_DIR} does not exist."
    schema_files = [f for f in os.listdir(SCHEMA_DIR) if f.endswith(".gel")]
    assert schema_files, f"No .gel schema file found in {SCHEMA_DIR}."
    migrations_dir = os.path.join(SCHEMA_DIR, "migrations")
    assert os.path.isdir(migrations_dir), f"{migrations_dir} does not exist."
    migrations = [f for f in os.listdir(migrations_dir) if f.endswith(".edgeql")]
    assert migrations, f"No migration file found in {migrations_dir}."


def test_solution_entrypoint_not_present_yet():
    assert not os.path.exists(SOLUTION_PATH), (
        f"{SOLUTION_PATH} already exists; the executor is supposed to create it."
    )


def test_server_is_reachable(client):
    assert client.query_single("select 1") == 1, "The Gel server did not answer a trivial query."


def test_schema_types_present(client):
    names = _query_json(
        client,
        """
        select schema::ObjectType { name }
        filter .name in {'default::SettingsLayer', 'default::SettingsRecord'}
        """,
    )
    found = sorted(row["name"] for row in names)
    assert found == ["default::SettingsLayer", "default::SettingsRecord"], (
        f"Expected the seeded object types to exist, found: {found}"
    )


def test_settings_layer_properties(client):
    rows = _query_json(
        client,
        """
        select schema::ObjectType { pointers: { name } }
        filter .name = 'default::SettingsLayer'
        """,
    )
    assert rows, "default::SettingsLayer not found."
    names = {p["name"] for p in rows[0]["pointers"]}
    for expected in ("key", "tier", "doc", "active", "revision"):
        assert expected in names, f"SettingsLayer is missing property {expected!r}: {sorted(names)}"


def test_settings_record_properties_and_link_property(client):
    rows = _query_json(
        client,
        """
        select schema::ObjectType {
            pointers: { name, pointers: { name } }
        }
        filter .name = 'default::SettingsRecord'
        """,
    )
    assert rows, "default::SettingsRecord not found."
    pointers = {p["name"]: p for p in rows[0]["pointers"]}
    for expected in ("slug", "label", "layers"):
        assert expected in pointers, (
            f"SettingsRecord is missing pointer {expected!r}: {sorted(pointers)}"
        )
    link_props = {p["name"] for p in pointers["layers"]["pointers"]}
    assert "precedence" in link_props, (
        f"SettingsRecord.layers is missing the 'precedence' link property: {sorted(link_props)}"
    )


def test_seeded_object_counts(client):
    records = client.query_single("select count(SettingsRecord)")
    layers = client.query_single("select count(SettingsLayer)")
    assert records == 307, f"Expected 307 seeded SettingsRecord objects, found {records}."
    assert layers == 914, f"Expected 914 seeded SettingsLayer objects, found {layers}."


def test_seeded_curated_layers(client):
    base = _layer(client, "base.global")
    assert base["revision"] == 3, f"base.global should start at revision 3, got {base['revision']}."
    assert base["active"] is True, "base.global should be active."
    assert base["doc"]["theme"] == "light", "base.global.doc lost its seeded 'theme' value."
    assert base["doc"][""] == "empty-key-global", "base.global.doc lost its empty-string key."
    assert base["doc"]["limits"]["nested"]["deep"] == {"a": 1, "b": 2}, (
        "base.global.doc lost its deep nested values."
    )

    alice = _layer(client, "user.alice")
    assert alice["doc"]["keep"] is None, "user.alice.doc should contain an explicit null 'keep'."
    assert alice["revision"] == 0, "user.alice should start at revision 0."

    eng = _layer(client, "group.engineering")
    assert eng["doc"]["日本語"] == "group", "group.engineering.doc lost its non-ASCII key."
    assert eng["doc"]["flags"] is None, "group.engineering.doc should contain a null 'flags'."

    disabled = _layer(client, "override.disabled")
    assert disabled["active"] is False, "override.disabled must start inactive."
    assert disabled["revision"] == 41, "override.disabled should start at revision 41."

    empty = _layer(client, "base.empty")
    assert empty["doc"] == {}, "base.empty.doc should be an empty JSON object."

    patch_user = _layer(client, "patch.user")
    assert patch_user["doc"] == {"a": {"b": 2}}, "patch.user.doc has an unexpected seeded value."
    assert patch_user["revision"] == 0, "patch.user should start at revision 0."

    race_user = _layer(client, "race.user")
    assert race_user["doc"] == {}, "race.user.doc should start as an empty JSON object."
    assert race_user["revision"] == 0, "race.user should start at revision 0."


def test_seeded_records_and_chains(client):
    rows = _query_json(
        client,
        "select SettingsRecord { slug, label, layers: { key } } filter .slug = <str>$s",
        s="acme-alice",
    )
    assert len(rows) == 1, "Seeded record 'acme-alice' is missing."
    assert rows[0]["label"] == "Acme / Alice", "acme-alice has an unexpected label."
    keys = sorted(layer["key"] for layer in rows[0]["layers"])
    assert keys == ["base.global", "group.engineering", "override.disabled", "user.alice"], (
        f"acme-alice has an unexpected layer chain: {keys}"
    )

    orphan = _query_json(
        client,
        "select SettingsRecord { slug, layers: { key } } filter .slug = <str>$s",
        s="orphan-record",
    )
    assert len(orphan) == 1, "Seeded record 'orphan-record' is missing."
    assert orphan[0]["layers"] == [], "orphan-record must start with no layers."

    filler = client.query_single(
        "select count((select SettingsRecord filter .slug like 'fill-%'))",
    )
    assert filler == 300, f"Expected 300 seeded filler records, found {filler}."


def test_migration_state_is_in_sync(client):
    assert client is not None
    proc = subprocess.run(
        ["gel", "migration", "status", "--schema-dir", SCHEMA_DIR],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, (
        "gel migration status failed for the seeded project: "
        f"rc={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )


def test_gel_cli_can_query(client):
    assert client is not None
    proc = subprocess.run(
        ["gel", "query", "select count(SettingsLayer)"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, (
        f"gel query failed: rc={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert "914" in proc.stdout, f"Unexpected layer count from the CLI: {proc.stdout!r}"
