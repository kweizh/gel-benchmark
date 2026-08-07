"""Final-state verification for gel_stdlib_string_regex_normalization_cli.

Every expectation is derived from an independent reference implementation of the
normalization specification (rules A-G of the task description) evaluated over the
RawProduct rows that are actually stored in the database at the time of the check.
This keeps the expectations correct for rows that are inserted while the tests run.
"""

import glob
import json
import os
import re
import subprocess
import time

import pytest

PROJECT_DIR = "/home/user/catalog"
NORMALIZE_SH = os.path.join(PROJECT_DIR, "normalize.sh")
QUERIES_DIR = os.path.join(PROJECT_DIR, "queries")
REPORT_JSON = os.path.join(PROJECT_DIR, "report.json")
MIGRATIONS_DIR = os.path.join(PROJECT_DIR, "dbschema", "migrations")
START_SCRIPT = "/usr/local/bin/gel-start.sh"

FORBIDDEN_EXTENSIONS = (
    ".py",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".rb",
    ".pl",
    ".php",
    ".go",
    ".lua",
    ".awk",
)
FORBIDDEN_WORDS = [
    "python",
    "python3",
    "node",
    "deno",
    "bun",
    "perl",
    "ruby",
    "awk",
    "gawk",
    "sed",
    "jq",
    "tr",
    "cut",
    "rev",
    "iconv",
    "xxd",
    "base64",
]

REPORT_KEYS = {
    "raw_total",
    "clean_total",
    "rejected_total",
    "tag_total",
    "products",
    "rejected",
    "tags",
}

PHONE_RE = re.compile(r"\+[0-9]{1,3}-[0-9]{3}-[0-9]{4}")
PHONE_SUB_RE = re.compile(r"(\+[0-9]{1,3}-[0-9]{3})-[0-9]{4}")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
COMPACT_SKU_RE = re.compile(r"^[A-Z]{3}[0-9]{4}[A-Z0-9]{2}$")
TAG_SPLIT_RE = re.compile(r"[,;/| \t]+")
WHITESPACE_RUN_RE = re.compile(r"[ \t\r\n]+")
ASCII_LETTERS_RE = re.compile(r"[A-Za-z]+")


# --------------------------------------------------------------------------- #
# Gel plumbing
# --------------------------------------------------------------------------- #
def _gel_env():
    env = dict(os.environ)
    env.setdefault("GEL_HOST", "127.0.0.1")
    env.setdefault("GEL_PORT", "5656")
    env.setdefault("GEL_USER", "admin")
    env.setdefault("GEL_BRANCH", "main")
    env.setdefault("GEL_CLIENT_TLS_SECURITY", "insecure")
    return env


def _run_gel(args, timeout=120, cwd=PROJECT_DIR):
    return subprocess.run(
        ["gel"] + args,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_gel_env(),
        cwd=cwd,
    )


def _server_ready():
    try:
        proc = _run_gel(["query", "-F", "json", "select 1"], timeout=30, cwd="/")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return proc.returncode == 0


@pytest.fixture(scope="session")
def gel_server():
    """Guarantee the local Gel server is up before any CLI/DB interaction."""
    if _server_ready():
        return True
    assert os.path.isfile(START_SCRIPT), (
        f"Gel server is not reachable and the start script {START_SCRIPT} is missing."
    )
    subprocess.run(
        ["bash", START_SCRIPT], capture_output=True, text=True, timeout=300, env=_gel_env()
    )
    deadline = time.time() + 180
    while time.time() < deadline:
        if _server_ready():
            return True
        time.sleep(3)
    pytest.fail("Local Gel server did not become reachable within 180 seconds.")


def query_json(edgeql, timeout=120):
    proc = _run_gel(["query", "-F", "json", edgeql], timeout=timeout)
    assert proc.returncode == 0, f"EdgeQL query failed: {edgeql}\nstderr: {proc.stderr}"
    return json.loads(proc.stdout)


def query_raw(edgeql, timeout=120):
    """Run a query without asserting success (used for negative constraint checks)."""
    return _run_gel(["query", "-F", "json", edgeql], timeout=timeout)


def eq_str(value):
    escaped = (
        value.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return "'" + escaped + "'"


def run_normalize(args=None, timeout=600):
    argv = ["bash", NORMALIZE_SH] + list(args or [])
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_gel_env(),
        cwd="/tmp",
    )


# --------------------------------------------------------------------------- #
# Independent reference implementation of the normalization specification
# --------------------------------------------------------------------------- #
def ref_compact_sku(raw_sku):
    compact = "".join(
        ch for ch in raw_sku.upper() if ("A" <= ch <= "Z") or ("0" <= ch <= "9")
    )
    return compact if COMPACT_SKU_RE.match(compact) else None


