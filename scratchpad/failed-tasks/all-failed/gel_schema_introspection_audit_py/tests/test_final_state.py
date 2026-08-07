"""Final-state verification for the gel_schema_introspection_audit_py task.

The suite exercises the real `schema_audit` CLI/API against the real local Gel
server and cross-checks every reported value against independent introspection
queries issued by the test itself (nothing about the baked schema inventory is
hardcoded).

IMPORTANT ordering note: the very last test in this file
(`test_schema_agnostic_after_new_migration`) MUTATES the schema of the branch by
creating and applying a new migration, and restores the original schema file +
history afterwards in a `finally` block. It is deliberately the last test in the
module so that no other test can observe the intermediate schema. Do not move it
and do not run this file with a randomising / parallel test order plugin.
"""

import glob
import json
import os
import re
import subprocess
import textwrap

import pytest

PROJECT_DIR = "/home/user/gel-audit"
SCHEMA_DIR = os.path.join(PROJECT_DIR, "dbschema")
SCHEMA_FILE = os.path.join(SCHEMA_DIR, "default.gel")
MIGRATIONS_DIR = os.path.join(SCHEMA_DIR, "migrations")
START_SCRIPT = "/usr/local/bin/gel-start.sh"

AUDIT_MAIN = "/tmp/audit-main.json"

RULE_SEVERITY = {
    "type-missing-exclusive": "error",
    "type-name-not-pascal-case": "warning",
    "pointer-name-not-snake-case": "warning",
    "multi-link-required": "warning",
    "link-property-not-required": "error",
    "policy-without-tenant-id": "error",
    "deprecated-type": "warning",
    "index-duplicates-exclusive": "info",
    "global-name-not-snake-case": "warning",
}

SNAKE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
PASCAL_RE = re.compile(r"^[A-Z][A-Za-z0-9]*$")

TOP_KEYS = {
    "audit_version",
    "branch",
    "migrations",
    "object_types",
    "globals",
    "aliases",
    "functions",
    "ignored_rules",
    "violations",
    "summary",
}
TYPE_KEYS = {
    "name",
    "abstract",
    "bases",
    "annotations",
    "pointers",
    "constraints",
    "indexes",
    "access_policies",
    "triggers",
}
POINTER_KEYS = {
    "name",
    "kind",
    "target",
    "required",
    "cardinality",
    "readonly",
    "computed",
    "constraints",
    "link_properties",
}
LINK_PROP_KEYS = {"name", "target", "required", "constraints"}
CONSTRAINT_KEYS = {"name", "subjectexpr"}
INDEX_KEYS = {"expr"}
POLICY_KEYS = {"name", "action", "access_kinds", "expr"}
TRIGGER_KEYS = {"name", "timing", "scope", "kinds"}
GLOBAL_KEYS = {"name", "target", "required", "cardinality", "computed"}
FUNCTION_KEYS = {"name", "return_type", "volatility", "param_types"}
VIOLATION_KEYS = {"rule", "severity", "target", "message"}
SUMMARY_KEYS = {"error", "warning", "info", "total"}

TYPES_QUERY = """
select schema::ObjectType {
    name,
    abstract,
    bases: { name },
    annotations: { name, @value },
    pointers: {
        name,
        ptype := .__type__.name,
        cardinality,
        required,
        readonly,
        expr,
        tname := .target.name,
        cnames := (select .constraints.name),
        [is schema::Link].pointers: {
            name,
            required,
            tname := .target.name,
            cnames := (select .constraints.name)
        }
    },
    constraints: { name, subjectexpr },
    indexes: { expr },
    access_policies: { name, action, access_kinds, expr },
    triggers: { name, timing, scope, kinds }
}
filter not .builtin and not .internal
   and not .from_alias and not .compound_type
"""

GLOBALS_QUERY = """
select schema::Global {
    name,
    required,
    cardinality,
    expr,
    tname := .target.name
}
filter not .builtin and not .internal
"""

ALIASES_QUERY = """
select schema::Alias { name }
filter not .builtin and not .internal
"""

FUNCTIONS_QUERY = """
select schema::Function {
    name,
    volatility,
    rtname := .return_type.name,
    params: { name, idx := @index, tname := .type.name }
}
filter not .builtin and not .internal
"""

