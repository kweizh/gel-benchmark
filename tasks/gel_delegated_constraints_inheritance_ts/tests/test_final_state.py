"""Final-state verification for the gel_delegated_constraints_inheritance_ts task.

The suite drives the real local Gel instance (through the `gel` CLI) and the real
CLI the executor was asked to build. Nothing is mocked.

Ordering matters: runs A, B and C are stateful and are executed exactly once by
the session-scoped ``ingest_runs`` fixture, in that order, before any assertion
about their outcome is made.
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
from typing import Any

import pytest

PROJECT_DIR = "/home/user/assetreg"
SRC_DIR = os.path.join(PROJECT_DIR, "src")
MIGRATIONS_DIR = os.path.join(PROJECT_DIR, "dbschema", "migrations")
RUN_SCRIPT = os.path.join(PROJECT_DIR, "run-ingest.sh")
ENSURE_SERVER = "/usr/local/bin/gel-ensure-server.sh"

MANIFEST_A = "/tmp/run_a.json"
MANIFEST_B = "/tmp/run_b.json"
MANIFEST_C = "/tmp/run_c.json"
MANIFEST_EMPTY = "/tmp/run_empty.json"
REPORT_A = "/tmp/report_a.json"
REPORT_B = "/tmp/report_b.json"
REPORT_C = "/tmp/report_c.json"
REPORT_EMPTY = "/tmp/report_empty.json"
REPORT_MISSING = "/tmp/report_missing.json"

CONSTRAINT_ERROR = "ConstraintViolationError"
MISSING_REQUIRED_ERROR = "MissingRequiredError"


# --------------------------------------------------------------------------
# Manifests
# --------------------------------------------------------------------------

RUN_A = {
    "regions": ["EU_WEST", "US_EAST"],
    # Deliberately not in `seq` order: the tool must sort before ingesting.
    "assets": [
        {
            "seq": 3,
            "kind": "storage",
            "code": "ALPHA",
            "serial": "SN-0003",
            "region": "EU_WEST",
            "slot": 1,
            "capacity": 8,
            "reserved": 8,
            "revision": 2,
            "tags": ["COLD"],
            "volume_gb": 500,
        },
        {
            "seq": 1,
            "kind": "server",
            "code": "ALPHA",
            "serial": "SN-0001",
            "region": "EU_WEST",
            "slot": 1,
            "capacity": 10,
            "reserved": 2,
            "revision": 1,
            "tags": ["WEB", "PROD"],
            "hostname": "host-1",
        },
        {
            "seq": 2,
            "kind": "server",
            "code": "BETA",
            "serial": "SN-0002",
            "region": "US_EAST",
            "slot": 1,
            "capacity": 4,
            "reserved": 0,
            "revision": 1,
            "hostname": "host-2",
        },
    ],
}

RUN_B = {
    "regions": ["EU_WEST", "US_EAST"],
    "assets": [
        {
            "seq": 10,
            "kind": "server",
            "code": "GAMMA",
            "serial": "SN-0010",
            "region": "EU_WEST",
            "slot": 10,
            "capacity": 6,
            "reserved": 1,
            "revision": 1,
            "tags": ["WEB"],
            "hostname": "host-10",
        },
        {
            "seq": 11,
            "kind": "server",
            "code": "ALPHA",
            "serial": "SN-0011",
            "region": "US_EAST",
            "slot": 11,
            "capacity": 6,
            "reserved": 1,
            "revision": 1,
            "hostname": "host-11",
        },
        {
            "seq": 12,
            "kind": "storage",
            "code": "DELTA",
            "serial": "SN-0001",
            "region": "US_EAST",
            "slot": 12,
            "capacity": 6,
            "reserved": 1,
            "revision": 1,
            "volume_gb": 100,
        },
        {
            "seq": 13,
            "kind": "server",
            "code": "EPSILON",
            "serial": "SN-0013",
            "region": "EU_WEST",
            "slot": 1,
            "capacity": 6,
            "reserved": 1,
            "revision": 1,
            "hostname": "host-13",
        },
        {
            "seq": 14,
            "kind": "server",
            "code": "ZETA",
            "serial": "SN-0014",
            "region": "EU_WEST",
            "slot": 14,
            "capacity": 3,
            "reserved": 9,
            "revision": 1,
            "hostname": "host-14",
        },
        {
            "seq": 15,
            "kind": "server",
            "code": "ETA",
            "serial": "SN-0015",
            "region": "EU_WEST",
            "slot": 15,
            "capacity": 6,
            "reserved": 1,
            "revision": 0,
            "hostname": "host-15",
        },
        {
            "seq": 16,
            "kind": "server",
            "code": "bad-code",
            "serial": "SN-0016",
            "region": "EU_WEST",
            "slot": 16,
            "capacity": 6,
            "reserved": 1,
            "revision": 1,
            "hostname": "host-16",
        },
        {
            "seq": 17,
            "kind": "server",
            "code": "THETA",
            "serial": "SN-0017",
            "region": "EU_WEST",
            "slot": 17,
            "capacity": 6,
            "reserved": 1,
            "revision": 1,
            "tags": ["WAYTOOLONGTAG"],
            "hostname": "host-17",
        },
        {
            "seq": 18,
            "kind": "server",
            "code": "IOTA",
            "serial": "SN-0018",
            "region": "EU_WEST",
            "slot": 18,
            "capacity": 6,
            "reserved": 1,
            "revision": 1,
        },
        {
            "seq": 19,
            "kind": "storage",
            "code": "ALPHA",
            "serial": "SN-0019",
            "region": "US_EAST",
            "slot": 19,
            "capacity": 6,
            "reserved": 1,
            "revision": 1,
            "volume_gb": 200,
        },
        {
            "seq": 20,
            "kind": "storage",
            "code": "KAPPA",
            "serial": "SN-0020",
            "region": "US_EAST",
            "slot": 20,
            "capacity": 6,
            "reserved": 1,
            "revision": 1,
            "volume_gb": 200,
        },
    ],
}

RUN_C = {
    "regions": ["EU_WEST", "US_EAST", "AP_SOUTH"],
    "assets": [
        {
            "seq": 30,
            "kind": "storage",
            "code": "OMEGA",
            "serial": "SN-0030",
            "region": "AP_SOUTH",
            "slot": 30,
            "capacity": 5,
            "reserved": 5,
            "revision": 1,
            "volume_gb": 10,
        },
        {
            "seq": 31,
            "kind": "server",
            "code": "ALPHA",
            "serial": "SN-0031",
            "region": "AP_SOUTH",
            "slot": 31,
            "capacity": 5,
            "reserved": 1,
            "revision": 1,
            "hostname": "host-31",
        },
        {
            "seq": 32,
            "kind": "storage",
            "code": "PSI",
            "serial": "SN-0010",
            "region": "AP_SOUTH",
            "slot": 32,
            "capacity": 5,
            "reserved": 1,
            "revision": 1,
            "volume_gb": 10,
        },
    ],
}

RUN_EMPTY = {"regions": ["EU_WEST"], "assets": []}

# (seq, status, reason, error_class)
EXPECTED_B = [
    (10, "inserted", None, None),
    (11, "rejected", "CODE_DUPLICATE_IN_KIND", CONSTRAINT_ERROR),
    (12, "rejected", "SERIAL_DUPLICATE_GLOBAL", CONSTRAINT_ERROR),
    (13, "rejected", "SLOT_DUPLICATE_IN_KIND", CONSTRAINT_ERROR),
    (14, "rejected", "CAPACITY_EXCEEDED", CONSTRAINT_ERROR),
    (15, "rejected", "REVISION_TOO_LOW", CONSTRAINT_ERROR),
    (16, "rejected", "TOKEN_INVALID(max=16)", CONSTRAINT_ERROR),
    (17, "rejected", "TOKEN_INVALID(max=12)", CONSTRAINT_ERROR),
    (18, "rejected", "MISSING_REQUIRED", MISSING_REQUIRED_ERROR),
    (19, "rejected", "CODE_DUPLICATE_IN_KIND", CONSTRAINT_ERROR),
    (20, "inserted", None, None),
]

EXPECTED_B_REASON_COUNTS = {
    "CODE_DUPLICATE_IN_KIND": 2,
    "SERIAL_DUPLICATE_GLOBAL": 1,
    "SLOT_DUPLICATE_IN_KIND": 1,
    "CAPACITY_EXCEEDED": 1,
    "REVISION_TOO_LOW": 1,
    "TOKEN_INVALID(max=16)": 1,
    "TOKEN_INVALID(max=12)": 1,
    "MISSING_REQUIRED": 1,
}

EXPECTED_C = [
    (30, "inserted", None, None),
    (31, "rejected", "CODE_DUPLICATE_IN_KIND", CONSTRAINT_ERROR),
    (32, "rejected", "SERIAL_DUPLICATE_GLOBAL", CONSTRAINT_ERROR),
]

REJECTED_SERIALS_B = [
    "SN-0011",
    "SN-0013",
    "SN-0014",
    "SN-0015",
    "SN-0016",
    "SN-0017",
    "SN-0018",
    "SN-0019",
]


# --------------------------------------------------------------------------
# Helpers / fixtures
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def gel_server() -> str:
    """Start the bundled local Gel server (idempotent) and wait for readiness.

    Every test that shells out to the `gel` CLI or to the executor's CLI MUST
    request this fixture, otherwise it can run before the server is listening
    and fail with a spurious `Connection refused`.
    """
    proc = subprocess.run(
        ["bash", ENSURE_SERVER], capture_output=True, text=True, timeout=300
    )
    print(f"[gel-ensure-server] stdout:\n{proc.stdout}")
    print(f"[gel-ensure-server] stderr:\n{proc.stderr}")
    assert proc.returncode == 0, (
        f"Could not start the local Gel server via {ENSURE_SERVER}: {proc.stderr}"
    )
    return ENSURE_SERVER


def gel_query(query: str) -> Any:
    proc = subprocess.run(
        ["gel", "query", "-F", "json", query],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, (
        f"EdgeQL query failed.\nquery: {query}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    return json.loads(proc.stdout)


def gel_query_expect_failure(query: str) -> str:
    proc = subprocess.run(
        ["gel", "query", "-F", "json", query],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode != 0, (
        f"Expected the query to be rejected by the database but it succeeded.\n"
        f"query: {query}\nstdout:\n{proc.stdout}"
    )
    return proc.stdout + proc.stderr


def gel_scalar(query: str) -> Any:
    rows = gel_query(query)
    assert len(rows) == 1, f"Expected exactly one row for {query!r}, got {rows!r}"
    return rows[0]


def write_manifest(path: str, payload: dict) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)


def run_ingest(args: list[str]) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["bash", RUN_SCRIPT, *args],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=600,
    )
    print(f"[run-ingest {' '.join(args)}] rc={proc.returncode}")
    print(f"stdout:\n{proc.stdout}")
    print(f"stderr:\n{proc.stderr}")
    return proc


def last_stdout_line(proc: subprocess.CompletedProcess) -> str:
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert lines, "The CLI printed nothing to stdout."
    return lines[-1].strip()


def load_report(path: str) -> dict:
    assert os.path.isfile(path), f"Report file {path} was not written."
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="session")
def ingest_runs(gel_server: str) -> dict:
    """Execute runs A, B and C once, in order, and collect their artifacts."""
    assert os.path.isfile(RUN_SCRIPT), (
        f"{RUN_SCRIPT} does not exist; the ingest entrypoint was not created."
    )

    for path in (REPORT_A, REPORT_B, REPORT_C):
        if os.path.exists(path):
            os.remove(path)

    write_manifest(MANIFEST_A, RUN_A)
    write_manifest(MANIFEST_B, RUN_B)
    write_manifest(MANIFEST_C, RUN_C)

    proc_a = run_ingest(["--input", MANIFEST_A, "--report", REPORT_A])
    state_after_a = {
        "server": gel_scalar("select count(ServerAsset)"),
        "storage": gel_scalar("select count(StorageAsset)"),
        "assets": gel_scalar("select count(Asset)"),
        "regions": sorted(gel_query("select Region.key")),
    }

    proc_b = run_ingest(["--input", MANIFEST_B, "--report", REPORT_B])
    state_after_b = {
        "assets": gel_scalar("select count(Asset)"),
        "regions": sorted(gel_query("select Region.key")),
        "serials": sorted(gel_query("select Asset.serial")),
    }

    proc_c = run_ingest(["--input", MANIFEST_C, "--report", REPORT_C])
    state_after_c = {
        "assets": gel_scalar("select count(Asset)"),
        "regions": sorted(gel_query("select Region.key")),
    }

    return {
        "proc_a": proc_a,
        "proc_b": proc_b,
        "proc_c": proc_c,
        "report_a": load_report(REPORT_A),
        "report_b": load_report(REPORT_B),
        "report_c": load_report(REPORT_C),
        "state_after_a": state_after_a,
        "state_after_b": state_after_b,
        "state_after_c": state_after_c,
    }


@pytest.fixture(scope="session")
def schema_types(gel_server: str) -> dict[str, dict]:
    rows = gel_query(
        """
        select schema::ObjectType {
            name,
            is_abstract := .abstract,
            base_names := (select .bases.name),
            pointers: {
                name,
                required,
                constraints: {
                    cname := .name,
                    delegated,
                    errmessage,
                    subjectexpr
                }
            },
            constraints: {
                cname := .name,
                delegated,
                errmessage,
                subjectexpr
            }
        }
        filter .name like 'default::%'
        """
    )
    return {row["name"]: row for row in rows}


def pointer_of(type_row: dict, pointer_name: str) -> dict:
    for pointer in type_row["pointers"]:
        if pointer["name"] == pointer_name:
            return pointer
    raise AssertionError(
        f"Object type {type_row['name']} has no pointer named {pointer_name!r}; "
        f"found {[p['name'] for p in type_row['pointers']]}"
    )


def constraints_named(container: dict, constraint_name: str) -> list[dict]:
    return [c for c in container["constraints"] if c["cname"] == constraint_name]


# --------------------------------------------------------------------------
# 1. Project layout, migration history
# --------------------------------------------------------------------------


def test_run_ingest_entrypoint_exists() -> None:
    assert os.path.isfile(RUN_SCRIPT), (
        f"The ingest entrypoint {RUN_SCRIPT} does not exist."
    )


def test_typescript_sources_exist_and_use_gel_client() -> None:
    sources = glob.glob(os.path.join(SRC_DIR, "**", "*.ts"), recursive=True)
    assert sources, (
        f"No TypeScript source file was found under {SRC_DIR}; the ingest tool must "
        "be written in TypeScript there."
    )
    blob = ""
    for path in sources:
        with open(path, encoding="utf-8") as handle:
            blob += handle.read()
    assert '"gel"' in blob or "'gel'" in blob, (
        "None of the TypeScript sources reference the `gel` npm client package; the "
        "tool must drive the database through that client."
    )


def test_migration_files_exist() -> None:
    migrations = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql")))
    assert migrations, (
        f"No migration file found in {MIGRATIONS_DIR}; the schema must be applied "
        "through Gel's migration system."
    )
    print(f"migrations: {migrations}")


def test_migration_status_is_in_sync(gel_server: str) -> None:
    proc = subprocess.run(
        ["gel", "migration", "status"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, (
        "`gel migration status` reports the database is not fully migrated.\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


# --------------------------------------------------------------------------
# 2. Schema introspection: types and inheritance
# --------------------------------------------------------------------------


def test_all_required_object_types_exist(schema_types: dict[str, dict]) -> None:
    for name in (
        "default::Region",
        "default::Revisioned",
        "default::Taggable",
        "default::Asset",
        "default::ServerAsset",
        "default::StorageAsset",
        "default::Operator",
    ):
        assert name in schema_types, (
            f"Object type {name} is missing from module `default`; found "
            f"{sorted(schema_types)}"
        )


def test_abstractness_of_types(schema_types: dict[str, dict]) -> None:
    for name in ("default::Revisioned", "default::Taggable", "default::Asset"):
        assert schema_types[name]["is_abstract"] is True, (
            f"{name} must be declared as an abstract type."
        )
    for name in (
        "default::Region",
        "default::ServerAsset",
        "default::StorageAsset",
        "default::Operator",
    ):
        assert schema_types[name]["is_abstract"] is False, (
            f"{name} must be a concrete (non-abstract) type."
        )


def test_asset_uses_multiple_inheritance(schema_types: dict[str, dict]) -> None:
    bases = sorted(schema_types["default::Asset"]["base_names"])
    assert bases == ["default::Revisioned", "default::Taggable"], (
        "default::Asset must extend exactly default::Revisioned and default::Taggable "
        f"(multiple inheritance); got {bases}"
    )


def test_concrete_asset_subtypes(schema_types: dict[str, dict]) -> None:
    for name in ("default::ServerAsset", "default::StorageAsset"):
        assert schema_types[name]["base_names"] == ["default::Asset"], (
            f"{name} must extend default::Asset; got {schema_types[name]['base_names']}"
        )
    hostname = pointer_of(schema_types["default::ServerAsset"], "hostname")
    assert hostname["required"] is True, (
        "default::ServerAsset.hostname must be a required property."
    )
    volume = pointer_of(schema_types["default::StorageAsset"], "volume_gb")
    assert volume["required"] is True, (
        "default::StorageAsset.volume_gb must be a required property."
    )


def test_asset_declares_required_pointers(schema_types: dict[str, dict]) -> None:
    asset = schema_types["default::Asset"]
    for pointer_name in ("code", "serial", "region", "slot", "capacity", "reserved"):
        pointer = pointer_of(asset, pointer_name)
        assert pointer["required"] is True, (
            f"default::Asset.{pointer_name} must be required."
        )
    # inherited from the two mixins
    pointer_of(asset, "revision")
    pointer_of(asset, "tags")


# --------------------------------------------------------------------------
# 3. Schema introspection: schema::Constraint.delegated
# --------------------------------------------------------------------------


def test_code_exclusive_constraint_is_delegated(schema_types: dict[str, dict]) -> None:
    code = pointer_of(schema_types["default::Asset"], "code")
    exclusive = constraints_named(code, "std::exclusive")
    assert len(exclusive) == 1, (
        "default::Asset.code must carry exactly one exclusive constraint; got "
        f"{code['constraints']}"
    )
    assert exclusive[0]["delegated"] is True, (
        "The exclusive constraint on default::Asset.code must be delegated so that "
        "uniqueness is enforced per concrete subtype."
    )
    assert exclusive[0]["errmessage"] == "CODE_DUPLICATE_IN_KIND", (
        "The exclusive constraint on default::Asset.code must report the message "
        f"CODE_DUPLICATE_IN_KIND; got {exclusive[0]['errmessage']!r}"
    )


def test_serial_exclusive_constraint_is_not_delegated(
    schema_types: dict[str, dict],
) -> None:
    serial = pointer_of(schema_types["default::Asset"], "serial")
    exclusive = constraints_named(serial, "std::exclusive")
    assert len(exclusive) == 1, (
        "default::Asset.serial must carry exactly one exclusive constraint; got "
        f"{serial['constraints']}"
    )
    assert exclusive[0]["delegated"] is False, (
        "The exclusive constraint on default::Asset.serial must NOT be delegated: "
        "serials must be unique across the whole Asset hierarchy."
    )
    assert exclusive[0]["errmessage"] == "SERIAL_DUPLICATE_GLOBAL", (
        "The exclusive constraint on default::Asset.serial must report the message "
        f"SERIAL_DUPLICATE_GLOBAL; got {exclusive[0]['errmessage']!r}"
    )


def test_composite_object_constraint_is_delegated(
    schema_types: dict[str, dict],
) -> None:
    asset = schema_types["default::Asset"]
    candidates = [
        c
        for c in constraints_named(asset, "std::exclusive")
        if c["subjectexpr"]
        and "region" in c["subjectexpr"]
        and "slot" in c["subjectexpr"]
    ]
    assert len(candidates) == 1, (
        "default::Asset must declare exactly one object-level exclusive constraint "
        f"over (region, slot); got {asset['constraints']}"
    )
    assert candidates[0]["delegated"] is True, (
        "The (region, slot) exclusive constraint on default::Asset must be delegated."
    )
    assert candidates[0]["errmessage"] == "SLOT_DUPLICATE_IN_KIND", (
        "The (region, slot) exclusive constraint must report SLOT_DUPLICATE_IN_KIND; "
        f"got {candidates[0]['errmessage']!r}"
    )


def test_capacity_expression_constraint(schema_types: dict[str, dict]) -> None:
    asset = schema_types["default::Asset"]
    expressions = constraints_named(asset, "std::expression")
    assert len(expressions) == 1, (
        "default::Asset must declare exactly one object-level expression constraint; "
        f"got {asset['constraints']}"
    )
    constraint = expressions[0]
    assert constraint["delegated"] is False, (
        "The reserved<=capacity expression constraint must not be delegated."
    )
    assert constraint["errmessage"] == "CAPACITY_EXCEEDED", (
        "The reserved<=capacity constraint must report CAPACITY_EXCEEDED; got "
        f"{constraint['errmessage']!r}"
    )
    subjectexpr = constraint["subjectexpr"] or ""
    assert "reserved" in subjectexpr and "capacity" in subjectexpr, (
        "The expression constraint must span both `reserved` and `capacity`; got "
        f"{subjectexpr!r}"
    )


def test_inherited_constraints_are_not_delegated_on_subtypes(
    schema_types: dict[str, dict],
) -> None:
    for name in ("default::ServerAsset", "default::StorageAsset"):
        subtype = schema_types[name]
        code = pointer_of(subtype, "code")
        exclusive = constraints_named(code, "std::exclusive")
        assert exclusive, f"{name}.code lost its inherited exclusive constraint."
        assert all(c["delegated"] is False for c in exclusive), (
            f"The exclusive constraint inherited by {name}.code must be resolved into "
            "a non-delegated, per-subtype constraint."
        )
        composite = [
            c
            for c in constraints_named(subtype, "std::exclusive")
            if c["subjectexpr"]
            and "region" in c["subjectexpr"]
            and "slot" in c["subjectexpr"]
        ]
        assert composite, f"{name} lost the inherited (region, slot) constraint."
        assert all(c["delegated"] is False for c in composite), (
            f"The (region, slot) constraint inherited by {name} must be resolved into "
            "a non-delegated, per-subtype constraint."
        )


def test_no_unexpected_delegated_constraints(schema_types: dict[str, dict]) -> None:
    delegated_pointers = sorted(
        {
            f"{name.split('::')[-1]}.{pointer['name']}"
            for name, row in schema_types.items()
            for pointer in row["pointers"]
            for constraint in pointer["constraints"]
            if constraint["delegated"] is True
        }
    )
    delegated_objects = sorted(
        {
            name.split("::")[-1]
            for name, row in schema_types.items()
            if any(c["delegated"] is True for c in row["constraints"])
        }
    )
    assert delegated_pointers == ["Asset.code"], (
        "`Asset.code` must be the only delegated pointer constraint in module "
        f"`default`; got {delegated_pointers}"
    )
    assert delegated_objects == ["Asset"], (
        "`Asset` must be the only type with a delegated object-level constraint; got "
        f"{delegated_objects}"
    )


# --------------------------------------------------------------------------
# 4. Schema introspection: parameterized abstract constraint
# --------------------------------------------------------------------------


def test_single_parameterized_abstract_constraint(gel_server: str) -> None:
    rows = gel_query(
        """
        select schema::Constraint {
            name,
            errmessage,
            parameters := (
                select .params { pname := .name, type_name := .type.name }
            )
        }
        filter .abstract and .name like 'default::%'
        """
    )
    assert len(rows) == 1, (
        "Module `default` must declare exactly one user-defined abstract constraint; "
        f"got {[r['name'] for r in rows]}"
    )
    params = [p for p in rows[0]["parameters"] if p["pname"] != "__subject__"]
    assert len(params) == 1, (
        "The abstract token constraint must take exactly one declared parameter; got "
        f"{params}"
    )
    assert params[0]["type_name"] == "std::int64", (
        "The abstract token constraint's parameter must be of type std::int64; got "
        f"{params[0]['type_name']!r}"
    )


def test_abstract_constraint_reused_at_both_sites(
    gel_server: str, schema_types: dict[str, dict]
) -> None:
    rows = gel_query(
        "select schema::Constraint { name } filter .abstract "
        "and .name like 'default::%'"
    )
    abstract_name = rows[0]["name"]

    code = pointer_of(schema_types["default::Asset"], "code")
    assert constraints_named(code, abstract_name), (
        f"default::Asset.code must use the abstract constraint {abstract_name}; got "
        f"{[c['cname'] for c in code['constraints']]}"
    )
    tags = pointer_of(schema_types["default::Taggable"], "tags")
    assert constraints_named(tags, abstract_name), (
        f"default::Taggable.tags must use the abstract constraint {abstract_name}; got "
        f"{[c['cname'] for c in tags['constraints']]}"
    )


# --------------------------------------------------------------------------
# 5. Schema introspection: link property constraint
# --------------------------------------------------------------------------


def test_operator_link_property_and_constraint(gel_server: str) -> None:
    # NOTE: the `[is schema::Link]` intersection is mandatory. Nesting `pointers`
    # directly under a generic `schema::Pointer` set raises InvalidReferenceError.
    rows = gel_query(
        """
        select schema::ObjectType {
            name,
            pointers: {
                name,
                link_props := ([is schema::Link].pointers.name),
                constraints: {
                    cname := .name,
                    errmessage,
                    subjectexpr
                }
            }
        }
        filter .name = 'default::Operator'
        """
    )
    assert len(rows) == 1, "default::Operator was not found in the schema."
    operator = rows[0]

    name_pointer = pointer_of(operator, "name")
    name_exclusive = constraints_named(name_pointer, "std::exclusive")
    assert len(name_exclusive) == 1, (
        f"default::Operator.name must be unique; got {name_pointer['constraints']}"
    )
    assert name_exclusive[0]["errmessage"] == "OPERATOR_NAME_DUPLICATE", (
        "default::Operator.name uniqueness must report OPERATOR_NAME_DUPLICATE; got "
        f"{name_exclusive[0]['errmessage']!r}"
    )

    crew = pointer_of(operator, "crew")
    assert "role" in crew["link_props"], (
        "The `crew` link must declare a link property named `role`; got "
        f"{crew['link_props']}"
    )
    crew_exclusive = constraints_named(crew, "std::exclusive")
    assert len(crew_exclusive) == 1, (
        f"The `crew` link must carry exactly one exclusive constraint; got "
        f"{crew['constraints']}"
    )
    assert crew_exclusive[0]["errmessage"] == "ROLE_DUPLICATE_FOR_OPERATOR", (
        "The `crew` link constraint must report ROLE_DUPLICATE_FOR_OPERATOR; got "
        f"{crew_exclusive[0]['errmessage']!r}"
    )
    subjectexpr = crew_exclusive[0]["subjectexpr"] or ""
    assert "@role" in subjectexpr, (
        "The `crew` constraint must be scoped to the `role` link property; got "
        f"{subjectexpr!r}"
    )


# --------------------------------------------------------------------------
# 6. Run A - happy path
# --------------------------------------------------------------------------


def test_run_a_exit_code_and_stdout(ingest_runs: dict) -> None:
    proc = ingest_runs["proc_a"]
    assert proc.returncode == 0, (
        f"Run A rejected nothing, so the exit code must be 0; got {proc.returncode}"
    )
    assert last_stdout_line(proc) == "SUMMARY inserted=3 rejected=0", (
        f"Unexpected final stdout line for run A: {last_stdout_line(proc)!r}"
    )


def test_run_a_report_totals(ingest_runs: dict) -> None:
    report = ingest_runs["report_a"]
    assert sorted(report.keys()) == sorted(
        ["total", "inserted", "rejected", "results", "reason_counts", "schema"]
    ), f"Unexpected top-level report keys: {sorted(report.keys())}"
    assert report["total"] == 3, f"Run A total must be 3; got {report['total']}"
    assert report["inserted"] == 3, (
        f"All three run A records must be inserted; got {report['inserted']}"
    )
    assert report["rejected"] == 0, (
        f"Run A must reject nothing; got {report['rejected']}"
    )
    assert report["reason_counts"] == {}, (
        f"Run A must produce an empty reason_counts; got {report['reason_counts']}"
    )


def test_run_a_results_ordering_and_shape(ingest_runs: dict) -> None:
    results = ingest_runs["report_a"]["results"]
    assert [r["seq"] for r in results] == [1, 2, 3], (
        "Run A results must be ordered by ascending seq (the manifest lists them out "
        f"of order); got {[r['seq'] for r in results]}"
    )
    for entry in results:
        assert sorted(entry.keys()) == sorted(
            ["seq", "kind", "serial", "status", "reason", "error_class"]
        ), f"Unexpected result entry keys: {sorted(entry.keys())}"
        assert entry["status"] == "inserted", (
            f"Run A record {entry['seq']} should have been inserted; got {entry}"
        )
        assert entry["reason"] is None and entry["error_class"] is None, (
            f"Inserted records must carry null reason/error_class; got {entry}"
        )
    assert [r["serial"] for r in results] == ["SN-0001", "SN-0002", "SN-0003"], (
        f"Unexpected serials in run A results: {[r['serial'] for r in results]}"
    )
    assert [r["kind"] for r in results] == ["server", "server", "storage"], (
        f"Unexpected kinds in run A results: {[r['kind'] for r in results]}"
    )


def test_run_a_schema_section_from_introspection(ingest_runs: dict) -> None:
    schema = ingest_runs["report_a"]["schema"]
    assert sorted(schema.keys()) == sorted(
        [
            "abstract_constraints",
            "delegated_pointer_constraints",
            "delegated_object_constraints",
        ]
    ), f"Unexpected report `schema` keys: {sorted(schema.keys())}"
    assert schema["delegated_pointer_constraints"] == ["Asset.code"], (
        "The report's delegated pointer constraints must be exactly ['Asset.code']; "
        f"got {schema['delegated_pointer_constraints']}"
    )
    assert schema["delegated_object_constraints"] == ["Asset"], (
        "The report's delegated object constraints must be exactly ['Asset']; got "
        f"{schema['delegated_object_constraints']}"
    )
    abstract = schema["abstract_constraints"]
    assert isinstance(abstract, list) and len(abstract) == 1, (
        f"Exactly one abstract constraint is expected in module default; got {abstract}"
    )
    assert abstract[0].startswith("default::"), (
        f"The abstract constraint name must be module-qualified; got {abstract[0]!r}"
    )


def test_run_a_database_state(ingest_runs: dict) -> None:
    state = ingest_runs["state_after_a"]
    assert state["server"] == 2, (
        f"Run A must leave exactly 2 ServerAsset objects; got {state['server']}"
    )
    assert state["storage"] == 1, (
        f"Run A must leave exactly 1 StorageAsset object; got {state['storage']}"
    )
    assert state["assets"] == 3, (
        f"Run A must leave exactly 3 Asset objects; got {state['assets']}"
    )
    assert state["regions"] == ["EU_WEST", "US_EAST"], (
        f"Run A must create exactly the declared regions; got {state['regions']}"
    )


# --------------------------------------------------------------------------
# 7. Run B - every rejection reason + partial-failure semantics
# --------------------------------------------------------------------------


def test_run_b_exit_code_and_stdout(ingest_runs: dict) -> None:
    proc = ingest_runs["proc_b"]
    assert proc.returncode == 2, (
        f"Run B rejects records, so the exit code must be 2; got {proc.returncode}"
    )
    assert last_stdout_line(proc) == "SUMMARY inserted=2 rejected=9", (
        f"Unexpected final stdout line for run B: {last_stdout_line(proc)!r}"
    )


def test_run_b_report_totals(ingest_runs: dict) -> None:
    report = ingest_runs["report_b"]
    assert report["total"] == 11, f"Run B total must be 11; got {report['total']}"
    assert report["inserted"] == 2, (
        f"Run B must insert exactly 2 records; got {report['inserted']}"
    )
    assert report["rejected"] == 9, (
        f"Run B must reject exactly 9 records; got {report['rejected']}"
    )
    assert report["inserted"] + report["rejected"] == report["total"], (
        "inserted + rejected must equal total."
    )


def test_run_b_classification_table(ingest_runs: dict) -> None:
    results = ingest_runs["report_b"]["results"]
    assert [r["seq"] for r in results] == [e[0] for e in EXPECTED_B], (
        f"Run B results must be ordered by ascending seq; got {[r['seq'] for r in results]}"
    )
    actual = [
        (r["seq"], r["status"], r["reason"], r["error_class"]) for r in results
    ]
    for expected, got in zip(EXPECTED_B, actual):
        assert got == expected, (
            f"Wrong classification for record seq={expected[0]}.\n"
            f"expected (seq, status, reason, error_class) = {expected}\n"
            f"got                                          = {got}"
        )


def test_run_b_reason_counts(ingest_runs: dict) -> None:
    counts = ingest_runs["report_b"]["reason_counts"]
    assert counts == EXPECTED_B_REASON_COUNTS, (
        f"Unexpected reason_counts for run B.\nexpected: {EXPECTED_B_REASON_COUNTS}\n"
        f"got:      {counts}"
    )


def test_run_b_left_no_partial_writes(ingest_runs: dict) -> None:
    serials = ingest_runs["state_after_b"]["serials"]
    for serial in REJECTED_SERIALS_B:
        assert serial not in serials, (
            f"Serial {serial} belongs to a rejected record and must not exist in the "
            f"database; found serials: {serials}"
        )
    for serial in ("SN-0010", "SN-0020"):
        assert serial in serials, (
            f"Serial {serial} was reported as inserted but is missing from the "
            f"database; found serials: {serials}"
        )


def test_run_b_database_counts(ingest_runs: dict) -> None:
    state = ingest_runs["state_after_b"]
    assert state["assets"] == 5, (
        f"After run B the database must hold exactly 5 Asset objects; got {state['assets']}"
    )
    assert state["regions"] == ["EU_WEST", "US_EAST"], (
        "Re-declaring existing region keys must not create duplicates; got "
        f"{state['regions']}"
    )


# --------------------------------------------------------------------------
# 8. Run C - re-run against a populated database
# --------------------------------------------------------------------------


def test_run_c_exit_code_and_stdout(ingest_runs: dict) -> None:
    proc = ingest_runs["proc_c"]
    assert proc.returncode == 2, (
        f"Run C rejects records, so the exit code must be 2; got {proc.returncode}"
    )
    assert last_stdout_line(proc) == "SUMMARY inserted=1 rejected=2", (
        f"Unexpected final stdout line for run C: {last_stdout_line(proc)!r}"
    )


def test_run_c_report(ingest_runs: dict) -> None:
    report = ingest_runs["report_c"]
    assert report["total"] == 3, f"Run C total must be 3; got {report['total']}"
    assert report["inserted"] == 1, (
        f"Run C must insert exactly 1 record; got {report['inserted']}"
    )
    assert report["rejected"] == 2, (
        f"Run C must reject exactly 2 records; got {report['rejected']}"
    )
    actual = [
        (r["seq"], r["status"], r["reason"], r["error_class"])
        for r in report["results"]
    ]
    assert actual == EXPECTED_C, (
        f"Unexpected run C classifications.\nexpected: {EXPECTED_C}\ngot:      {actual}"
    )
    assert report["reason_counts"] == {
        "CODE_DUPLICATE_IN_KIND": 1,
        "SERIAL_DUPLICATE_GLOBAL": 1,
    }, f"Unexpected run C reason_counts: {report['reason_counts']}"


def test_run_c_database_state(ingest_runs: dict) -> None:
    state = ingest_runs["state_after_c"]
    assert state["regions"] == ["AP_SOUTH", "EU_WEST", "US_EAST"], (
        "Run C must create only the missing region and leave existing ones alone; got "
        f"{state['regions']}"
    )
    assert state["assets"] == 6, (
        f"After run C the database must hold exactly 6 Asset objects; got {state['assets']}"
    )


# --------------------------------------------------------------------------
# 9. Operator rules, verified directly against the database
# --------------------------------------------------------------------------


def test_operator_rules_enforced_by_database(ingest_runs: dict) -> None:
    gel_query("delete Operator")
    try:
        gel_query(
            "insert Operator { name := 'op-alpha', crew := ("
            "select Asset { @role := 'PRIMARY' } filter .serial = 'SN-0001') }"
        )

        output = gel_query_expect_failure(
            "update Operator filter .name = 'op-alpha' set { crew += ("
            "select Asset { @role := 'PRIMARY' } filter .serial = 'SN-0002') }"
        )
        assert "ROLE_DUPLICATE_FOR_OPERATOR" in output, (
            "Linking a second asset with the same @role to the same Operator must be "
            f"refused with ROLE_DUPLICATE_FOR_OPERATOR; got:\n{output}"
        )

        output = gel_query_expect_failure("insert Operator { name := 'op-alpha' }")
        assert "OPERATOR_NAME_DUPLICATE" in output, (
            "Duplicate Operator names must be refused with OPERATOR_NAME_DUPLICATE; "
            f"got:\n{output}"
        )

        gel_query(
            "insert Operator { name := 'op-beta', crew := ("
            "select Asset { @role := 'PRIMARY' } filter .serial = 'SN-0002') }"
        )
    finally:
        gel_query("delete Operator")

    assert gel_scalar("select count(Asset)") == 6, (
        "The Operator checks must not have changed the number of Asset objects."
    )
    assert gel_scalar("select count(Region)") == 3, (
        "The Operator checks must not have changed the number of Region objects."
    )


# --------------------------------------------------------------------------
# 10. Argument handling and boundary cases (kept last: they touch the CLI again)
# --------------------------------------------------------------------------


def test_missing_report_flag_exits_1(ingest_runs: dict) -> None:
    proc = run_ingest(["--input", MANIFEST_A])
    assert proc.returncode == 1, (
        f"A missing --report flag must exit with code 1; got {proc.returncode}"
    )
    assert proc.stderr.strip(), "A usage error must produce a diagnostic on stderr."


def test_unreadable_input_exits_1_without_report(ingest_runs: dict) -> None:
    if os.path.exists(REPORT_MISSING):
        os.remove(REPORT_MISSING)
    proc = run_ingest(
        ["--input", "/tmp/does_not_exist.json", "--report", REPORT_MISSING]
    )
    assert proc.returncode == 1, (
        f"An unreadable manifest must exit with code 1; got {proc.returncode}"
    )
    assert proc.stderr.strip(), "An input error must produce a diagnostic on stderr."
    assert not os.path.exists(REPORT_MISSING), (
        f"No report file may be written when the manifest cannot be read, but "
        f"{REPORT_MISSING} exists."
    )


def test_empty_manifest_and_reversed_flags(ingest_runs: dict) -> None:
    if os.path.exists(REPORT_EMPTY):
        os.remove(REPORT_EMPTY)
    write_manifest(MANIFEST_EMPTY, RUN_EMPTY)

    proc = run_ingest(["--report", REPORT_EMPTY, "--input", MANIFEST_EMPTY])
    assert proc.returncode == 0, (
        "An empty manifest rejects nothing, so the exit code must be 0 (and the flags "
        f"must be accepted in either order); got {proc.returncode}"
    )
    assert last_stdout_line(proc) == "SUMMARY inserted=0 rejected=0", (
        f"Unexpected final stdout line for the empty manifest: {last_stdout_line(proc)!r}"
    )

    report = load_report(REPORT_EMPTY)
    assert report["total"] == 0, f"Empty manifest total must be 0; got {report['total']}"
    assert report["inserted"] == 0 and report["rejected"] == 0, (
        f"Empty manifest must insert and reject nothing; got {report}"
    )
    assert report["results"] == [], f"Expected no results; got {report['results']}"
    assert report["reason_counts"] == {}, (
        f"Expected empty reason_counts; got {report['reason_counts']}"
    )

    assert gel_scalar("select count(Asset)") == 6, (
        "The empty-manifest run must not change the number of Asset objects."
    )
    assert gel_scalar("select count(Region)") == 3, (
        "The empty-manifest run must not change the number of Region objects."
    )