def ref_base_slug(raw_name):
    lowered = raw_name.lower()
    mapped = "".join(
        ch if (("a" <= ch <= "z") or ("0" <= ch <= "9")) else "-" for ch in lowered
    )
    collapsed = re.sub(r"-+", "-", mapped).strip("-")
    return collapsed[:40].rstrip("-")


def ref_display_name(raw_name):
    nw = WHITESPACE_RUN_RE.sub(" ", raw_name).strip(" \t\r\n")
    has_ascii_letter = any(("a" <= c <= "z") or ("A" <= c <= "Z") for c in nw)
    has_lower_ascii = any("a" <= c <= "z" for c in nw)
    if has_ascii_letter and not has_lower_ascii:
        nw = ASCII_LETTERS_RE.sub(lambda m: m.group(0)[0].upper() + m.group(0)[1:].lower(), nw)
    return nw


def ref_contacts(raw_contact):
    phones = sorted(set(PHONE_RE.findall(raw_contact)))
    emails = sorted({m.lower() for m in EMAIL_RE.findall(raw_contact)})
    redacted = EMAIL_RE.sub("[EMAIL]", raw_contact)
    redacted = PHONE_SUB_RE.sub(lambda m: m.group(1) + "-XXXX", redacted)
    return phones, emails, redacted


def ref_tags(raw_tags):
    found = []
    for piece in TAG_SPLIT_RE.split(raw_tags):
        normalized = "".join(
            ch for ch in piece.lower() if ("a" <= ch <= "z") or ("0" <= ch <= "9")
        )
        if 2 <= len(normalized) <= 24:
            found.append(normalized)
    return sorted(set(found))


def reference_state(raw_rows):
    """Return (products_by_source_id, rejected_by_source_id, tag_counts)."""
    rejected = {}
    survivors = []
    for row in sorted(raw_rows, key=lambda r: r["source_id"]):
        compact = ref_compact_sku(row["raw_sku"])
        if compact is None:
            rejected[row["source_id"]] = "BAD_SKU"
            continue
        base = ref_base_slug(row["raw_name"])
        if base == "":
            rejected[row["source_id"]] = "EMPTY_SLUG"
            continue
        survivors.append((row, compact, base))

    winners = {}
    for row, compact, _base in survivors:
        current = winners.get(compact)
        if current is None or row["source_id"] < current:
            winners[compact] = row["source_id"]

    accepted = []
    for row, compact, base in survivors:
        if winners[compact] != row["source_id"]:
            rejected[row["source_id"]] = "DUPLICATE_SKU"
        else:
            accepted.append((row, compact, base))

    slug_groups = {}
    for row, _compact, base in accepted:
        slug_groups.setdefault(base, []).append(row["source_id"])
    for ids in slug_groups.values():
        ids.sort()

    products = {}
    tag_counts = {}
    for row, compact, base in accepted:
        position = slug_groups[base].index(row["source_id"])
        slug = base if position == 0 else f"{base}-{position + 1}"
        phones, emails, redacted = ref_contacts(row["raw_contact"])
        tags = ref_tags(row["raw_tags"])
        for tag in tags:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
        products[row["source_id"]] = {
            "source_id": row["source_id"],
            "slug": slug,
            "display_name": ref_display_name(row["raw_name"]),
            "sku": f"{compact[0:3]}-{compact[3:7]}-{compact[7:9]}",
            "sku_prefix": compact[0:3],
            "sku_serial": int(compact[3:7]),
            "contact_redacted": redacted,
            "phones": phones,
            "emails": emails,
            "tag_summary": ",".join(tags),
            "tags": tags,
        }
    return products, rejected, tag_counts


def reference_report(raw_rows):
    products, rejected, tag_counts = reference_state(raw_rows)
    return {
        "raw_total": len(raw_rows),
        "clean_total": len(products),
        "rejected_total": len(rejected),
        "tag_total": len(tag_counts),
        "products": [
            {
                "source_id": p["source_id"],
                "slug": p["slug"],
                "sku": p["sku"],
                "sku_serial": p["sku_serial"],
                "display_name": p["display_name"],
                "tag_summary": p["tag_summary"],
            }
            for p in sorted(products.values(), key=lambda p: p["source_id"])
        ],
        "rejected": [
            {"source_id": sid, "reason": reason}
            for sid, reason in sorted(rejected.items(), key=lambda kv: kv[0])
        ],
        "tags": [
            {"name": name, "products": count}
            for name, count in sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
    }


# --------------------------------------------------------------------------- #
# Database readers
# --------------------------------------------------------------------------- #
def fetch_raw_rows():
    return query_json(
        "select RawProduct { source_id, raw_name, raw_sku, raw_contact, raw_tags }"
    )


def fetch_clean_products():
    rows = query_json(
        "select CleanProduct { id, source_id, slug, display_name, sku, sku_prefix, "
        "sku_serial, contact_redacted, phones, emails, tag_summary, tags: { name } }"
    )
    return {row["source_id"]: row for row in rows}


def fetch_rejected():
    rows = query_json("select RejectedProduct { source_id, reason }")
    return {row["source_id"]: row["reason"] for row in rows}


def fetch_tags():
    rows = query_json(
        "select Tag { id, name, products := count(.<tags[is CleanProduct]) }"
    )
    return {row["name"]: row for row in rows}


def parse_report(stdout):
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"stdout of normalize.sh is not a single parseable JSON document "
            f"({exc}); got: {stdout[:2000]!r}"
        )


