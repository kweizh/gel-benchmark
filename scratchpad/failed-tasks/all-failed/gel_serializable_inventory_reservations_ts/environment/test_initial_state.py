import json
import os
import shutil
import subprocess

import pytest

PROJECT_DIR = "/home/user/inventory"
START_GEL = "/usr/local/bin/start-gel"


@pytest.fixture(scope="session")
def gel_server() -> None:
    """Make sure the local Gel instance is up before any DB/CLI check runs."""
    assert os.path.isfile(START_GEL), f"{START_GEL} helper script is missing."
    proc = subprocess.run([START_GEL], capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, (
        "Failed to start the local Gel server.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


def test_gel_cli_available() -> None:
    assert shutil.which("gel") is not None, "The `gel` CLI was not found in PATH."


def test_node_toolchain_available() -> None:
    assert shutil.which("node") is not None, "`node` was not found in PATH."
    assert shutil.which("npx") is not None, "`npx` was not found in PATH."


def test_python_test_runner_available() -> None:
    assert shutil.which("pytest") is not None, "`pytest` was not found in PATH."


def test_project_directory_exists() -> None:
    assert os.path.isdir(PROJECT_DIR), f"Project directory {PROJECT_DIR} does not exist."


def test_gel_toml_exists() -> None:
    path = os.path.join(PROJECT_DIR, "gel.toml")
    assert os.path.isfile(path), f"Expected the Gel project manifest {path} to exist."


def test_dbschema_directory_exists() -> None:
    path = os.path.join(PROJECT_DIR, "dbschema")
    assert os.path.isdir(path), f"Expected the schema directory {path} to exist."


def test_package_json_exists() -> None:
    path = os.path.join(PROJECT_DIR, "package.json")
    assert os.path.isfile(path), f"Expected {path} to exist."
    with open(path) as fh:
        data = json.load(fh)
    deps = {}
    deps.update(data.get("dependencies") or {})
    deps.update(data.get("devDependencies") or {})
    assert "gel" in deps, "package.json does not declare the `gel` client dependency."
    assert "tsx" in deps, "package.json does not declare the `tsx` dependency."


def test_tsconfig_exists() -> None:
    path = os.path.join(PROJECT_DIR, "tsconfig.json")
    assert os.path.isfile(path), f"Expected {path} to exist."


def test_src_directory_exists() -> None:
    path = os.path.join(PROJECT_DIR, "src")
    assert os.path.isdir(path), f"Expected the source directory {path} to exist."


def test_node_modules_are_vendored() -> None:
    for pkg in ("gel", "tsx"):
        path = os.path.join(PROJECT_DIR, "node_modules", pkg)
        assert os.path.isdir(path), (
            f"Expected the npm package `{pkg}` to be pre-installed at {path}; "
            "the container has no network access."
        )


def test_tsx_runner_works_offline() -> None:
    script = os.path.join(PROJECT_DIR, "src", "__initial_state_probe.ts")
    with open(script, "w") as fh:
        fh.write('const answer: number = 21 * 2;\nconsole.log(JSON.stringify({ answer }));\n')
    try:
        proc = subprocess.run(
            ["npx", "tsx", "src/__initial_state_probe.ts"],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=300,
        )
    finally:
        if os.path.exists(script):
            os.remove(script)
    assert proc.returncode == 0, (
        "`npx tsx` could not run a TypeScript file offline.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert '"answer":42' in proc.stdout.replace(" ", ""), (
        f"Unexpected output from the tsx probe: {proc.stdout!r}"
    )


def test_gel_client_library_is_importable(gel_server: None) -> None:
    script = os.path.join(PROJECT_DIR, "src", "__initial_state_client_probe.ts")
    with open(script, "w") as fh:
        fh.write(
            'import { createClient } from "gel";\n'
            "async function main(): Promise<void> {\n"
            "  const client = createClient();\n"
            "  await client.ensureConnected();\n"
            "  const value = await client.queryRequiredSingle<number>('select 1 + 1');\n"
            "  console.log(JSON.stringify({ value }));\n"
            "  await client.close();\n"
            "}\n"
            "main().catch((err) => { console.error(err); process.exit(1); });\n"
        )
    try:
        proc = subprocess.run(
            ["npx", "tsx", "src/__initial_state_client_probe.ts"],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=300,
        )
    finally:
        if os.path.exists(script):
            os.remove(script)
    assert proc.returncode == 0, (
        "The vendored `gel` client could not connect to the local instance.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert '"value":2' in proc.stdout.replace(" ", ""), (
        f"Unexpected output from the gel client probe: {proc.stdout!r}"
    )


def test_gel_server_answers_queries(gel_server: None) -> None:
    proc = subprocess.run(
        ["gel", "query", "select 1 + 1"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, (
        "`gel query` failed against the local instance.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "2" in proc.stdout, f"Unexpected `gel query` output: {proc.stdout!r}"


def test_service_types_are_not_predefined(gel_server: None) -> None:
    proc = subprocess.run(
        [
            "gel",
            "query",
            "--output-format=json",
            "select schema::ObjectType { name } "
            "filter .name like 'default::%' and not .is_from_alias",
        ],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, (
        "Could not introspect the schema of the local instance.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    names = {row["name"] for row in json.loads(proc.stdout)}
    for expected_missing in (
        "default::StockItem",
        "default::Reservation",
        "default::ReservationLine",
        "default::LedgerEntry",
    ):
        assert expected_missing not in names, (
            f"{expected_missing} already exists in the initial database; "
            "the executor is supposed to create it."
        )
