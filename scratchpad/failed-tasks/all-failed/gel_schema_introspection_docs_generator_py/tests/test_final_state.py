import json
import os
import shutil
import subprocess

import pytest

PROJECT_DIR = "/home/user/museum"
GENERATOR = "tools/schema_docs.py"

BASELINE_DIR = "/tmp/verify-default"
RERUN_DIR = "/tmp/verify-default-2"
ARCHIVE_DIR = "/tmp/verify-archive"
GHOST_DIR = "/tmp/verify-ghost"
LIVE_DIR = "/tmp/verify-live"
LIVE_PROBE_DIR = "/tmp/verify-live-probe"
LIVE_RESTORED_DIR = "/tmp/verify-live-restored"

EXPECTED_OBJECT_TYPES = [
    "Artifact",
    "Curator",
    "Documented",
    "Exhibition",
    "Gallery",
    "LoanRecord",
    "Painting",
    "Sculpture",
    "Tracked",
]

EXPECTED_SCALAR_TYPES = ["AccessionCode", "ConditionGrade", "Rating"]

EXPECTED_LINT = [
    {
        "rule": "L001",
        "subject": "Exhibition",
        "message": "type 'Exhibition' has no exclusive constraint",
    },
    {
        "rule": "L001",
        "subject": "LoanRecord",
        "message": "type 'LoanRecord' has no exclusive constraint",
    },
    {
        "rule": "L002",
        "subject": "Gallery",
        "message": "type 'Gallery' has no doc annotation",
    },
    {
        "rule": "L002",
        "subject": "LoanRecord",
        "message": "type 'LoanRecord' has no doc annotation",
    },
    {
        "rule": "L003",
        "subject": "Artifact.gallery",
        "message": "link 'Artifact.gallery' uses the default restrict deletion policy",
    },
    {
        "rule": "L003",
        "subject": "Painting.gallery",
        "message": "link 'Painting.gallery' uses the default restrict deletion policy",
    },
    {
        "rule": "L003",
        "subject": "Sculpture.gallery",
        "message": "link 'Sculpture.gallery' uses the default restrict deletion policy",
    },
    {
        "rule": "L004",
        "subject": "Rating",
        "message": "scalar type 'Rating' has no constraints",
    },
]

PROBE_FILE = os.path.join(PROJECT_DIR, "dbschema", "probe.gel")
PROBE_SDL = "module default {\n  type LiveProbeMarker {\n    required tag: str;\n  }\n}\n"