@pytest.fixture(scope="session")
def seeded_raw_rows(gel_server):
    """Snapshot of the seeded RawProduct rows, taken before the tests mutate anything."""
    return {row["source_id"]: row for row in fetch_raw_rows()}


# --------------------------------------------------------------------------- #
# 1. Delivered artifacts and EdgeQL-only constraint
# --------------------------------------------------------------------------- #
def test_01_entrypoint_and_queries_exist():
    assert os.path.isfile(NORMALIZE_SH), f"Missing entrypoint script {NORMALIZE_SH}."
    assert os.path.isdir(QUERIES_DIR), f"Missing EdgeQL directory {QUERIES_DIR}."
    edgeql_files = [
        p for p in glob.glob(os.path.join(QUERIES_DIR, "**", "*.edgeql"), recursive=True)
        if os.path.getsize(p) > 0
    ]
    assert edgeql_files, (
        f"Expected at least one non-empty .edgeql file under {QUERIES_DIR}, found none."
    )


def test_02_no_foreign_language_files_in_project():
    offenders = []
    for root, dirs, files in os.walk(PROJECT_DIR):
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules"}]
        for name in files:
            if name.lower().endswith(FORBIDDEN_EXTENSIONS):
                offenders.append(os.path.join(root, name))
    assert not offenders, (
        "The normalization must be implemented in EdgeQL driven by shell; found "
        f"forbidden source files: {offenders}"
    )