MIGRATIONS_QUERY = """
select schema::Migration { name, parents: { name } }
"""


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def run_module(args, extra_env=None, timeout=240):
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["python3", "-m", "schema_audit", *args],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def run_cli(args, timeout=300):
    return subprocess.run(
        args,
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def short_name(name):
    return name.rsplit("::", 1)[-1]


def strip_ws(text):
    return re.sub(r"\s+", "", text)


def as_bool(value):
    return bool(value) if value is not None else False


def migration_chain(rows):
    """Order migrations from the root (no parents) along the parent chain."""
    parents = {row["name"]: [p["name"] for p in (row.get("parents") or [])] for row in rows}
    children = {}
    roots = []
    for name, plist in parents.items():
        if not plist:
            roots.append(name)
        for parent in plist:
            children.setdefault(parent, []).append(name)
    assert len(roots) == 1, f"Expected exactly one root migration, got {roots}"
    chain = [roots[0]]
    while True:
        nxt = children.get(chain[-1], [])
        if not nxt:
            break
        assert len(nxt) == 1, f"Migration history is not linear at {chain[-1]}: {nxt}"
        chain.append(nxt[0])
    assert len(chain) == len(parents), (
        f"Resolved chain {chain} does not cover all migrations {sorted(parents)}"
    )
    return chain


def introspect_types(db):
    rows = json.loads(db.query_json(TYPES_QUERY))
    result = {}
    for row in rows:
        pointers = {}
        for pointer in row.get("pointers") or []:
            if pointer["name"] in ("id", "__type__"):
                continue
            link_props = {}
            for nested in pointer.get("pointers") or []:
                if nested["name"] in ("source", "target"):
                    continue
                link_props[nested["name"]] = {
                    "name": nested["name"],
                    "target": nested.get("tname"),
                    "required": as_bool(nested.get("required")),
                    "constraints": sorted(nested.get("cnames") or []),
                }
            pointers[pointer["name"]] = {
                "name": pointer["name"],
                "kind": "link" if pointer["ptype"] == "schema::Link" else "property",
                "target": pointer.get("tname"),
                "required": as_bool(pointer.get("required")),
                "cardinality": pointer.get("cardinality"),
                "readonly": as_bool(pointer.get("readonly")),
                "computed": bool(pointer.get("expr")),
                "constraints": sorted(pointer.get("cnames") or []),
                "link_properties": [link_props[k] for k in sorted(link_props)],
            }
        result[row["name"]] = {
            "name": row["name"],
            "abstract": as_bool(row.get("abstract")),
            "bases": sorted(base["name"] for base in row.get("bases") or []),
            "annotations": sorted(
                (
                    {"name": ann["name"], "value": ann.get("@value")}
                    for ann in row.get("annotations") or []
                ),
                key=lambda item: item["name"],
            ),
            "pointers": [pointers[k] for k in sorted(pointers)],
            "constraints": sorted(
                (
                    {"name": con["name"], "subjectexpr": con.get("subjectexpr")}
                    for con in row.get("constraints") or []
                ),
                key=lambda item: (item["name"], item["subjectexpr"] or ""),
            ),
            "indexes": sorted(
                ({"expr": idx["expr"]} for idx in row.get("indexes") or []),
                key=lambda item: item["expr"],
            ),
            "access_policies": sorted(
                (
                    {
                        "name": pol["name"],
                        "action": pol["action"],
                        "access_kinds": sorted(pol.get("access_kinds") or []),
                        "expr": pol.get("expr"),
                    }
                    for pol in row.get("access_policies") or []
                ),
                key=lambda item: item["name"],
            ),
            "triggers": sorted(
                (
                    {
                        "name": trg["name"],
                        "timing": trg["timing"],
                        "scope": trg["scope"],
                        "kinds": sorted(trg.get("kinds") or []),
                    }
                    for trg in row.get("triggers") or []
                ),
                key=lambda item: item["name"],
            ),
        }
    return result


def recompute_violations(doc, ignored=()):
    """Re-evaluate every documented lint rule from the audit document alone."""
    ignored = set(ignored)
    found = set()

    def add(rule, target):
        if rule not in ignored:
            found.add((rule, RULE_SEVERITY[rule], target))

    for otype in doc["object_types"]:
        name = otype["name"]
        pointers = otype["pointers"]
        type_constraints = otype["constraints"]
        if not otype["abstract"]:
            has_exclusive = any(
                con["name"] == "std::exclusive" for con in type_constraints
            ) or any("std::exclusive" in ptr["constraints"] for ptr in pointers)
            if not has_exclusive:
                add("type-missing-exclusive", name)
        if not PASCAL_RE.match(short_name(name)):
            add("type-name-not-pascal-case", name)
        for ptr in pointers:
            if not SNAKE_RE.match(ptr["name"]):
                add("pointer-name-not-snake-case", f"{name}.{ptr['name']}")
            if (
                ptr["kind"] == "link"
                and ptr["cardinality"] == "Many"
                and ptr["required"]
            ):
                add("multi-link-required", f"{name}.{ptr['name']}")
            for lprop in ptr["link_properties"]:
                if not lprop["required"]:
                    add(
                        "link-property-not-required",
                        f"{name}.{ptr['name']}@{lprop['name']}",
                    )
        if otype["access_policies"] and not any(
            ptr["name"] == "tenant_id" for ptr in pointers
        ):
            add("policy-without-tenant-id", name)
        if any(ann["name"] == "default::deprecated" for ann in otype["annotations"]):
            add("deprecated-type", name)
        duplicated = {
            strip_ws(con["subjectexpr"] or "")
            for con in type_constraints
            if con["name"] == "std::exclusive" and con["subjectexpr"]
        }
        duplicated |= {
            strip_ws("." + ptr["name"])
            for ptr in pointers
            if "std::exclusive" in ptr["constraints"]
        }
        for index in otype["indexes"]:
            if strip_ws(index["expr"] or "") in duplicated:
                add("index-duplicates-exclusive", f"{name}:{index['expr']}")

    for glob_entry in doc["globals"]:
        if not SNAKE_RE.match(short_name(glob_entry["name"])):
            add("global-name-not-snake-case", glob_entry["name"])

    return sorted(found, key=lambda item: (item[0], item[2]))


def documented_violations(doc):
    return [(v["rule"], v["severity"], v["target"]) for v in doc["violations"]]


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def gel_server():
    """Start the local Gel server (idempotent) and wait for readiness.

    Every test that runs the CLI, the audit tool or a client query MUST request
    this fixture, otherwise it can race the server startup.
    """
    proc = subprocess.run(
        [START_SCRIPT], capture_output=True, text=True, timeout=600
    )
    print(f"[gel-start.sh stdout]\n{proc.stdout}")
    print(f"[gel-start.sh stderr]\n{proc.stderr}")
    assert proc.returncode == 0, (
        f"{START_SCRIPT} failed (exit {proc.returncode}).\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    return True


@pytest.fixture(scope="session")
def db(gel_server):
    import gel

    client = gel.create_client()
    try:
        yield client
    finally:
        try:
            client.close()
        except Exception:  # pragma: no cover - best effort cleanup
            pass


@pytest.fixture(scope="session")
def audit_run(gel_server):
    """Run the plain audit once and share the result with the whole session."""
    if os.path.exists(AUDIT_MAIN):
        os.remove(AUDIT_MAIN)
    proc = run_module(["audit", "--out", AUDIT_MAIN])
    assert os.path.isfile(AUDIT_MAIN), (
        "Expected 'audit --out' to create the audit document at "
        f"{AUDIT_MAIN}.\nexit={proc.returncode}\nstdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )
    raw = open(AUDIT_MAIN, "rb").read()
    try:
        doc = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # pragma: no cover
        pytest.fail(f"The audit document is not valid UTF-8 JSON: {exc}")
    return {
        "doc": doc,
        "raw": raw,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "returncode": proc.returncode,
    }


@pytest.fixture(scope="session")
def doc(audit_run):
    return audit_run["doc"]


@pytest.fixture(scope="session")
def introspected(db):
    return introspect_types(db)


# --------------------------------------------------------------------------- #
# 1. rule catalog
# --------------------------------------------------------------------------- #
def test_rules_catalog(gel_server):
    proc = run_module(["rules"])
    assert proc.returncode == 0, (
        f"'rules' must exit 0, got {proc.returncode}.\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert isinstance(payload, list), f"'rules' must print a JSON array, got {type(payload)}"
    for entry in payload:
        assert set(entry) == {"id", "severity"}, (
            f"Each rule entry must have exactly the keys id/severity, got {sorted(entry)}"
        )
    ids = [entry["id"] for entry in payload]
    assert ids == sorted(ids), f"The rule catalog must be sorted by id, got {ids}"
    assert set(ids) == set(RULE_SEVERITY), (
        f"Unexpected rule ids.\nexpected: {sorted(RULE_SEVERITY)}\nactual:   {sorted(ids)}"
    )
    for entry in payload:
        assert entry["severity"] == RULE_SEVERITY[entry["id"]], (
            f"Rule {entry['id']} must have severity {RULE_SEVERITY[entry['id']]}, "
            f"got {entry['severity']}"
        )


# --------------------------------------------------------------------------- #
# 2. document shape
# --------------------------------------------------------------------------- #
def test_audit_document_shape(doc, db):
    assert set(doc) == TOP_KEYS, (
        f"Top-level keys must be exactly {sorted(TOP_KEYS)}, got {sorted(doc)}"
    )
    assert doc["audit_version"] == 1, f"audit_version must be 1, got {doc['audit_version']!r}"
    branch = json.loads(db.query_single_json("select sys::get_current_branch()"))
    assert doc["branch"] == branch, (
        f"branch must be the audited branch {branch!r}, got {doc['branch']!r}"
    )
    assert doc["ignored_rules"] == [], (
        f"A plain audit run must report ignored_rules == [], got {doc['ignored_rules']!r}"
    )
    assert isinstance(doc["migrations"], list) and all(
        isinstance(name, str) for name in doc["migrations"]
    ), "migrations must be a list of strings"
    assert isinstance(doc["aliases"], list) and all(
        isinstance(name, str) for name in doc["aliases"]
    ), "aliases must be a list of strings"
    assert doc["aliases"] == sorted(doc["aliases"]), "aliases must be sorted"

    names = [otype["name"] for otype in doc["object_types"]]
    assert names == sorted(names), f"object_types must be sorted by name, got {names}"

    for otype in doc["object_types"]:
        assert set(otype) == TYPE_KEYS, (
            f"Object type {otype.get('name')!r} keys must be exactly "
            f"{sorted(TYPE_KEYS)}, got {sorted(otype)}"
        )
        assert isinstance(otype["abstract"], bool), (
            f"{otype['name']}: abstract must be a bool"
        )
        assert otype["bases"] == sorted(otype["bases"]), (
            f"{otype['name']}: bases must be sorted"
        )
        assert [a["name"] for a in otype["annotations"]] == sorted(
            a["name"] for a in otype["annotations"]
        ), f"{otype['name']}: annotations must be sorted by name"
        for ann in otype["annotations"]:
            assert set(ann) == {"name", "value"}, (
                f"{otype['name']}: annotation keys must be name/value, got {sorted(ann)}"
            )
        ptr_names = [p["name"] for p in otype["pointers"]]
        assert ptr_names == sorted(ptr_names), (
            f"{otype['name']}: pointers must be sorted by name, got {ptr_names}"
        )
        assert "id" not in ptr_names and "__type__" not in ptr_names, (
            f"{otype['name']}: implicit pointers id/__type__ must not be reported"
        )
        for ptr in otype["pointers"]:
            label = f"{otype['name']}.{ptr.get('name')}"
            assert set(ptr) == POINTER_KEYS, (
                f"{label}: pointer keys must be exactly {sorted(POINTER_KEYS)}, "
                f"got {sorted(ptr)}"
            )
            assert ptr["kind"] in ("property", "link"), (
                f"{label}: kind must be 'property' or 'link', got {ptr['kind']!r}"
            )
            assert ptr["cardinality"] in ("One", "Many"), (
                f"{label}: cardinality must be One/Many, got {ptr['cardinality']!r}"
            )
            for flag in ("required", "readonly", "computed"):
                assert isinstance(ptr[flag], bool), f"{label}: {flag} must be a bool"
            assert ptr["constraints"] == sorted(ptr["constraints"]), (
                f"{label}: constraints must be sorted"
            )
            if ptr["kind"] == "property":
                assert ptr["link_properties"] == [], (
                    f"{label}: a property must report link_properties == []"
                )
            lp_names = [lp["name"] for lp in ptr["link_properties"]]
            assert lp_names == sorted(lp_names), (
                f"{label}: link_properties must be sorted by name"
            )
            assert "source" not in lp_names and "target" not in lp_names, (
                f"{label}: implicit source/target link properties must not be reported"
            )
            for lprop in ptr["link_properties"]:
                assert set(lprop) == LINK_PROP_KEYS, (
                    f"{label}@{lprop.get('name')}: keys must be exactly "
                    f"{sorted(LINK_PROP_KEYS)}, got {sorted(lprop)}"
                )
                assert isinstance(lprop["required"], bool), (
                    f"{label}@{lprop['name']}: required must be a bool"
                )
                assert lprop["constraints"] == sorted(lprop["constraints"]), (
                    f"{label}@{lprop['name']}: constraints must be sorted"
                )
        for con in otype["constraints"]:
            assert set(con) == CONSTRAINT_KEYS, (
                f"{otype['name']}: type constraint keys must be exactly "
                f"{sorted(CONSTRAINT_KEYS)}, got {sorted(con)}"
            )
        assert otype["constraints"] == sorted(
            otype["constraints"], key=lambda c: (c["name"], c["subjectexpr"] or "")
        ), f"{otype['name']}: type-level constraints must be sorted"
        for index in otype["indexes"]:
            assert set(index) == INDEX_KEYS, (
                f"{otype['name']}: index keys must be exactly {sorted(INDEX_KEYS)}, "
                f"got {sorted(index)}"
            )
        assert [i["expr"] for i in otype["indexes"]] == sorted(
            i["expr"] for i in otype["indexes"]
        ), f"{otype['name']}: indexes must be sorted by expr"
        for policy in otype["access_policies"]:
            assert set(policy) == POLICY_KEYS, (
                f"{otype['name']}: access policy keys must be exactly "
                f"{sorted(POLICY_KEYS)}, got {sorted(policy)}"
            )
            assert policy["access_kinds"] == sorted(policy["access_kinds"]), (
                f"{otype['name']}: access_kinds must be sorted"
            )
        assert [p["name"] for p in otype["access_policies"]] == sorted(
            p["name"] for p in otype["access_policies"]
        ), f"{otype['name']}: access_policies must be sorted by name"
        for trigger in otype["triggers"]:
            assert set(trigger) == TRIGGER_KEYS, (
                f"{otype['name']}: trigger keys must be exactly "
                f"{sorted(TRIGGER_KEYS)}, got {sorted(trigger)}"
            )
            assert trigger["kinds"] == sorted(trigger["kinds"]), (
                f"{otype['name']}: trigger kinds must be sorted"
            )
        assert [t["name"] for t in otype["triggers"]] == sorted(
            t["name"] for t in otype["triggers"]
        ), f"{otype['name']}: triggers must be sorted by name"

    for entry in doc["globals"]:
        assert set(entry) == GLOBAL_KEYS, (
            f"global {entry.get('name')!r}: keys must be exactly {sorted(GLOBAL_KEYS)}, "
            f"got {sorted(entry)}"
        )
        assert entry["cardinality"] in ("One", "Many"), (
            f"global {entry['name']}: cardinality must be One/Many"
        )
    assert [g["name"] for g in doc["globals"]] == sorted(
        g["name"] for g in doc["globals"]
    ), "globals must be sorted by name"

    for fn in doc["functions"]:
        assert set(fn) == FUNCTION_KEYS, (
            f"function {fn.get('name')!r}: keys must be exactly "
            f"{sorted(FUNCTION_KEYS)}, got {sorted(fn)}"
        )
        assert isinstance(fn["param_types"], list), (
            f"function {fn['name']}: param_types must be a list"
        )
    assert [(f["name"], ",".join(f["param_types"])) for f in doc["functions"]] == sorted(
        (f["name"], ",".join(f["param_types"])) for f in doc["functions"]
    ), "functions must be sorted by name then joined param_types"

    for violation in doc["violations"]:
        assert set(violation) == VIOLATION_KEYS, (
            f"violation keys must be exactly {sorted(VIOLATION_KEYS)}, "
            f"got {sorted(violation)}"
        )
        assert violation["severity"] in ("error", "warning", "info"), (
            f"unexpected severity {violation['severity']!r}"
        )
        assert isinstance(violation["message"], str) and violation["message"].strip(), (
            f"violation {violation['rule']}/{violation['target']} needs a non-empty message"
        )
    assert set(doc["summary"]) == SUMMARY_KEYS, (
        f"summary keys must be exactly {sorted(SUMMARY_KEYS)}, got {sorted(doc['summary'])}"
    )
    for key in SUMMARY_KEYS:
        assert isinstance(doc["summary"][key], int), f"summary.{key} must be an int"


# --------------------------------------------------------------------------- #
# 3. inventory
# --------------------------------------------------------------------------- #
def test_object_type_inventory_matches_introspection(doc, introspected, db):
    expected = set(introspected)
    actual = {otype["name"] for otype in doc["object_types"]}
    assert actual == expected, (
        "Reported object types do not match the audited types resolved by "
        f"independent introspection.\nmissing: {sorted(expected - actual)}\n"
        f"unexpected: {sorted(actual - expected)}"
    )
    assert not any(name.startswith(("std::", "schema::", "sys::", "cfg::")) for name in actual), (
        f"Standard-library types must not be audited: {sorted(actual)}"
    )
    alias_types = {
        row["name"]
        for row in json.loads(
            db.query_json("select schema::ObjectType { name } filter .from_alias")
        )
    }
    assert not (actual & alias_types), (
        f"Alias-generated types must not be audited: {sorted(actual & alias_types)}"
    )
    abstract_reported = {o["name"] for o in doc["object_types"] if o["abstract"]}
    abstract_expected = {n for n, v in introspected.items() if v["abstract"]}
    assert abstract_reported == abstract_expected, (
        f"Abstract types must be audited too.\nexpected: {sorted(abstract_expected)}\n"
        f"actual: {sorted(abstract_reported)}"
    )
    for otype in doc["object_types"]:
        assert otype["bases"] == introspected[otype["name"]]["bases"], (
            f"{otype['name']}: bases mismatch. expected "
            f"{introspected[otype['name']]['bases']}, got {otype['bases']}"
        )


def test_pointer_details_match_introspection(doc, introspected):
    for otype in doc["object_types"]:
        expected = introspected[otype["name"]]["pointers"]
        actual = otype["pointers"]
        assert [p["name"] for p in actual] == [p["name"] for p in expected], (
            f"{otype['name']}: pointer names mismatch. expected "
            f"{[p['name'] for p in expected]}, got {[p['name'] for p in actual]}"
        )
        for exp, act in zip(expected, actual):
            for key in ("kind", "target", "required", "cardinality", "readonly", "computed", "constraints"):
                assert act[key] == exp[key], (
                    f"{otype['name']}.{exp['name']}: {key} mismatch. "
                    f"expected {exp[key]!r}, got {act[key]!r}"
                )


def test_inherited_pointers_are_reported(doc, introspected, db):
    reported = {otype["name"]: otype for otype in doc["object_types"]}
    rows = json.loads(
        db.query_json(
            "select schema::ObjectType { name, ancestors: { name } } "
            "filter not .builtin and not .internal "
            "and not .from_alias and not .compound_type"
        )
    )
    abstract_names = {name for name, value in introspected.items() if value["abstract"]}
    assert abstract_names, "The baked schema is expected to declare abstract types"
    checked = 0
    for row in rows:
        ancestors = {a["name"] for a in row.get("ancestors") or []} & abstract_names
        for ancestor in ancestors:
            inherited = {p["name"] for p in introspected[ancestor]["pointers"]}
            got = {p["name"] for p in reported[row["name"]]["pointers"]}
            assert inherited <= got, (
                f"{row['name']} must report the pointers inherited from {ancestor}: "
                f"missing {sorted(inherited - got)}"
            )
            checked += 1
    assert checked > 0, (
        "Expected at least one concrete type inheriting from an abstract type"
    )


def test_link_properties_match_introspection(doc, introspected):
    expected_all = {}
    for name, value in introspected.items():
        for ptr in value["pointers"]:
            for lprop in ptr["link_properties"]:
                expected_all[f"{name}.{ptr['name']}@{lprop['name']}"] = lprop
    assert expected_all, (
        "The baked schema is expected to declare at least one link property"
    )
    actual_all = {}
    for otype in doc["object_types"]:
        for ptr in otype["pointers"]:
            for lprop in ptr["link_properties"]:
                actual_all[f"{otype['name']}.{ptr['name']}@{lprop['name']}"] = lprop
    assert set(actual_all) == set(expected_all), (
        "Reported link properties do not match independent introspection.\n"
        f"missing: {sorted(set(expected_all) - set(actual_all))}\n"
        f"unexpected: {sorted(set(actual_all) - set(expected_all))}"
    )
    for key, exp in expected_all.items():
        assert actual_all[key] == exp, (
            f"{key}: link property mismatch. expected {exp}, got {actual_all[key]}"
        )
    assert any(lp["required"] for lp in expected_all.values()), (
        "The baked schema is expected to contain a required link property"
    )
    assert any(not lp["required"] for lp in expected_all.values()), (
        "The baked schema is expected to contain an optional link property"
    )


def test_constraints_and_indexes_match_introspection(doc, introspected):
    for otype in doc["object_types"]:
        expected = introspected[otype["name"]]
        assert otype["constraints"] == expected["constraints"], (
            f"{otype['name']}: type-level constraints mismatch. "
            f"expected {expected['constraints']}, got {otype['constraints']}"
        )
        assert otype["indexes"] == expected["indexes"], (
            f"{otype['name']}: indexes mismatch. expected {expected['indexes']}, "
            f"got {otype['indexes']}"
        )
    assert any(otype["indexes"] for otype in doc["object_types"]), (
        "The baked schema is expected to declare at least one index"
    )
    assert any(otype["constraints"] for otype in doc["object_types"]), (
        "The baked schema is expected to declare at least one type-level constraint"
    )


def test_policies_triggers_annotations_match_introspection(doc, introspected):
    policies = 0
    triggers = 0
    annotations = 0
    for otype in doc["object_types"]:
        expected = introspected[otype["name"]]
        assert otype["access_policies"] == expected["access_policies"], (
            f"{otype['name']}: access policies mismatch. "
            f"expected {expected['access_policies']}, got {otype['access_policies']}"
        )
        assert otype["triggers"] == expected["triggers"], (
            f"{otype['name']}: triggers mismatch. expected {expected['triggers']}, "
            f"got {otype['triggers']}"
        )
        assert otype["annotations"] == expected["annotations"], (
            f"{otype['name']}: annotations mismatch. expected {expected['annotations']}, "
            f"got {otype['annotations']}"
        )
        policies += len(otype["access_policies"])
        triggers += len(otype["triggers"])
        annotations += len(otype["annotations"])
    assert policies >= 1, "The baked schema is expected to declare access policies"
    assert triggers >= 1, "The baked schema is expected to declare a trigger"
    assert annotations >= 1, "The baked schema is expected to declare an annotation"


def test_globals_aliases_functions_and_migrations_match(doc, db):
    global_rows = json.loads(db.query_json(GLOBALS_QUERY))
    expected_globals = sorted(
        (
            {
                "name": row["name"],
                "target": row.get("tname"),
                "required": as_bool(row.get("required")),
                "cardinality": row.get("cardinality"),
                "computed": bool(row.get("expr")),
            }
            for row in global_rows
        ),
        key=lambda item: item["name"],
    )
    assert doc["globals"] == expected_globals, (
        f"globals mismatch.\nexpected: {expected_globals}\nactual:   {doc['globals']}"
    )

    expected_aliases = sorted(
        row["name"] for row in json.loads(db.query_json(ALIASES_QUERY))
    )
    assert expected_aliases, "The baked schema is expected to declare an alias"
    assert doc["aliases"] == expected_aliases, (
        f"aliases mismatch.\nexpected: {expected_aliases}\nactual:   {doc['aliases']}"
    )

    expected_functions = []
    for row in json.loads(db.query_json(FUNCTIONS_QUERY)):
        params = sorted(
            row.get("params") or [],
            key=lambda p: (p.get("idx") is None, p.get("idx") or 0),
        )
        expected_functions.append(
            {
                "name": row["name"],
                "return_type": row.get("rtname"),
                "volatility": row.get("volatility"),
                "param_types": [p.get("tname") for p in params],
            }
        )
    expected_functions.sort(key=lambda f: (f["name"], ",".join(f["param_types"])))
    assert expected_functions, (
        "The baked schema is expected to declare user-defined functions"
    )
    assert any(fn["param_types"] for fn in expected_functions), (
        "The baked schema is expected to declare a function with parameters"
    )
    assert doc["functions"] == expected_functions, (
        f"functions mismatch.\nexpected: {expected_functions}\nactual:   {doc['functions']}"
    )

    expected_chain = migration_chain(json.loads(db.query_json(MIGRATIONS_QUERY)))
    assert doc["migrations"] == expected_chain, (
        f"migrations mismatch.\nexpected: {expected_chain}\nactual:   {doc['migrations']}"
    )
    branch_info = json.loads(
        db.query_single_json(
            "select sys::Branch { name, last_migration } "
            "filter .name = sys::get_current_branch()"
        )
    )
    if branch_info and branch_info.get("last_migration"):
        assert doc["migrations"][-1] == branch_info["last_migration"], (
            f"The last reported migration must be {branch_info['last_migration']}, "
            f"got {doc['migrations'][-1]}"
        )


# --------------------------------------------------------------------------- #
# 4. rule engine
# --------------------------------------------------------------------------- #
def test_rule_engine_matches_recomputation(doc):
    expected = recompute_violations(doc)
    actual = documented_violations(doc)
    assert actual == expected, (
        "The reported violations do not match the rule definitions recomputed "
        f"from the document.\nmissing: {sorted(set(expected) - set(actual))}\n"
        f"unexpected: {sorted(set(actual) - set(expected))}\n"
        f"ordering expected: {expected}\nordering actual: {actual}"
    )
    counts = {"error": 0, "warning": 0, "info": 0}
    for _, severity, _ in actual:
        counts[severity] += 1
    assert doc["summary"] == {**counts, "total": len(actual)}, (
        f"summary must be {{**{counts}, 'total': {len(actual)}}}, got {doc['summary']}"
    )
    for severity in ("error", "warning", "info"):
        assert counts[severity] > 0, (
            f"The baked schema is expected to produce at least one {severity} violation, "
            f"got {counts}"
        )
    assert len(set(actual)) == len(actual), (
        f"Each (rule, target) pair may appear only once, got {actual}"
    )


def test_expected_baked_violations_are_detected(doc):
    reported = {(rule, target) for rule, _, target in documented_violations(doc)}
    expected_pairs = [
        ("type-missing-exclusive", "default::ChangeLog"),
        ("type-missing-exclusive", "default::Invoice"),
        ("type-missing-exclusive", "default::Note"),
        ("type-name-not-pascal-case", "default::audit_entry"),
        ("pointer-name-not-snake-case", "default::Account.lastSyncedAt"),
        ("pointer-name-not-snake-case", "default::Tag.taggedProjects"),
        ("multi-link-required", "default::Team.maintainers"),
        ("link-property-not-required", "default::Team.members@joined_at"),
        ("policy-without-tenant-id", "default::Note"),
        ("deprecated-type", "default::LegacyWidget"),
        ("global-name-not-snake-case", "default::activeRegion"),
        ("index-duplicates-exclusive", "default::Project:.code"),
    ]
    missing = [pair for pair in expected_pairs if pair not in reported]
    assert not missing, (
        f"The audit missed these violations of the baked schema: {missing}\n"
        f"reported: {sorted(reported)}"
    )
    compliant = "default::Tenant"
    offending = [
        (rule, target)
        for rule, _, target in documented_violations(doc)
        if target == compliant or target.startswith(compliant + ".") or target.startswith(compliant + ":")
    ]
    assert not offending, (
        f"{compliant} is fully compliant and must not be reported: {offending}"
    )


# --------------------------------------------------------------------------- #
# 5. public API parity
# --------------------------------------------------------------------------- #
def test_public_api_build_audit_matches_cli(gel_server, doc):
    out_path = "/tmp/audit-api.json"
    if os.path.exists(out_path):
        os.remove(out_path)
    script = textwrap.dedent(
        """
        import asyncio
        import inspect
        import json
        import sys

        import gel
        import schema_audit

        if not inspect.iscoroutinefunction(schema_audit.build_audit):
            print("BUILD_AUDIT_NOT_ASYNC", file=sys.stderr)
            raise SystemExit(3)


        async def run():
            client = gel.create_async_client()
            try:
                return await schema_audit.build_audit(client)
            finally:
                try:
                    await client.aclose()
                except Exception:
                    pass


        document = asyncio.run(run())
        with open(sys.argv[1], "w", encoding="utf-8") as handle:
            json.dump(document, handle)
        """
    )
    proc = subprocess.run(
        ["python3", "-c", script, out_path],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert "BUILD_AUDIT_NOT_ASYNC" not in proc.stderr, (
        "schema_audit.build_audit must be an async (coroutine) function."
    )
    assert proc.returncode == 0, (
        "Awaiting schema_audit.build_audit(client) failed.\n"
        f"exit={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    api_doc = json.load(open(out_path, encoding="utf-8"))
    assert api_doc == doc, (
        "The document returned by build_audit() must equal the document written "
        "by the CLI."
    )


# --------------------------------------------------------------------------- #
# 6. exit-code table and rule filtering
# --------------------------------------------------------------------------- #
def test_plain_run_exit_code_is_error(audit_run):
    assert audit_run["returncode"] == 30, (
        "The baked schema contains error-severity violations, so the audit must "
        f"exit 30, got {audit_run['returncode']}.\nstderr:\n{audit_run['stderr']}"
    )


def test_ignoring_rules_changes_worst_severity(gel_server, doc):
    error_rules = sorted(r for r, s in RULE_SEVERITY.items() if s == "error")
    warning_rules = sorted(r for r, s in RULE_SEVERITY.items() if s == "warning")
    all_rules = sorted(RULE_SEVERITY)

    cases = [
        (error_rules, 20),
        (error_rules + warning_rules, 10),
        (all_rules, 0),
    ]
    for index, (ignored, expected_code) in enumerate(cases):
        out_path = f"/tmp/audit-ignore-{index}.json"
        if os.path.exists(out_path):
            os.remove(out_path)
        args = ["audit", "--out", out_path]
        for rule in ignored:
            args += ["--ignore-rule", rule]
        proc = run_module(args)
        assert proc.returncode == expected_code, (
            f"Ignoring {ignored} must yield exit code {expected_code}, got "
            f"{proc.returncode}.\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
        payload = json.load(open(out_path, encoding="utf-8"))
        assert payload["ignored_rules"] == sorted(set(ignored)), (
            f"ignored_rules must be {sorted(set(ignored))}, got {payload['ignored_rules']}"
        )
        assert not [v for v in payload["violations"] if v["rule"] in ignored], (
            "Ignored rules must not produce violations: "
            f"{[v['rule'] for v in payload['violations'] if v['rule'] in ignored]}"
        )
        assert documented_violations(payload) == recompute_violations(
            payload, ignored=ignored
        ), f"Violations with ignored rules {ignored} do not match the recomputation"
        if expected_code == 0:
            assert payload["violations"] == [], (
                f"Ignoring every rule must leave no violations, got {payload['violations']}"
            )
            assert payload["summary"] == {
                "error": 0,
                "warning": 0,
                "info": 0,
                "total": 0,
            }, f"Ignoring every rule must zero the summary, got {payload['summary']}"

    duplicated = ["audit", "--out", "/tmp/audit-ignore-dup.json"]
    first_rule = all_rules[0]
    duplicated += ["--ignore-rule", first_rule, "--ignore-rule", first_rule]
    proc = run_module(duplicated)
    payload = json.load(open("/tmp/audit-ignore-dup.json", encoding="utf-8"))
    assert payload["ignored_rules"] == [first_rule], (
        f"Repeated --ignore-rule values must be de-duplicated, got {payload['ignored_rules']}"
    )


# --------------------------------------------------------------------------- #
# 7. stdout contract
# --------------------------------------------------------------------------- #
def test_stdout_summary_format(audit_run):
    doc = audit_run["doc"]
    lines = audit_run["stdout"].splitlines()
    violations = doc["violations"]
    pointer_count = sum(len(otype["pointers"]) for otype in doc["object_types"])
    expected = [
        f"object_types={len(doc['object_types'])}",
        f"pointers={pointer_count}",
        f"violations={doc['summary']['total']}",
        f"error={doc['summary']['error']}",
        f"warning={doc['summary']['warning']}",
        f"info={doc['summary']['info']}",
    ]
    expected += [
        f"{v['severity']} {v['rule']} {v['target']}" for v in violations
    ]
    expected.append("exit=30")
    assert lines == expected, (
        "Unexpected stdout summary.\nexpected:\n"
        + "\n".join(expected)
        + "\nactual:\n"
        + "\n".join(lines)
    )


def test_quiet_suppresses_stdout(gel_server, audit_run):
    out_path = "/tmp/audit-quiet.json"
    if os.path.exists(out_path):
        os.remove(out_path)
    proc = run_module(["audit", "--out", out_path, "--quiet"])
    assert proc.stdout == "", f"--quiet must print nothing on stdout, got {proc.stdout!r}"
    assert proc.returncode == audit_run["returncode"], (
        f"--quiet must not change the exit code, got {proc.returncode}"
    )
    payload = json.load(open(out_path, encoding="utf-8"))
    assert payload == audit_run["doc"], "--quiet must not change the audit document"


def test_output_is_deterministic(gel_server, audit_run):
    out_path = "/tmp/audit-repeat.json"
    if os.path.exists(out_path):
        os.remove(out_path)
    proc = run_module(["audit", "--out", out_path])
    assert proc.returncode == audit_run["returncode"], (
        f"Re-running the audit must be stable, got exit {proc.returncode}"
    )
    assert open(out_path, "rb").read() == audit_run["raw"], (
        "Two audit runs against an unchanged schema must produce byte-identical files"
    )


# --------------------------------------------------------------------------- #
# 8. argument handling and failure modes
# --------------------------------------------------------------------------- #
def test_usage_errors_exit_64(gel_server):
    out_path = "/tmp/audit-usage.json"
    cases = [
        [],
        ["definitely-not-a-command"],
        ["audit"],
        ["audit", "--out", out_path, "--totally-unknown-flag"],
        ["audit", "--out", out_path, "--ignore-rule", "not-a-real-rule"],
    ]
    for args in cases:
        if os.path.exists(out_path):
            os.remove(out_path)
        proc = run_module(args)
        assert proc.returncode == 64, (
            f"Usage error {args} must exit 64, got {proc.returncode}.\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
        assert proc.stdout == "", (
            f"Usage error {args} must keep stdout empty, got {proc.stdout!r}"
        )
        assert not os.path.exists(out_path), (
            f"Usage error {args} must not write the output document"
        )


def test_unreachable_database_exits_65(gel_server):
    out_path = "/tmp/audit-unreachable.json"
    if os.path.exists(out_path):
        os.remove(out_path)
    proc = run_module(
        ["audit", "--out", out_path],
        extra_env={
            "GEL_PORT": "5699",
            "GEL_WAIT_UNTIL_AVAILABLE": "1s",
            "GEL_CONNECT_TIMEOUT": "1s",
        },
    )
    assert proc.returncode == 65, (
        "An unreachable database must produce exit code 65, got "
        f"{proc.returncode}.\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert proc.stdout == "", f"Failed audits must keep stdout empty, got {proc.stdout!r}"
    assert not os.path.exists(out_path), "A failed audit must not write the output document"


# --------------------------------------------------------------------------- #
# 9. schema agnosticism -- MUST STAY LAST (mutates the database schema)
# --------------------------------------------------------------------------- #
def test_schema_agnostic_after_new_migration(gel_server, doc):
    """Apply a new migration and re-run the audit.

    This mutates the branch schema, so it is the last test in the module. The
    original schema file and a restoring migration are re-applied in `finally`.
    """
    original = open(SCHEMA_FILE, encoding="utf-8").read()
    marker_lines = [
        line for line in original.splitlines() if "TEST INJECTION POINT" in line
    ]
    assert marker_lines, f"{SCHEMA_FILE} lost its injection-point marker"
    annotation_lines = [
        line for line in original.splitlines() if "annotation deprecated :=" in line
    ]
    assert annotation_lines, (
        f"{SCHEMA_FILE} is expected to declare a 'deprecated' annotation value"
    )

    addition = textwrap.dedent(
        """
          type Shipment extending Timestamped {
            required tracking_code: str {
              constraint exclusive;
            }
            trackingURL: str;
            required multi watchers: Account {
              note: str;
            }
          }
        """
    )
    mutated_lines = []
    for line in original.splitlines():
        if "annotation deprecated :=" in line:
            continue
        if "TEST INJECTION POINT" in line:
            mutated_lines.append(addition)
        mutated_lines.append(line)
    mutated = "\n".join(mutated_lines) + "\n"

    before_migrations = list(doc["migrations"])
    out_path = "/tmp/audit-migrated.json"
    if os.path.exists(out_path):
        os.remove(out_path)

    try:
        with open(SCHEMA_FILE, "w", encoding="utf-8") as handle:
            handle.write(mutated)
        created = run_cli(
            [
                "gel",
                "migration",
                "create",
                "--non-interactive",
                "--allow-unsafe",
                "--schema-dir",
                SCHEMA_DIR,
            ]
        )
        assert created.returncode == 0, (
            "Failed to create the mutation migration.\n"
            f"stdout:\n{created.stdout}\nstderr:\n{created.stderr}"
        )
        applied = run_cli(["gel", "migrate"])
        assert applied.returncode == 0, (
            f"Failed to apply the mutation migration.\n"
            f"stdout:\n{applied.stdout}\nstderr:\n{applied.stderr}"
        )

        proc = run_module(["audit", "--out", out_path])
        assert os.path.isfile(out_path), (
            "The audit must still produce a document after the schema changed.\n"
            f"exit={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
        new_doc = json.load(open(out_path, encoding="utf-8"))

        names = {otype["name"] for otype in new_doc["object_types"]}
        assert "default::Shipment" in names, (
            f"The newly migrated type must be audited, got {sorted(names)}"
        )
        shipment = next(
            otype for otype in new_doc["object_types"] if otype["name"] == "default::Shipment"
        )
        shipment_pointers = {ptr["name"] for ptr in shipment["pointers"]}
        assert {"tracking_code", "trackingURL", "watchers"} <= shipment_pointers, (
            f"default::Shipment pointers are incomplete: {sorted(shipment_pointers)}"
        )
        inherited = {
            ptr["name"]
            for otype in new_doc["object_types"]
            if otype["name"] == "default::Timestamped"
            for ptr in otype["pointers"]
        }
        assert inherited and inherited <= shipment_pointers, (
            "default::Shipment must report the pointers inherited from its abstract "
            f"parent: missing {sorted(inherited - shipment_pointers)}"
        )
        watchers = next(
            ptr for ptr in shipment["pointers"] if ptr["name"] == "watchers"
        )
        assert [lp["name"] for lp in watchers["link_properties"]] == ["note"], (
            "default::Shipment.watchers must report its 'note' link property, got "
            f"{watchers['link_properties']}"
        )

        reported = {(rule, target) for rule, _, target in documented_violations(new_doc)}
        for pair in [
            ("pointer-name-not-snake-case", "default::Shipment.trackingURL"),
            ("multi-link-required", "default::Shipment.watchers"),
            ("link-property-not-required", "default::Shipment.watchers@note"),
        ]:
            assert pair in reported, (
                f"Expected the new violation {pair} after the migration, got {sorted(reported)}"
            )
        assert ("deprecated-type", "default::LegacyWidget") not in reported, (
            "The deprecated annotation was removed, so its violation must disappear"
        )
        assert documented_violations(new_doc) == recompute_violations(new_doc), (
            "After the schema change the violations no longer match the rule definitions"
        )
        assert new_doc["migrations"][: len(before_migrations)] == before_migrations, (
            "The migration history must keep its previous prefix, got "
            f"{new_doc['migrations']}"
        )
        assert len(new_doc["migrations"]) > len(before_migrations), (
            "The newly applied migration must show up in the reported history, got "
            f"{new_doc['migrations']}"
        )
    finally:
        with open(SCHEMA_FILE, "w", encoding="utf-8") as handle:
            handle.write(original)
        restore = run_cli(
            [
                "gel",
                "migration",
                "create",
                "--non-interactive",
                "--allow-unsafe",
                "--schema-dir",
                SCHEMA_DIR,
            ]
        )
        print(f"[restore migration create]\n{restore.stdout}\n{restore.stderr}")
        reapply = run_cli(["gel", "migrate"])
        print(f"[restore migrate]\n{reapply.stdout}\n{reapply.stderr}")
        print(
            "[migration files]"
            + str(sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql"))))
        )