# --------------------------------------------------------------------------
# helpers / fixtures
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def gel_server():
    """Guarantee the local Gel server is up before anything touches the DB."""
    start = shutil.which("start-gel")
    assert start is not None, "The 'start-gel' helper script is not available in PATH."
    proc = subprocess.run([start], capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, (
        f"'start-gel' failed with exit code {proc.returncode}.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    return True


def run_generator(out_dir, module="default", cleanup=True):
    if cleanup and os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    return subprocess.run(
        ["python3", GENERATOR, "--module", module, "--out-dir", out_dir],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=300,
    )


def read_outputs(out_dir):
    json_path = os.path.join(out_dir, "schema.json")
    md_path = os.path.join(out_dir, "SCHEMA.md")
    assert os.path.isfile(json_path), f"{json_path} was not created by the generator."
    assert os.path.isfile(md_path), f"{md_path} was not created by the generator."
    with open(json_path, "rb") as f:
        json_bytes = f.read()
    with open(md_path, "rb") as f:
        md_bytes = f.read()
    try:
        data = json.loads(json_bytes.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(f"{json_path} is not valid UTF-8 JSON: {exc}") from exc
    return data, json_bytes, md_bytes


@pytest.fixture(scope="session")
def baseline(gel_server):
    proc = run_generator(BASELINE_DIR)
    assert proc.returncode == 0, (
        "The generator failed for module 'default'.\n"
        f"exit code: {proc.returncode}\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    data, json_bytes, md_bytes = read_outputs(BASELINE_DIR)
    return {
        "proc": proc,
        "data": data,
        "json_bytes": json_bytes,
        "md_bytes": md_bytes,
        "md_text": md_bytes.decode("utf-8"),
    }


def object_type(data, name):
    matches = [t for t in data["object_types"] if t.get("name") == name]
    assert matches, (
        f"Expected object type '{name}' in the generated documentation, "
        f"found: {[t.get('name') for t in data['object_types']]}"
    )
    return matches[0]


def scalar_type(data, name):
    matches = [t for t in data["scalar_types"] if t.get("name") == name]
    assert matches, (
        f"Expected scalar type '{name}' in the generated documentation, "
        f"found: {[t.get('name') for t in data['scalar_types']]}"
    )
    return matches[0]


def pointer(entries, kind, owner, name):
    matches = [p for p in entries if p.get("name") == name]
    assert matches, (
        f"Expected {kind} '{name}' on '{owner}', "
        f"found: {[p.get('name') for p in entries]}"
    )
    return matches[0]


# --------------------------------------------------------------------------
# 1. schema is applied through the migration workflow
# --------------------------------------------------------------------------


def test_migration_status_is_up_to_date(gel_server):
    proc = subprocess.run(
        ["gel", "migration", "status"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, (
        "'gel migration status' did not succeed; the schema is not applied cleanly.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    combined = (proc.stdout + proc.stderr).lower()
    assert "up to date" in combined, (
        f"Expected the database to be up to date with the migrations, got: {proc.stdout} {proc.stderr}"
    )


def test_migrations_directory_has_migration_files(gel_server):
    migrations_dir = os.path.join(PROJECT_DIR, "dbschema", "migrations")
    assert os.path.isdir(migrations_dir), (
        f"Expected the migrations directory {migrations_dir} to exist."
    )
    files = [f for f in os.listdir(migrations_dir) if f.endswith(".edgeql")]
    assert files, (
        f"Expected at least one .edgeql migration script in {migrations_dir}, found: "
        f"{os.listdir(migrations_dir)}"
    )


# --------------------------------------------------------------------------
# 2. generator runs and reports its artifacts
# --------------------------------------------------------------------------


def test_generator_reports_written_paths(baseline):
    lines = [line.strip() for line in baseline["proc"].stdout.splitlines() if line.strip()]
    assert len(lines) >= 2, (
        "Expected the generator to print the JSON path on the first stdout line and the "
        f"Markdown path on the second, got: {baseline['proc'].stdout!r}"
    )
    assert lines[0] == os.path.join(BASELINE_DIR, "schema.json"), (
        f"First stdout line should be {os.path.join(BASELINE_DIR, 'schema.json')}, got {lines[0]!r}"
    )
    assert lines[1] == os.path.join(BASELINE_DIR, "SCHEMA.md"), (
        f"Second stdout line should be {os.path.join(BASELINE_DIR, 'SCHEMA.md')}, got {lines[1]!r}"
    )


# --------------------------------------------------------------------------
# 3. JSON top level
# --------------------------------------------------------------------------


def test_json_top_level_shape(baseline):
    data = baseline["data"]
    assert isinstance(data, dict), "schema.json must contain a JSON object at the top level."
    assert set(data.keys()) == {"module", "object_types", "scalar_types", "lint"}, (
        "schema.json must have exactly the keys module, object_types, scalar_types, lint; got: "
        f"{sorted(data.keys())}"
    )
    assert data["module"] == "default", (
        f"Expected \"module\": \"default\", got {data['module']!r}"
    )


# --------------------------------------------------------------------------
# 4. exact object type set / builtin exclusion
# --------------------------------------------------------------------------


def test_object_type_set_is_exact_and_sorted(baseline):
    names = [t.get("name") for t in baseline["data"]["object_types"]]
    assert names == EXPECTED_OBJECT_TYPES, (
        f"object_types must be exactly {EXPECTED_OBJECT_TYPES} (sorted by name), got: {names}"
    )


def test_no_builtin_or_foreign_types_are_documented(baseline):
    names = [t.get("name") for t in baseline["data"]["object_types"]]
    for name in names:
        assert "::" not in name, (
            f"Type names of the documented module must not be module-qualified, got: {name}"
        )
    for forbidden in ["std::Object", "schema::ObjectType", "cfg::Config", "StorageBox"]:
        assert forbidden not in names, (
            f"'{forbidden}' must not appear in the documented object types of module 'default'."
        )


def test_object_type_entry_keys_are_exact(baseline):
    expected_keys = {
        "name",
        "abstract",
        "doc",
        "bases",
        "ancestors",
        "constraints",
        "indexes",
        "properties",
        "links",
    }
    for entry in baseline["data"]["object_types"]:
        assert set(entry.keys()) == expected_keys, (
            f"Object type entry '{entry.get('name')}' must have exactly the keys "
            f"{sorted(expected_keys)}, got: {sorted(entry.keys())}"
        )


# --------------------------------------------------------------------------
# 5. inheritance resolution
# --------------------------------------------------------------------------


def test_inheritance_of_abstract_multiple_inheritance_type(baseline):
    artifact = object_type(baseline["data"], "Artifact")
    assert artifact["abstract"] is True, "Artifact must be reported as abstract."
    assert artifact["bases"] == ["Documented", "Tracked"], (
        f"Artifact.bases must be ['Documented', 'Tracked'], got {artifact['bases']}"
    )
    assert artifact["ancestors"] == ["Documented", "Tracked"], (
        f"Artifact.ancestors must be ['Documented', 'Tracked'], got {artifact['ancestors']}"
    )


def test_inheritance_of_concrete_subtype(baseline):
    painting = object_type(baseline["data"], "Painting")
    assert painting["abstract"] is False, "Painting must be reported as non-abstract."
    assert painting["bases"] == ["Artifact"], (
        f"Painting.bases must be ['Artifact'], got {painting['bases']}"
    )
    assert painting["ancestors"] == ["Artifact", "Documented", "Tracked"], (
        "Painting.ancestors must be ['Artifact', 'Documented', 'Tracked'], got "
        f"{painting['ancestors']}"
    )


def test_standard_library_ancestors_are_filtered_out(baseline):
    curator = object_type(baseline["data"], "Curator")
    assert curator["bases"] == [], (
        f"Curator.bases must be [] (std ancestors are filtered out), got {curator['bases']}"
    )
    assert curator["ancestors"] == [], (
        f"Curator.ancestors must be [], got {curator['ancestors']}"
    )


# --------------------------------------------------------------------------
# 6. properties
# --------------------------------------------------------------------------


def test_property_set_includes_inherited_and_excludes_id(baseline):
    painting = object_type(baseline["data"], "Painting")
    names = [p.get("name") for p in painting["properties"]]
    assert names == [
        "accession",
        "condition",
        "created_at",
        "display_label",
        "height_cm",
        "medium",
        "summary",
        "title",
        "width_cm",
    ], f"Unexpected Painting.properties (inherited properties included, 'id' excluded): {names}"


def test_property_entry_keys_are_exact(baseline):
    expected_keys = {
        "name",
        "target",
        "required",
        "cardinality",
        "computed",
        "constraints",
        "doc",
    }
    for entry in baseline["data"]["object_types"]:
        for prop in entry["properties"]:
            assert set(prop.keys()) == expected_keys, (
                f"Property '{entry['name']}.{prop.get('name')}' must have exactly the keys "
                f"{sorted(expected_keys)}, got: {sorted(prop.keys())}"
            )


def test_custom_scalar_property_with_exclusive_constraint(baseline):
    painting = object_type(baseline["data"], "Painting")
    accession = pointer(painting["properties"], "property", "Painting", "accession")
    assert accession["target"] == "AccessionCode", (
        f"Painting.accession target must be 'AccessionCode', got {accession['target']!r}"
    )
    assert accession["required"] is True, "Painting.accession must be required."
    assert accession["cardinality"] == "single", (
        f"Painting.accession cardinality must be 'single', got {accession['cardinality']!r}"
    )
    assert accession["computed"] is False, "Painting.accession must not be computed."
    assert accession["constraints"] == ["std::exclusive"], (
        f"Painting.accession constraints must be ['std::exclusive'], got {accession['constraints']}"
    )


def test_computed_property_is_flagged(baseline):
    painting = object_type(baseline["data"], "Painting")
    label = pointer(painting["properties"], "property", "Painting", "display_label")
    assert label["computed"] is True, "Painting.display_label must be reported as computed."
    assert label["target"] == "std::str", (
        f"Painting.display_label target must be 'std::str', got {label['target']!r}"
    )
    assert label["cardinality"] == "single", (
        f"Painting.display_label cardinality must be 'single', got {label['cardinality']!r}"
    )


def test_inherited_required_property_target_is_qualified(baseline):
    painting = object_type(baseline["data"], "Painting")
    created = pointer(painting["properties"], "property", "Painting", "created_at")
    assert created["target"] == "std::datetime", (
        f"Painting.created_at target must be 'std::datetime', got {created['target']!r}"
    )
    assert created["required"] is True, "Painting.created_at must be required."


def test_multi_property_cardinality(baseline):
    curator = object_type(baseline["data"], "Curator")
    specialties = pointer(curator["properties"], "property", "Curator", "specialties")
    assert specialties["cardinality"] == "multi", (
        f"Curator.specialties cardinality must be 'multi', got {specialties['cardinality']!r}"
    )
    assert specialties["required"] is False, "Curator.specialties must not be required."
    assert specialties["target"] == "std::str", (
        f"Curator.specialties target must be 'std::str', got {specialties['target']!r}"
    )


def test_property_level_annotation_is_surfaced(baseline):
    curator = object_type(baseline["data"], "Curator")
    email = pointer(curator["properties"], "property", "Curator", "email")
    assert email["doc"] == "Primary contact address.", (
        f"Curator.email doc must be 'Primary contact address.', got {email['doc']!r}"
    )
    name = pointer(curator["properties"], "property", "Curator", "name")
    assert name["doc"] == "", f"Curator.name doc must be the empty string, got {name['doc']!r}"


def test_custom_scalar_target_of_property(baseline):
    loan = object_type(baseline["data"], "LoanRecord")
    rating = pointer(loan["properties"], "property", "LoanRecord", "condition_rating")
    assert rating["target"] == "Rating", (
        f"LoanRecord.condition_rating target must be 'Rating', got {rating['target']!r}"
    )


# --------------------------------------------------------------------------
# 7. links
# --------------------------------------------------------------------------


def test_link_entry_keys_are_exact(baseline):
    expected_keys = {
        "name",
        "target",
        "required",
        "cardinality",
        "computed",
        "on_target_delete",
        "link_properties",
        "constraints",
        "doc",
    }
    for entry in baseline["data"]["object_types"]:
        for link in entry["links"]:
            assert set(link.keys()) == expected_keys, (
                f"Link '{entry['name']}.{link.get('name')}' must have exactly the keys "
                f"{sorted(expected_keys)}, got: {sorted(link.keys())}"
            )


def test_exhibition_link_set(baseline):
    exhibition = object_type(baseline["data"], "Exhibition")
    names = [l.get("name") for l in exhibition["links"]]
    assert names == ["exhibits", "lead_curator"], (
        f"Exhibition.links must be exactly ['exhibits', 'lead_curator'], got {names}"
    )


def test_multi_link_with_link_properties_and_allow_policy(baseline):
    exhibition = object_type(baseline["data"], "Exhibition")
    exhibits = pointer(exhibition["links"], "link", "Exhibition", "exhibits")
    assert exhibits["target"] == "Artifact", (
        f"Exhibition.exhibits target must be 'Artifact', got {exhibits['target']!r}"
    )
    assert exhibits["cardinality"] == "multi", (
        f"Exhibition.exhibits cardinality must be 'multi', got {exhibits['cardinality']!r}"
    )
    assert exhibits["required"] is False, "Exhibition.exhibits must not be required."
    assert exhibits["computed"] is False, "Exhibition.exhibits must not be computed."
    assert exhibits["on_target_delete"] == "allow", (
        f"Exhibition.exhibits on_target_delete must be 'allow', got {exhibits['on_target_delete']!r}"
    )
    assert exhibits["doc"] == "Artifacts on display, in curated order.", (
        f"Exhibition.exhibits doc mismatch, got {exhibits['doc']!r}"
    )
    assert exhibits["link_properties"] == [
        {"name": "display_order", "target": "std::int64"},
        {"name": "insured_value", "target": "std::int64"},
    ], (
        "Exhibition.exhibits link_properties must list only the declared link properties "
        f"(sorted by name), got {exhibits['link_properties']}"
    )


def test_required_single_link_with_delete_source_policy(baseline):
    exhibition = object_type(baseline["data"], "Exhibition")
    lead = pointer(exhibition["links"], "link", "Exhibition", "lead_curator")
    assert lead["target"] == "Curator", (
        f"Exhibition.lead_curator target must be 'Curator', got {lead['target']!r}"
    )
    assert lead["cardinality"] == "single", (
        f"Exhibition.lead_curator cardinality must be 'single', got {lead['cardinality']!r}"
    )
    assert lead["required"] is True, "Exhibition.lead_curator must be required."
    assert lead["on_target_delete"] == "delete source", (
        f"Exhibition.lead_curator on_target_delete must be 'delete source', got "
        f"{lead['on_target_delete']!r}"
    )
    assert lead["link_properties"] == [], (
        f"Exhibition.lead_curator must have no link properties, got {lead['link_properties']}"
    )


def test_loan_record_link_policy(baseline):
    loan = object_type(baseline["data"], "LoanRecord")
    artifact = pointer(loan["links"], "link", "LoanRecord", "artifact")
    assert artifact["on_target_delete"] == "delete source", (
        f"LoanRecord.artifact on_target_delete must be 'delete source', got "
        f"{artifact['on_target_delete']!r}"
    )


def test_computed_backlink_reporting(baseline):
    gallery = object_type(baseline["data"], "Gallery")
    names = [l.get("name") for l in gallery["links"]]
    assert names == ["artifacts"], (
        f"Gallery.links must be exactly ['artifacts'], got {names}"
    )
    artifacts = gallery["links"][0]
    assert artifacts["computed"] is True, "Gallery.artifacts must be reported as computed."
    assert artifacts["cardinality"] == "multi", (
        f"Gallery.artifacts cardinality must be 'multi', got {artifacts['cardinality']!r}"
    )
    assert artifacts["target"] == "Artifact", (
        f"Gallery.artifacts target must be 'Artifact', got {artifacts['target']!r}"
    )
    assert artifacts["on_target_delete"] is None, (
        f"Gallery.artifacts on_target_delete must be null for computed links, got "
        f"{artifacts['on_target_delete']!r}"
    )


def test_inherited_link_and_no_type_link(baseline):
    painting = object_type(baseline["data"], "Painting")
    names = [l.get("name") for l in painting["links"]]
    assert names == ["gallery"], f"Painting.links must be exactly ['gallery'], got {names}"
    assert painting["links"][0]["on_target_delete"] == "restrict", (
        "Painting.gallery must report the default 'restrict' target deletion policy, got "
        f"{painting['links'][0]['on_target_delete']!r}"
    )
    for entry in baseline["data"]["object_types"]:
        link_names = [l.get("name") for l in entry["links"]]
        assert "__type__" not in link_names, (
            f"The implicit '__type__' link must never be reported (found on {entry['name']})."
        )


# --------------------------------------------------------------------------
# 8. type-level constraints, indexes, annotations
# --------------------------------------------------------------------------


def test_type_level_constraints_and_indexes(baseline):
    painting = object_type(baseline["data"], "Painting")
    assert painting["constraints"] == ["std::expression"], (
        f"Painting.constraints must be ['std::expression'], got {painting['constraints']}"
    )
    assert painting["indexes"] == [".title"], (
        f"Painting.indexes must be ['.title'], got {painting['indexes']}"
    )
    artifact = object_type(baseline["data"], "Artifact")
    assert artifact["indexes"] == [".title"], (
        f"Artifact.indexes must be ['.title'], got {artifact['indexes']}"
    )
    curator = object_type(baseline["data"], "Curator")
    assert curator["indexes"] == [], (
        f"Curator.indexes must be [], got {curator['indexes']}"
    )


def test_type_documentation_annotations(baseline):
    artifact = object_type(baseline["data"], "Artifact")
    assert artifact["doc"] == "Any physical item held by the museum.", (
        f"Artifact.doc mismatch, got {artifact['doc']!r}"
    )
    gallery = object_type(baseline["data"], "Gallery")
    assert gallery["doc"] == "", f"Gallery.doc must be the empty string, got {gallery['doc']!r}"
    loan = object_type(baseline["data"], "LoanRecord")
    assert loan["doc"] == "", f"LoanRecord.doc must be the empty string, got {loan['doc']!r}"


# --------------------------------------------------------------------------
# 9. scalar types
# --------------------------------------------------------------------------


def test_scalar_type_set_and_keys(baseline):
    names = [s.get("name") for s in baseline["data"]["scalar_types"]]
    assert names == EXPECTED_SCALAR_TYPES, (
        f"scalar_types must be exactly {EXPECTED_SCALAR_TYPES}, got {names}"
    )
    expected_keys = {"name", "doc", "bases", "enum_values", "constraints"}
    for entry in baseline["data"]["scalar_types"]:
        assert set(entry.keys()) == expected_keys, (
            f"Scalar entry '{entry.get('name')}' must have exactly the keys "
            f"{sorted(expected_keys)}, got {sorted(entry.keys())}"
        )


def test_constrained_scalar(baseline):
    accession = scalar_type(baseline["data"], "AccessionCode")
    assert accession["bases"] == ["std::str"], (
        f"AccessionCode.bases must be ['std::str'], got {accession['bases']}"
    )
    assert accession["enum_values"] == [], (
        f"AccessionCode.enum_values must be [], got {accession['enum_values']}"
    )
    assert accession["constraints"] == ["std::regexp"], (
        f"AccessionCode.constraints must be ['std::regexp'], got {accession['constraints']}"
    )


def test_enum_scalar_keeps_declaration_order(baseline):
    grade = scalar_type(baseline["data"], "ConditionGrade")
    assert grade["enum_values"] == ["Pristine", "Good", "Fair", "Poor"], (
        "ConditionGrade.enum_values must keep declaration order "
        f"['Pristine', 'Good', 'Fair', 'Poor'], got {grade['enum_values']}"
    )
    assert grade["constraints"] == [], (
        f"ConditionGrade.constraints must be [], got {grade['constraints']}"
    )


def test_plain_scalar(baseline):
    rating = scalar_type(baseline["data"], "Rating")
    assert rating["bases"] == ["std::int64"], (
        f"Rating.bases must be ['std::int64'], got {rating['bases']}"
    )
    assert rating["enum_values"] == [], (
        f"Rating.enum_values must be [], got {rating['enum_values']}"
    )
    assert rating["constraints"] == [], (
        f"Rating.constraints must be [], got {rating['constraints']}"
    )


# --------------------------------------------------------------------------
# 10. lint findings
# --------------------------------------------------------------------------


def test_lint_findings_exact_set_and_order(baseline):
    lint = baseline["data"]["lint"]
    assert lint == EXPECTED_LINT, (
        "The lint findings must match exactly (sorted by rule then subject).\n"
        f"expected: {json.dumps(EXPECTED_LINT, indent=2)}\n"
        f"actual:   {json.dumps(lint, indent=2)}"
    )


def test_lint_does_not_report_false_positives(baseline):
    lint = baseline["data"]["lint"]
    l001_subjects = {f["subject"] for f in lint if f["rule"] == "L001"}
    for clean in ["Curator", "Gallery", "Painting", "Sculpture", "Artifact", "Documented", "Tracked"]:
        assert clean not in l001_subjects, (
            f"'{clean}' must not produce an L001 finding, got L001 subjects: {sorted(l001_subjects)}"
        )
    l004_subjects = {f["subject"] for f in lint if f["rule"] == "L004"}
    for clean in ["ConditionGrade", "AccessionCode"]:
        assert clean not in l004_subjects, (
            f"'{clean}' must not produce an L004 finding, got L004 subjects: {sorted(l004_subjects)}"
        )


# --------------------------------------------------------------------------
# 11. second module is isolated
# --------------------------------------------------------------------------


def test_archive_module_is_documented_in_isolation(gel_server):
    proc = run_generator(ARCHIVE_DIR, module="archive")
    assert proc.returncode == 0, (
        "The generator failed for module 'archive'.\n"
        f"exit code: {proc.returncode}\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    data, _, _ = read_outputs(ARCHIVE_DIR)
    assert data["module"] == "archive", (
        f"Expected \"module\": \"archive\", got {data['module']!r}"
    )
    names = [t.get("name") for t in data["object_types"]]
    assert names == ["StorageBox"], (
        f"The archive module must document exactly ['StorageBox'], got {names}"
    )
    assert data["scalar_types"] == [], (
        f"The archive module has no scalar types, got {data['scalar_types']}"
    )

    box = object_type(data, "StorageBox")
    assert box["doc"] == "A crate in climate-controlled storage.", (
        f"StorageBox.doc mismatch, got {box['doc']!r}"
    )
    prop_names = [p.get("name") for p in box["properties"]]
    assert prop_names == ["code"], (
        f"StorageBox.properties must be exactly ['code'], got {prop_names}"
    )
    code = box["properties"][0]
    assert code["target"] == "std::str", (
        f"StorageBox.code target must be 'std::str', got {code['target']!r}"
    )
    assert code["required"] is True, "StorageBox.code must be required."
    assert code["constraints"] == ["std::exclusive"], (
        f"StorageBox.code constraints must be ['std::exclusive'], got {code['constraints']}"
    )

    link_names = [l.get("name") for l in box["links"]]
    assert link_names == ["contents"], (
        f"StorageBox.links must be exactly ['contents'], got {link_names}"
    )
    contents = box["links"][0]
    assert contents["target"] == "default::Artifact", (
        "Cross-module targets must stay fully qualified: StorageBox.contents target must be "
        f"'default::Artifact', got {contents['target']!r}"
    )
    assert contents["cardinality"] == "multi", (
        f"StorageBox.contents cardinality must be 'multi', got {contents['cardinality']!r}"
    )
    assert contents["on_target_delete"] == "restrict", (
        f"StorageBox.contents on_target_delete must be 'restrict', got "
        f"{contents['on_target_delete']!r}"
    )

    assert data["lint"] == [
        {
            "rule": "L003",
            "subject": "StorageBox.contents",
            "message": "link 'StorageBox.contents' uses the default restrict deletion policy",
        }
    ], f"Unexpected lint findings for module 'archive': {json.dumps(data['lint'], indent=2)}"


# --------------------------------------------------------------------------
# 12. unknown module
# --------------------------------------------------------------------------


def test_unknown_module_fails_without_side_effects(gel_server):
    if os.path.isdir(GHOST_DIR):
        shutil.rmtree(GHOST_DIR)
    proc = subprocess.run(
        ["python3", GENERATOR, "--module", "ghosts", "--out-dir", GHOST_DIR],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 3, (
        "An unknown module must make the generator exit with code 3, got "
        f"{proc.returncode}.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "error: unknown module: ghosts" in proc.stderr, (
        f"stderr must contain 'error: unknown module: ghosts', got: {proc.stderr!r}"
    )
    assert not os.path.exists(GHOST_DIR), (
        f"{GHOST_DIR} must not be created when the module does not exist."
    )


# --------------------------------------------------------------------------
# 13. markdown reference
# --------------------------------------------------------------------------


def test_markdown_title_and_section_order(baseline):
    text = baseline["md_text"]
    first_line = text.splitlines()[0] if text.splitlines() else ""
    assert first_line == "# Schema Reference: default", (
        f"The first line of SCHEMA.md must be '# Schema Reference: default', got {first_line!r}"
    )
    positions = {}
    for heading in ["## Object Types", "## Scalar Types", "## Lint Findings"]:
        idx = text.find(heading)
        assert idx != -1, f"SCHEMA.md must contain the heading '{heading}'."
        positions[heading] = idx
    assert (
        positions["## Object Types"]
        < positions["## Scalar Types"]
        < positions["## Lint Findings"]
    ), (
        "SCHEMA.md must contain '## Object Types', '## Scalar Types' and '## Lint Findings' "
        f"in that order, got offsets: {positions}"
    )


def test_markdown_has_heading_per_documented_type(baseline):
    lines = [line.strip() for line in baseline["md_text"].splitlines()]
    for name in EXPECTED_OBJECT_TYPES + EXPECTED_SCALAR_TYPES:
        assert f"### {name}" in lines, (
            f"SCHEMA.md must contain a '### {name}' heading line."
        )


def test_markdown_lists_every_lint_finding(baseline):
    text = baseline["md_text"]
    start = text.find("## Lint Findings")
    assert start != -1, "SCHEMA.md must contain the '## Lint Findings' heading."
    section = text[start:]
    for finding in EXPECTED_LINT:
        matching = [
            line
            for line in section.splitlines()
            if finding["rule"] in line and finding["message"] in line
        ]
        assert matching, (
            "The lint section of SCHEMA.md must contain a line with rule "
            f"{finding['rule']} and the message {finding['message']!r}."
        )


# --------------------------------------------------------------------------
# 14. determinism
# --------------------------------------------------------------------------


def test_rerun_is_byte_identical(baseline):
    proc = run_generator(RERUN_DIR)
    assert proc.returncode == 0, (
        f"The second generator run failed.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    _, json_bytes, md_bytes = read_outputs(RERUN_DIR)
    assert json_bytes == baseline["json_bytes"], (
        "schema.json must be byte-identical across runs against an unchanged database."
    )
    assert md_bytes == baseline["md_bytes"], (
        "SCHEMA.md must be byte-identical across runs against an unchanged database."
    )


# --------------------------------------------------------------------------
# 15. reads the live database, not the .gel sources
# --------------------------------------------------------------------------


def test_generator_works_without_the_schema_sources(baseline):
    dbschema = os.path.join(PROJECT_DIR, "dbschema")
    hidden = "/tmp/verify-dbschema-backup"
    assert os.path.isdir(dbschema), f"{dbschema} is missing."
    if os.path.exists(hidden):
        shutil.rmtree(hidden)
    shutil.copytree(dbschema, hidden)
    shutil.rmtree(dbschema)
    try:
        proc = run_generator(LIVE_DIR)
    finally:
        if not os.path.isdir(dbschema):
            shutil.copytree(hidden, dbschema)
        shutil.rmtree(hidden)
    assert proc.returncode == 0, (
        "The generator must still work when the dbschema/ directory is absent, because it has "
        f"to read the live database.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    _, json_bytes, md_bytes = read_outputs(LIVE_DIR)
    assert json_bytes == baseline["json_bytes"], (
        "schema.json produced without dbschema/ must be byte-identical to the baseline."
    )
    assert md_bytes == baseline["md_bytes"], (
        "SCHEMA.md produced without dbschema/ must be byte-identical to the baseline."
    )


def test_generator_reflects_a_newly_migrated_type(baseline):
    def migrate():
        create = subprocess.run(
            ["gel", "migration", "create", "--non-interactive", "--allow-unsafe"],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=600,
        )
        apply_ = subprocess.run(
            ["gel", "migrate"],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=600,
        )
        return create, apply_

    with open(PROBE_FILE, "w", encoding="utf-8") as f:
        f.write(PROBE_SDL)
    try:
        create, apply_ = migrate()
        assert create.returncode == 0, (
            "Could not create a migration for the probe type.\n"
            f"stdout: {create.stdout}\nstderr: {create.stderr}"
        )
        assert apply_.returncode == 0, (
            f"Could not apply the probe migration.\nstdout: {apply_.stdout}\nstderr: {apply_.stderr}"
        )
        proc = run_generator(LIVE_PROBE_DIR)
        assert proc.returncode == 0, (
            f"The generator failed after the probe migration.\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
        )
        data, _, _ = read_outputs(LIVE_PROBE_DIR)
        names = [t.get("name") for t in data["object_types"]]
        assert "LiveProbeMarker" in names, (
            "The generator must report the type that was just added to the live database, "
            f"got object types: {names}"
        )
        lint_pairs = {(f["rule"], f["subject"]) for f in data["lint"]}
        assert ("L001", "LiveProbeMarker") in lint_pairs, (
            f"Expected an L001 finding for LiveProbeMarker, got: {sorted(lint_pairs)}"
        )
        assert ("L002", "LiveProbeMarker") in lint_pairs, (
            f"Expected an L002 finding for LiveProbeMarker, got: {sorted(lint_pairs)}"
        )
    finally:
        if os.path.exists(PROBE_FILE):
            os.remove(PROBE_FILE)
        restore_create, restore_apply = migrate()

    assert restore_create.returncode == 0, (
        "Could not roll the probe type back out of the database.\n"
        f"stdout: {restore_create.stdout}\nstderr: {restore_create.stderr}"
    )
    assert restore_apply.returncode == 0, (
        "Could not apply the rollback migration.\n"
        f"stdout: {restore_apply.stdout}\nstderr: {restore_apply.stderr}"
    )
    proc = run_generator(LIVE_RESTORED_DIR)
    assert proc.returncode == 0, (
        f"The generator failed after the rollback.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    _, json_bytes, md_bytes = read_outputs(LIVE_RESTORED_DIR)
    assert json_bytes == baseline["json_bytes"], (
        "After rolling the probe type back out, schema.json must be byte-identical to the baseline."
    )
    assert md_bytes == baseline["md_bytes"], (
        "After rolling the probe type back out, SCHEMA.md must be byte-identical to the baseline."
    )