def test_03_shell_scripts_do_not_use_text_processing_tools():
    offenders = []
    for root, dirs, files in os.walk(PROJECT_DIR):
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules"}]
        for name in files:
            if not name.endswith(".sh"):
                continue
            path = os.path.join(root, name)
            try:
                content = open(path, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            for word in FORBIDDEN_WORDS:
                if re.search(r"\b" + re.escape(word) + r"\b", content):
                    offenders.append((path, word))
    assert not offenders, (
        "Shell scripts must not use external text-processing tools; found: " f"{offenders}"
    )


# --------------------------------------------------------------------------- #
# 2. Migrations and schema shape
# --------------------------------------------------------------------------- #
def test_04_new_migration_created(gel_server):
    migrations = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql")))
    assert len(migrations) >= 2, (
        "Expected the schema change to be delivered as a new migration file in "
        f"{MIGRATIONS_DIR}; found {migrations}"
    )
    status = _run_gel(["migration", "status"], timeout=180)
    assert status.returncode == 0, (
        "`gel migration status` reports the migration history is not in sync "
        f"(rc={status.returncode}): {status.stdout}\n{status.stderr}"
    )
    applied = query_json("select count(schema::Migration)")[0]
    assert applied >= 2, f"Expected at least 2 applied migrations in the database, got {applied}."


def test_05_schema_shape(gel_server):
    expected = {
        "default::CleanProduct": {
            "source_id",
            "slug",
            "display_name",
            "sku",
            "sku_prefix",
            "sku_serial",
            "contact_redacted",
            "phones",
            "emails",
            "tag_summary",
            "tags",
        },
        "default::RejectedProduct": {"source_id", "reason"},
        "default::Tag": {"name"},
    }
    rows = query_json(
        "select schema::ObjectType { name, pointers: { name } } "
        "filter .name in {'default::CleanProduct', 'default::RejectedProduct', 'default::Tag'}"
    )
    found = {row["name"]: {p["name"] for p in row["pointers"]} for row in rows}
    for type_name, pointers in expected.items():
        assert type_name in found, f"Object type {type_name} is missing from the schema."
        missing = pointers - found[type_name]
        assert not missing, f"Object type {type_name} is missing pointers: {sorted(missing)}"


# --------------------------------------------------------------------------- #
# 3. Baseline state produced by the executor
# --------------------------------------------------------------------------- #
def test_06_check_mode_reports_clean_baseline(gel_server, seeded_raw_rows):
    if os.path.exists(REPORT_JSON):
        os.remove(REPORT_JSON)
    proc = run_normalize(["--check"])
    assert proc.returncode == 0, (
        "`normalize.sh --check` must exit 0 when the stored state is already normalized "
        f"(rc={proc.returncode}); stderr: {proc.stderr[-3000:]}"
    )
    report = parse_report(proc.stdout)
    assert isinstance(report, dict), f"The report must be a JSON object, got {type(report)}."
    assert set(report.keys()) == REPORT_KEYS, (
        f"Report top-level keys must be exactly {sorted(REPORT_KEYS)}, got {sorted(report.keys())}."
    )
    expected = reference_report(list(seeded_raw_rows.values()))
    assert report == expected, (
        "The report does not match the reference normalization of the seeded rows.\n"
        f"expected: {json.dumps(expected, ensure_ascii=False, sort_keys=True)}\n"
        f"actual:   {json.dumps(report, ensure_ascii=False, sort_keys=True)}"
    )
    assert os.path.isfile(REPORT_JSON), f"`--check` must also write the report to {REPORT_JSON}."
    on_disk = json.loads(open(REPORT_JSON, encoding="utf-8").read())
    assert on_disk == report, (
        f"{REPORT_JSON} must parse to the same JSON value that is printed on stdout."
    )


def test_07_stored_clean_products_match_reference(gel_server, seeded_raw_rows):
    products, rejected, _tag_counts = reference_state(list(seeded_raw_rows.values()))
    stored = fetch_clean_products()
    assert set(stored.keys()) == set(products.keys()), (
        "The set of CleanProduct source_ids does not match the reference.\n"
        f"expected: {sorted(products)}\nactual:   {sorted(stored)}"
    )
    for source_id, expected in products.items():
        row = stored[source_id]
        for field in (
            "slug",
            "display_name",
            "sku",
            "sku_prefix",
            "sku_serial",
            "contact_redacted",
            "tag_summary",
        ):
            assert row[field] == expected[field], (
                f"CleanProduct {source_id}: {field} is {row[field]!r}, expected {expected[field]!r}."
            )
        assert list(row["phones"]) == expected["phones"], (
            f"CleanProduct {source_id}: phones is {row['phones']!r}, expected {expected['phones']!r}."
        )
        assert list(row["emails"]) == expected["emails"], (
            f"CleanProduct {source_id}: emails is {row['emails']!r}, expected {expected['emails']!r}."
        )
        linked = sorted(t["name"] for t in row["tags"])
        assert linked == expected["tags"], (
            f"CleanProduct {source_id}: linked tags are {linked}, expected {expected['tags']}."
        )
    stored_rejected = fetch_rejected()
    assert stored_rejected == rejected, (
        f"RejectedProduct rows do not match the reference.\nexpected: {rejected}\nactual:   {stored_rejected}"
    )


def test_08_specific_seeded_expectations(gel_server):
    stored = fetch_clean_products()
    rejected = fetch_rejected()

    r1 = stored.get("R-0001")
    assert r1 is not None, "R-0001 must be an accepted CleanProduct."
    assert r1["slug"] == "espresso-machine-deluxe", f"R-0001 slug is {r1['slug']!r}."
    assert r1["display_name"] == "Espresso Machine Deluxe", (
        f"R-0001 display_name is {r1['display_name']!r}."
    )
    assert r1["sku"] == "ESP-0042-A1", f"R-0001 sku is {r1['sku']!r}."
    assert r1["sku_prefix"] == "ESP", f"R-0001 sku_prefix is {r1['sku_prefix']!r}."
    assert r1["sku_serial"] == 42, f"R-0001 sku_serial is {r1['sku_serial']!r}."
    assert list(r1["emails"]) == ["sales@example.com"], f"R-0001 emails is {r1['emails']!r}."
    assert list(r1["phones"]) == ["+1-555-0142"], f"R-0001 phones is {r1['phones']!r}."
    assert r1["contact_redacted"] == "[EMAIL] or +1-555-XXXX", (
        f"R-0001 contact_redacted is {r1['contact_redacted']!r}."
    )
    assert r1["tag_summary"] == "coffee,kitchen", f"R-0001 tag_summary is {r1['tag_summary']!r}."

    r2 = stored.get("R-0002")
    assert r2 is not None, "R-0002 must be an accepted CleanProduct."
    assert r2["display_name"] == "Grinder Pro-Max", (
        f"R-0002 display_name is {r2['display_name']!r}, expected title-cased 'Grinder Pro-Max'."
    )

    r10 = stored.get("R-0010")
    assert r10 is not None, "R-0010 must be an accepted CleanProduct."
    assert r10["sku_serial"] == 0, f"R-0010 sku_serial is {r10['sku_serial']!r}, expected 0."

    assert rejected.get("R-0004") == "EMPTY_SLUG", (
        f"R-0004 (emoji-only name) must be rejected with EMPTY_SLUG, got {rejected.get('R-0004')!r}."
    )
    assert rejected.get("R-0013") == "EMPTY_SLUG", (
        f"R-0013 (whitespace-only name) must be rejected with EMPTY_SLUG, got {rejected.get('R-0013')!r}."
    )
    assert rejected.get("R-0005") == "BAD_SKU", (
        f"R-0005 must be rejected with BAD_SKU, got {rejected.get('R-0005')!r}."
    )
    assert rejected.get("R-0007") == "DUPLICATE_SKU", (
        f"R-0007 must be rejected with DUPLICATE_SKU, got {rejected.get('R-0007')!r}."
    )
    assert "R-0006" in stored, "R-0006 wins the duplicate SKU partition and must be accepted."
    assert stored["R-0008"]["slug"] == "widget-basic", (
        f"R-0008 slug is {stored['R-0008']['slug']!r}, expected 'widget-basic'."
    )
    assert stored["R-0009"]["slug"] == "widget-basic-2", (
        f"R-0009 slug is {stored['R-0009']['slug']!r}, expected 'widget-basic-2'."
    )
    r14 = stored.get("R-0014")
    assert r14 is not None, "R-0014 must be an accepted CleanProduct."
    assert sorted(t["name"] for t in r14["tags"]) == ["test", "unicode"], (
        f"R-0014 must drop the non-ASCII tag token, got {[t['name'] for t in r14['tags']]}."
    )
    assert list(r14["emails"]) == [], (
        f"R-0014 has no ASCII e-mail local part and must yield no emails, got {r14['emails']!r}."
    )


def test_09_tag_graph_is_consistent(gel_server, seeded_raw_rows):
    _products, _rejected, tag_counts = reference_state(list(seeded_raw_rows.values()))
    tags = fetch_tags()
    assert set(tags.keys()) == set(tag_counts.keys()), (
        f"Tag objects do not match the reference.\nexpected: {sorted(tag_counts)}\nactual:   {sorted(tags)}"
    )
    for name, count in tag_counts.items():
        assert tags[name]["products"] == count, (
            f"Tag {name!r} is linked by {tags[name]['products']} products, expected {count}."
        )
    orphans = [name for name, row in tags.items() if row["products"] == 0]
    assert not orphans, f"These Tag objects are not linked by any CleanProduct: {orphans}"
    for row in fetch_clean_products().values():
        expected_summary = ",".join(sorted(t["name"] for t in row["tags"]))
        assert row["tag_summary"] == expected_summary, (
            f"CleanProduct {row['source_id']}: tag_summary {row['tag_summary']!r} does not match "
            f"its linked tags {expected_summary!r}."
        )


# --------------------------------------------------------------------------- #
# 4. Schema-level constraints
# --------------------------------------------------------------------------- #
def _cleanup_constraint_probes():
    query_raw("delete CleanProduct filter .source_id like 'ZZZ-CHECK%'")
    query_raw("delete RejectedProduct filter .source_id like 'ZZZ-CHECK%'")
    query_raw("delete Tag filter .name = 'Bad Name'")


def _insert_clean_product(source_id, slug, sku):
    return query_raw(
        "insert CleanProduct { "
        f"source_id := {eq_str(source_id)}, "
        f"slug := {eq_str(slug)}, "
        "display_name := 'Probe', "
        f"sku := {eq_str(sku)}, "
        "sku_prefix := 'ZZZ', "
        "sku_serial := 1, "
        "contact_redacted := '', "
        "phones := <array<str>>[], "
        "emails := <array<str>>[], "
        "tag_summary := '' }"
    )


def test_10_schema_rejects_invalid_values(gel_server):
    try:
        probes = [
            ("Tag with an invalid name", query_raw("insert Tag { name := 'Bad Name' }")),
            (
                "CleanProduct with an invalid slug",
                _insert_clean_product("ZZZ-CHECK-1", "Not A Slug", "ZZZ-0001-01"),
            ),
            (
                "CleanProduct with an invalid sku",
                _insert_clean_product("ZZZ-CHECK-2", "zzz-check-2", "esp-0042-a1"),
            ),
            (
                "RejectedProduct with an invalid reason",
                query_raw(
                    "insert RejectedProduct { source_id := 'ZZZ-CHECK-3', reason := 'WHATEVER' }"
                ),
            ),
        ]
        for label, proc in probes:
            assert proc.returncode != 0, (
                f"{label} was accepted by the database; a schema constraint must reject it."
            )
            assert "ConstraintViolationError" in (proc.stderr + proc.stdout), (
                f"{label} failed, but not with a ConstraintViolationError: "
                f"{(proc.stderr + proc.stdout)[-1500:]}"
            )
    finally:
        _cleanup_constraint_probes()


def test_11_schema_accepts_legitimate_values(gel_server):
    try:
        proc = _insert_clean_product("ZZZ-CHECK-4", "zzz-check-4", "ZZZ-0001-01")
        assert proc.returncode == 0, (
            "A CleanProduct with a valid slug and sku must be insertable; the constraints are "
            f"too strict: {(proc.stderr + proc.stdout)[-1500:]}"
        )
        proc = query_raw("insert Tag { name := 'zz' }")
        assert proc.returncode == 0, (
            f"A two-character lowercase Tag name must be accepted: {(proc.stderr + proc.stdout)[-1500:]}"
        )
    finally:
        query_raw("delete CleanProduct filter .source_id like 'ZZZ-CHECK%'")
        query_raw("delete Tag filter .name = 'zz'")


# --------------------------------------------------------------------------- #
# 5. Idempotency, repair, drift detection
# --------------------------------------------------------------------------- #
def test_12_rerun_is_idempotent_and_preserves_ids(gel_server):
    before_products = {sid: row["id"] for sid, row in fetch_clean_products().items()}
    before_tags = {name: row["id"] for name, row in fetch_tags().items()}
    first = run_normalize(["--check"])
    assert first.returncode == 0, f"`--check` must exit 0 here: {first.stderr[-2000:]}"
    baseline_report = parse_report(first.stdout)

    proc = run_normalize()
    assert proc.returncode == 0, (
        f"A full run must exit 0 (rc={proc.returncode}): {proc.stderr[-3000:]}"
    )
    rerun_report = parse_report(proc.stdout)
    assert rerun_report == baseline_report, (
        "Re-running the pipeline over unchanged input must produce an identical report.\n"
        f"before: {json.dumps(baseline_report, ensure_ascii=False, sort_keys=True)}\n"
        f"after:  {json.dumps(rerun_report, ensure_ascii=False, sort_keys=True)}"
    )

    after_products = {sid: row["id"] for sid, row in fetch_clean_products().items()}
    after_tags = {name: row["id"] for name, row in fetch_tags().items()}
    assert after_products == before_products, (
        "CleanProduct ids must be stable across a run that does not change their values; "
        f"changed: {sorted(set(before_products.items()) ^ set(after_products.items()))}"
    )
    assert after_tags == before_tags, (
        "Tag ids must be stable across a run that does not change them; "
        f"changed: {sorted(set(before_tags.items()) ^ set(after_tags.items()))}"
    )

    check = run_normalize(["--check"])
    assert check.returncode == 0, (
        f"`--check` must still exit 0 after an idempotent run: {check.stderr[-2000:]}"
    )


def test_13_tampered_row_is_detected_and_repaired(gel_server):
    original = fetch_clean_products()["R-0001"]
    query_json(
        "update CleanProduct filter .source_id = 'R-0001' "
        "set { slug := 'tampered-slug', tag_summary := 'zzz' }"
    )
    check = run_normalize(["--check"])
    assert check.returncode == 3, (
        f"`--check` must exit 3 when the stored state drifted, got {check.returncode}: "
        f"{check.stderr[-2000:]}"
    )
    still_tampered = fetch_clean_products()["R-0001"]
    assert still_tampered["slug"] == "tampered-slug", (
        "`--check` must not modify the database, but the tampered slug was repaired."
    )
    parse_report(check.stdout)

    proc = run_normalize()
    assert proc.returncode == 0, f"The repairing run must exit 0: {proc.stderr[-3000:]}"
    repaired = fetch_clean_products()["R-0001"]
    assert repaired["slug"] == original["slug"], (
        f"R-0001 slug was not repaired: {repaired['slug']!r} != {original['slug']!r}."
    )
    assert repaired["tag_summary"] == original["tag_summary"], (
        f"R-0001 tag_summary was not repaired: {repaired['tag_summary']!r}."
    )
    assert repaired["id"] == original["id"], (
        "Repairing a row must update it in place, keeping the object id stable."
    )
    check = run_normalize(["--check"])
    assert check.returncode == 0, f"`--check` must exit 0 after the repair: {check.stderr[-2000:]}"


def test_14_orphan_tag_is_detected_and_removed(gel_server):
    query_json("insert Tag { name := 'zzorphan' }")
    check = run_normalize(["--check"])
    assert check.returncode == 3, (
        f"`--check` must exit 3 when an unlinked Tag exists, got {check.returncode}."
    )
    assert "zzorphan" in fetch_tags(), "`--check` must not modify the database."

    proc = run_normalize()
    assert proc.returncode == 0, f"The repairing run must exit 0: {proc.stderr[-3000:]}"
    assert "zzorphan" not in fetch_tags(), (
        "A Tag that is not linked by any CleanProduct must be deleted by a full run."
    )
    check = run_normalize(["--check"])
    assert check.returncode == 0, f"`--check` must exit 0 after cleanup: {check.stderr[-2000:]}"


def test_15_deleted_output_rows_are_recreated(gel_server):
    query_json("delete CleanProduct filter .source_id = 'R-0011'")
    query_json("delete RejectedProduct filter .source_id = 'R-0005'")
    check = run_normalize(["--check"])
    assert check.returncode == 3, (
        f"`--check` must exit 3 when derived rows are missing, got {check.returncode}."
    )
    proc = run_normalize()
    assert proc.returncode == 0, f"The repairing run must exit 0: {proc.stderr[-3000:]}"
    assert "R-0011" in fetch_clean_products(), "The deleted CleanProduct R-0011 must be recreated."
    assert fetch_rejected().get("R-0005") == "BAD_SKU", (
        "The deleted RejectedProduct R-0005 must be recreated with reason BAD_SKU."
    )
    check = run_normalize(["--check"])
    assert check.returncode == 0, f"`--check` must exit 0 after the repair: {check.stderr[-2000:]}"


# --------------------------------------------------------------------------- #
# 6. Unseen data
# --------------------------------------------------------------------------- #
NEW_RAW_ROWS = [
    {
        "source_id": "R-9001",
        "raw_name": "PRESSURE GAUGE",
        "raw_sku": "prs-0303-9z",
        "raw_contact": "b@c.io +1-555-1111",
        "raw_tags": "gauge,tools",
    },
    {
        "source_id": "R-9002",
        "raw_name": "espresso machine deluxe",
        "raw_sku": "esp-0043-a2",
        "raw_contact": "",
        "raw_tags": "coffee",
    },
    {
        "source_id": "R-9003",
        "raw_name": "Zero   Serial",
        "raw_sku": "zer-0000-00",
        "raw_contact": "   ",
        "raw_tags": "",
    },
    {
        "source_id": "R-9004",
        "raw_name": "Bad Sku Row",
        "raw_sku": "!!!",
        "raw_contact": "",
        "raw_tags": "misc",
    },
    {
        "source_id": "R-9005",
        "raw_name": "###",
        "raw_sku": "hsh-0001-aa",
        "raw_contact": "",
        "raw_tags": "x",
    },
    {
        "source_id": "R-9006",
        "raw_name": "Ünïcödé 🚀 Sample",
        "raw_sku": "smp-0202-b2",
        "raw_contact": "USER@Example.org, user@example.org, +33-111-2222",
        "raw_tags": "sample;;unicode",
    },
    {
        "source_id": "R-9007",
        "raw_name": "ALREADY CLEAN NAME",
        "raw_sku": "acn-0009-zz",
        "raw_contact": "",
        "raw_tags": "clean",
    },
    {
        "source_id": "R-0000",
        "raw_name": "Espresso Machine Deluxe Prime",
        "raw_sku": "ESP-0042-A1",
        "raw_contact": "",
        "raw_tags": "coffee,prime",
    },
]


def test_16_pipeline_handles_new_rows(gel_server):
    for row in NEW_RAW_ROWS:
        query_json(
            "insert RawProduct { "
            f"source_id := {eq_str(row['source_id'])}, "
            f"raw_name := {eq_str(row['raw_name'])}, "
            f"raw_sku := {eq_str(row['raw_sku'])}, "
            f"raw_contact := {eq_str(row['raw_contact'])}, "
            f"raw_tags := {eq_str(row['raw_tags'])} }}"
        )

    check = run_normalize(["--check"])
    assert check.returncode == 3, (
        f"`--check` must exit 3 once new RawProduct rows arrived, got {check.returncode}."
    )

    proc = run_normalize()
    assert proc.returncode == 0, f"The full run must exit 0: {proc.stderr[-3000:]}"
    report = parse_report(proc.stdout)

    raw_rows = fetch_raw_rows()
    expected_report = reference_report(raw_rows)
    assert report == expected_report, (
        "The report after ingesting new rows does not match the reference.\n"
        f"expected: {json.dumps(expected_report, ensure_ascii=False, sort_keys=True)}\n"
        f"actual:   {json.dumps(report, ensure_ascii=False, sort_keys=True)}"
    )

    products, rejected, tag_counts = reference_state(raw_rows)
    stored = fetch_clean_products()
    assert set(stored.keys()) == set(products.keys()), (
        f"CleanProduct set mismatch.\nexpected: {sorted(products)}\nactual:   {sorted(stored)}"
    )
    for source_id, expected in products.items():
        row = stored[source_id]
        for field in (
            "slug",
            "display_name",
            "sku",
            "sku_prefix",
            "sku_serial",
            "contact_redacted",
            "tag_summary",
        ):
            assert row[field] == expected[field], (
                f"CleanProduct {source_id}: {field} is {row[field]!r}, expected {expected[field]!r}."
            )
        assert list(row["phones"]) == expected["phones"], (
            f"CleanProduct {source_id}: phones is {row['phones']!r}, expected {expected['phones']!r}."
        )
        assert list(row["emails"]) == expected["emails"], (
            f"CleanProduct {source_id}: emails is {row['emails']!r}, expected {expected['emails']!r}."
        )
        assert sorted(t["name"] for t in row["tags"]) == expected["tags"], (
            f"CleanProduct {source_id}: linked tags mismatch."
        )
    assert fetch_rejected() == rejected, (
        f"RejectedProduct mismatch.\nexpected: {rejected}\nactual:   {fetch_rejected()}"
    )
    assert set(fetch_tags().keys()) == set(tag_counts.keys()), (
        f"Tag mismatch.\nexpected: {sorted(tag_counts)}\nactual:   {sorted(fetch_tags())}"
    )


def test_17_reranking_cases_are_correct(gel_server):
    stored = fetch_clean_products()
    rejected = fetch_rejected()

    assert rejected.get("R-0001") == "DUPLICATE_SKU", (
        "R-0000 shares R-0001's canonical SKU and has the smaller source_id, so R-0001 must "
        f"now be rejected with DUPLICATE_SKU; got {rejected.get('R-0001')!r}."
    )
    assert "R-0001" not in stored, "R-0001 must no longer have a CleanProduct."
    assert "R-0000" in stored, "R-0000 must now be the accepted record for SKU ESP-0042-A1."
    assert stored["R-9002"]["slug"] == "espresso-machine-deluxe", (
        "With R-0001 rejected, R-9002 is the only record with that base slug and must own it; "
        f"got {stored['R-9002']['slug']!r}."
    )
    assert stored["R-0010"]["slug"] == "already-clean-name", (
        f"R-0010 keeps the base slug; got {stored['R-0010']['slug']!r}."
    )
    assert stored["R-9007"]["slug"] == "already-clean-name-2", (
        f"R-9007 must be de-collided to 'already-clean-name-2'; got {stored['R-9007']['slug']!r}."
    )
    assert stored["R-9007"]["display_name"] == "Already Clean Name", (
        f"R-9007 must be title-cased; got {stored['R-9007']['display_name']!r}."
    )
    assert stored["R-9003"]["sku_serial"] == 0, (
        f"R-9003 must have sku_serial 0; got {stored['R-9003']['sku_serial']!r}."
    )
    assert stored["R-9003"]["tag_summary"] == "", (
        f"R-9003 has no tags and must have an empty tag_summary; got {stored['R-9003']['tag_summary']!r}."
    )
    assert list(stored["R-9006"]["emails"]) == ["user@example.org"], (
        f"R-9006 must fold the two e-mail spellings into one entry; got {stored['R-9006']['emails']!r}."
    )
    assert stored["R-9006"]["contact_redacted"] == "[EMAIL], [EMAIL], +33-111-XXXX", (
        f"R-9006 contact_redacted is {stored['R-9006']['contact_redacted']!r}."
    )
    assert rejected.get("R-9004") == "BAD_SKU", (
        f"R-9004 must be rejected with BAD_SKU; got {rejected.get('R-9004')!r}."
    )
    assert rejected.get("R-9005") == "EMPTY_SLUG", (
        f"R-9005 must be rejected with EMPTY_SLUG; got {rejected.get('R-9005')!r}."
    )
    assert "two" not in fetch_tags(), (
        "Tags that only occur on rejected records must not exist as Tag objects."
    )


def test_18_repeated_runs_remain_stable(gel_server):
    check = run_normalize(["--check"])
    assert check.returncode == 0, f"`--check` must exit 0 after the full run: {check.stderr[-2000:]}"
    first = parse_report(check.stdout)
    second = run_normalize()
    assert second.returncode == 0, f"A repeated full run must exit 0: {second.stderr[-3000:]}"
    third = run_normalize()
    assert third.returncode == 0, f"A repeated full run must exit 0: {third.stderr[-3000:]}"
    assert parse_report(second.stdout) == first, "Report changed on the second consecutive run."
    assert parse_report(third.stdout) == first, "Report changed on the third consecutive run."
    expected = reference_report(fetch_raw_rows())
    assert first == expected, "The stable report no longer matches the reference."


# --------------------------------------------------------------------------- #
# 7. CLI contract and raw-data immutability
# --------------------------------------------------------------------------- #
def test_19_invalid_argument_is_rejected(gel_server):
    before_clean = len(fetch_clean_products())
    before_rejected = len(fetch_rejected())
    proc = run_normalize(["--bogus"], timeout=300)
    assert proc.returncode == 2, (
        f"An unknown argument must exit with code 2, got {proc.returncode}."
    )
    assert proc.stdout.strip() == "", (
        f"An unknown argument must print nothing on stdout, got {proc.stdout[:500]!r}."
    )
    assert proc.stderr.strip() != "", "An unknown argument must print a diagnostic on stderr."
    assert len(fetch_clean_products()) == before_clean, "An invalid invocation must not mutate data."
    assert len(fetch_rejected()) == before_rejected, "An invalid invocation must not mutate data."


def test_20_raw_data_untouched(gel_server, seeded_raw_rows):
    current = {row["source_id"]: row for row in fetch_raw_rows()}
    for source_id, original in seeded_raw_rows.items():
        assert source_id in current, f"Seeded RawProduct {source_id} was deleted."
        for field in ("raw_name", "raw_sku", "raw_contact", "raw_tags"):
            assert current[source_id][field] == original[field], (
                f"RawProduct {source_id}.{field} was modified by the pipeline: "
                f"{current[source_id][field]!r} != {original[field]!r}."
            )
