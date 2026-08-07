"""Final-state verification for the gel_migration_history_repair_cli task.

Everything is checked against the *running* local Gel server through the `gel`
CLI (schema introspection, live data, migration history, branch replay) plus the
report artifact the executor must write.

All expected values are derived either from live queries or from the build-time
snapshot at /opt/harbor/initial_state.json -- never hardcoded.
"""

import glob
import json
import os
import re
import subprocess
import time

import pytest

PROJECT_DIR = "/home/user/inventory"
SCHEMA_DIR = os.path.join(PROJECT_DIR, "dbschema")
MIGRATIONS_DIR = os.path.join(SCHEMA_DIR, "migrations")
REPORT_PATH = os.path.join(PROJECT_DIR, "repair_report.json")
SNAPSHOT_PATH = "/opt/harbor/initial_state.json"
START_SCRIPT = "/usr/local/bin/gel-start.sh"
REPLAY_BRANCH = "replay_check"

MIGRATION_HEADER_RE = re.compile(r"CREATE\s+MIGRATION\s+([A-Za-z0-9_]+)", re.IGNORECASE)
REPORT_KEYS = {
    "history_length",
    "revisions",
    "recovered_revisions",
    "ddl_revision",
    "new_revision",
}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _run_gel(args, branch="main", timeout=300):
    env = dict(os.environ)
    env["GEL_BRANCH"] = branch
    env.setdefault("GEL_HOST", "127.0.0.1")
    env.setdefault("GEL_PORT", "5656")
    env.setdefault("GEL_USER", "admin")
    env.setdefault("GEL_CLIENT_TLS_SECURITY", "insecure")
    env.setdefault("GEL_RUN_VERSION_CHECK", "never")
    return subprocess.run(
        ["gel"] + args,
        cwd=PROJECT_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _query_json(query, branch="main"):
    proc = _run_gel(["query", "-F", "json", query], branch=branch)
    assert proc.returncode == 0, (
        f"EdgeQL query failed with exit code {proc.returncode}.\nquery: {query}\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    return json.loads(proc.stdout)


def _query_single(query, branch="main"):
    rows = _query_json(query, branch=branch)
    assert len(rows) == 1, f"Expected exactly one result for `{query}`, got {rows}."
    return rows[0]


def _query_expect_failure(query, branch="main"):
    """Run a mutation that MUST be rejected by the database."""
    return _run_gel(["query", query], branch=branch)


def _server_ready():
    try:
        proc = _run_gel(["query", "-F", "json", "select 1"], timeout=60)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0


def _db_migrations(branch="main"):
    return _query_json(
        "select schema::Migration { name, gb := <str>.generated_by } filter not .builtin",
        branch=branch,
    )


def _fs_history():
    files = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql")))
    assert files, f"No migration files found in {MIGRATIONS_DIR}."
    revisions = []
    for path in files:
        with open(path) as fh:
            match = MIGRATION_HEADER_RE.search(fh.read())
        assert match is not None, f"No `CREATE MIGRATION <id>` header found in {path}."
        revisions.append(match.group(1))
    return files, revisions


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
@pytest.fixture(scope="session")
def gel_server():
    """Ensure the local Gel server is running before any CLI/query based check."""
    if not _server_ready():
        if os.path.isfile(START_SCRIPT):
            proc = subprocess.run(
                [START_SCRIPT], capture_output=True, text=True, timeout=300, check=False
            )
            print("gel-start.sh stdout:", proc.stdout)
            print("gel-start.sh stderr:", proc.stderr)
        deadline = time.time() + 240
        while time.time() < deadline:
            if _server_ready():
                break
            time.sleep(3)
        else:
            pytest.fail("Local Gel server never became reachable on 127.0.0.1:5656.")
    return True


@pytest.fixture(scope="session")
def snapshot():
    assert os.path.isfile(
        SNAPSHOT_PATH
    ), f"Missing build-time snapshot {SNAPSHOT_PATH}; the image is broken."
    with open(SNAPSHOT_PATH) as fh:
        return json.load(fh)


@pytest.fixture(scope="session")
def report():
    assert os.path.isfile(REPORT_PATH), f"Report file {REPORT_PATH} was not created."
    with open(REPORT_PATH) as fh:
        try:
            data = json.load(fh)
        except json.JSONDecodeError as exc:
            pytest.fail(f"{REPORT_PATH} is not valid JSON: {exc}")
    assert isinstance(data, dict), f"{REPORT_PATH} must contain a JSON object."
    return data


# --------------------------------------------------------------------------
# 1. migration status
# --------------------------------------------------------------------------
def test_migration_status_is_in_sync(gel_server):
    proc = _run_gel(["migration", "status"])
    assert proc.returncode == 0, (
        "`gel migration status` must exit 0 once the project is repaired, got "
        f"{proc.returncode}.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    combined = proc.stdout + proc.stderr
    assert "up to date" in combined, (
        "`gel migration status` should report that the database is up to date, got:\n"
        f"{combined}"
    )


# --------------------------------------------------------------------------
# 2-3. history integrity
# --------------------------------------------------------------------------
def test_filesystem_history_matches_database_history(gel_server):
    files, fs_revisions = _fs_history()
    db_rows = _db_migrations()
    db_names = {row["name"] for row in db_rows}

    assert set(fs_revisions) == db_names, (
        "The revisions in dbschema/migrations must be exactly the revisions recorded "
        f"in the database.\non disk: {sorted(fs_revisions)}\nin database: {sorted(db_names)}"
    )
    indices = [int(os.path.basename(path)[:5]) for path in files]
    assert indices == list(range(1, len(files) + 1)), (
        "Migration files must be numbered contiguously starting at 00001, got "
        f"{[os.path.basename(p) for p in files]}."
    )
    tip = _query_json(
        "select schema::Migration { name } "
        "filter not .builtin and not exists .<parents[is schema::Migration]"
    )
    assert len(tip) == 1, f"Expected a single head revision in the database, got {tip}."
    assert tip[0]["name"] == fs_revisions[-1], (
        "The newest revision recorded in the database must be the last migration file "
        f"on disk. database head: {tip[0]['name']}, last file: {fs_revisions[-1]}"
    )


def test_history_grew_by_exactly_one_revision(gel_server, snapshot):
    files, fs_revisions = _fs_history()
    db_rows = _db_migrations()
    expected = len(snapshot["db_revisions"]) + 1
    assert len(db_rows) == expected, (
        f"The database should record {expected} revisions (the {len(snapshot['db_revisions'])} "
        f"pre-existing ones plus exactly one new one), got {len(db_rows)}."
    )
    assert len(files) == expected, (
        f"dbschema/migrations should hold {expected} migration files, got {len(files)}: "
        f"{[os.path.basename(p) for p in files]}"
    )
    assert len(set(fs_revisions)) == expected, "Duplicate revision ids found on disk."
    pre_existing = {row["name"] for row in snapshot["db_revisions"]}
    assert pre_existing <= set(fs_revisions), (
        "Every pre-existing database revision must still be part of the history; missing: "
        f"{sorted(pre_existing - set(fs_revisions))}"
    )


# --------------------------------------------------------------------------
# 4-9. report artifact
# --------------------------------------------------------------------------
def test_report_file_has_expected_shape(report):
    assert set(report) == REPORT_KEYS, (
        f"{REPORT_PATH} must contain exactly the keys {sorted(REPORT_KEYS)}, got "
        f"{sorted(report)}."
    )
    assert isinstance(report["history_length"], int) and not isinstance(
        report["history_length"], bool
    ), "`history_length` must be an integer."
    for key in ("revisions", "recovered_revisions"):
        assert isinstance(report[key], list) and all(
            isinstance(item, str) for item in report[key]
        ), f"`{key}` must be a list of strings."
    for key in ("ddl_revision", "new_revision"):
        assert isinstance(report[key], str) and report[key], f"`{key}` must be a non-empty string."


def test_report_revisions_match_filesystem_history(gel_server, report):
    _, fs_revisions = _fs_history()
    assert report["revisions"] == fs_revisions, (
        "`revisions` must list every revision of the repaired history oldest first.\n"
        f"report: {report['revisions']}\nfilesystem: {fs_revisions}"
    )
    assert report["history_length"] == len(fs_revisions), (
        f"`history_length` must be {len(fs_revisions)}, got {report['history_length']}."
    )


def test_report_recovered_revisions_are_the_missing_ones(report, snapshot):
    initially_on_disk = set(snapshot["fs_revisions"])
    initially_in_db = [row["name"] for row in snapshot["db_revisions"]]
    missing = set(initially_in_db) - initially_on_disk
    expected = [rev for rev in report["revisions"] if rev in missing]
    assert len(expected) == len(missing) == 2, (
        "Sanity check on the baked snapshot failed: expected two revisions to be missing "
        f"from disk initially, resolved {sorted(missing)} / {expected}."
    )
    assert report["recovered_revisions"] == expected, (
        "`recovered_revisions` must be exactly the revisions that were recorded in the "
        f"database but absent from dbschema/migrations, oldest first: {expected}, got "
        f"{report['recovered_revisions']}."
    )


def test_report_ddl_revision_is_the_bare_ddl_one(gel_server, report, snapshot):
    _, fs_revisions = _fs_history()
    assert report["ddl_revision"] == snapshot["ddl_revision"], (
        "`ddl_revision` must be the revision that the database recorded as produced by "
        f"bare DDL, got {report['ddl_revision']}."
    )
    assert report["ddl_revision"] in fs_revisions, (
        "The bare-DDL revision must now also exist as a file in dbschema/migrations."
    )
    rows = _query_json(
        "select schema::Migration { name, gb := <str>.generated_by } "
        f"filter .name = '{report['ddl_revision']}'"
    )
    assert len(rows) == 1, f"Revision {report['ddl_revision']} is not recorded in the database."
    assert rows[0]["gb"] == "DDLStatement", (
        "The reported ddl_revision must still be recorded with generated_by=DDLStatement, got "
        f"{rows[0]['gb']}."
    )


def test_exactly_one_bare_ddl_revision_remains(gel_server):
    count = _query_single(
        "select count((select schema::Migration filter not .builtin "
        "and .generated_by = schema::MigrationGeneratedBy.DDLStatement))"
    )
    assert count == 1, (
        "Exactly one revision produced by bare DDL may exist in branch main "
        f"(the pre-existing one); found {count}."
    )


def test_report_new_revision_introduces_reorder_rule(gel_server, report):
    _, fs_revisions = _fs_history()
    assert report["new_revision"] == fs_revisions[-1], (
        "`new_revision` must be the newest revision of the history "
        f"({fs_revisions[-1]}), got {report['new_revision']}."
    )
    assert report["new_revision"] != report["ddl_revision"], (
        "`new_revision` must not be the pre-existing bare-DDL revision."
    )
    rows = _query_json(
        "select schema::Migration { name, script } "
        f"filter .name = '{report['new_revision']}'"
    )
    assert len(rows) == 1, f"Revision {report['new_revision']} is not recorded in the database."
    assert "ReorderRule" in rows[0]["script"], (
        "The new revision recorded in the database must be the one that introduces "
        f"ReorderRule; its script is:\n{rows[0]['script']}"
    )


def test_sdl_describes_recovered_and_new_objects():
    sdl = ""
    for path in sorted(glob.glob(os.path.join(SCHEMA_DIR, "*.gel"))):
        with open(path) as fh:
            sdl += fh.read()
    for needle in ("AuditEvent", "seq", "ReorderRule"):
        assert needle in sdl, (
            f"The SDL in dbschema/ must describe `{needle}` so that the schema source "
            "matches the database."
        )


# --------------------------------------------------------------------------
# 11-12. schema shape
# --------------------------------------------------------------------------
def _introspect(type_names, branch="main"):
    names = ", ".join(f"'{name}'" for name in type_names)
    rows = _query_json(
        "select schema::ObjectType { name, pointers: { name, required, "
        "card := <str>.cardinality, kind := .__type__.name, "
        "target_name := .target.name } } "
        f"filter .name in {{{names}}}",
        branch=branch,
    )
    return {row["name"]: {p["name"]: p for p in row["pointers"]} for row in rows}


def test_preexisting_schema_is_preserved(gel_server, snapshot):
    types = {
        "default::Warehouse": sorted(k for k in snapshot["warehouses"][0] if k != "id"),
        "default::Part": sorted(k for k in snapshot["parts"][0] if k != "id"),
        "default::AuditEvent": sorted(k for k in snapshot["audit_events"][0] if k != "id"),
        "default::StockLevel": ["part", "quantity", "warehouse"],
    }
    introspected = _introspect(list(types))
    for type_name, pointer_names in types.items():
        assert type_name in introspected, f"Object type {type_name} disappeared from the schema."
        pointers = introspected[type_name]
        for pointer_name in pointer_names:
            assert pointer_name in pointers, (
                f"{type_name} lost its `{pointer_name}` pointer; nothing that existed before "
                "may be dropped or renamed."
            )

    part = introspected["default::Part"]
    for required_name in ("sku", "description", "unit_price_cents"):
        assert part[required_name]["required"] is True, (
            f"default::Part.{required_name} must stay required."
        )

    audit = introspected["default::AuditEvent"]
    assert audit["event"]["required"] is True, "default::AuditEvent.event must stay required."
    assert audit["seq"]["required"] is True, "default::AuditEvent.seq must stay required."
    assert audit["seq"]["target_name"] == "std::int64", (
        f"default::AuditEvent.seq must stay an int64, got {audit['seq']['target_name']}."
    )
    assert audit["note"]["required"] is False, "default::AuditEvent.note must stay optional."

    stock = introspected["default::StockLevel"]
    for link_name in ("part", "warehouse"):
        assert stock[link_name]["kind"] == "schema::Link", (
            f"default::StockLevel.{link_name} must stay a link."
        )
        assert stock[link_name]["required"] is True, (
            f"default::StockLevel.{link_name} must stay required."
        )
        assert stock[link_name]["card"] == "One", (
            f"default::StockLevel.{link_name} must stay single, got {stock[link_name]['card']}."
        )
    assert stock["quantity"]["required"] is True, "default::StockLevel.quantity must stay required."

    warehouse = introspected["default::Warehouse"]
    for required_name in ("code", "name"):
        assert warehouse[required_name]["required"] is True, (
            f"default::Warehouse.{required_name} must stay required."
        )


def test_reorder_rule_schema_shape(gel_server):
    introspected = _introspect(["default::ReorderRule"])
    assert "default::ReorderRule" in introspected, "Object type default::ReorderRule is missing."
    pointers = introspected["default::ReorderRule"]

    for name in ("part", "min_quantity", "reorder_batch"):
        assert name in pointers, f"default::ReorderRule is missing `{name}`."

    part = pointers["part"]
    assert part["kind"] == "schema::Link", "default::ReorderRule.part must be a link."
    assert part["required"] is True, "default::ReorderRule.part must be required."
    assert part["card"] == "One", f"default::ReorderRule.part must be single, got {part['card']}."
    assert part["target_name"] == "default::Part", (
        f"default::ReorderRule.part must target default::Part, got {part['target_name']}."
    )

    for name in ("min_quantity", "reorder_batch"):
        pointer = pointers[name]
        assert pointer["kind"] == "schema::Property", (
            f"default::ReorderRule.{name} must be a property."
        )
        assert pointer["required"] is True, f"default::ReorderRule.{name} must be required."
        assert pointer["target_name"] == "std::int64", (
            f"default::ReorderRule.{name} must be an int64, got {pointer['target_name']}."
        )


# --------------------------------------------------------------------------
# 13-14. data preservation
# --------------------------------------------------------------------------
def test_seeded_warehouses_and_parts_are_untouched(gel_server, snapshot):
    warehouses = _query_json("select Warehouse { id, code, name } order by .code")
    parts = _query_json("select Part { id, sku, description, unit_price_cents } order by .sku")
    assert warehouses == snapshot["warehouses"], (
        "Warehouse objects must be preserved with identical ids and values.\n"
        f"live: {warehouses}\nexpected: {snapshot['warehouses']}"
    )
    assert parts == snapshot["parts"], (
        "Part objects must be preserved with identical ids and values.\n"
        f"live: {parts}\nexpected: {snapshot['parts']}"
    )


def test_seeded_stock_levels_and_audit_events_are_untouched(gel_server, snapshot):
    stock = _query_json(
        "select StockLevel { id, quantity, part_sku := .part.sku, "
        "warehouse_code := .warehouse.code } order by .part.sku then .warehouse.code"
    )
    audit = _query_json("select AuditEvent { id, event, note, seq } order by .seq")
    assert stock == snapshot["stock_levels"], (
        "StockLevel objects must be preserved with identical ids and values.\n"
        f"live: {stock}\nexpected: {snapshot['stock_levels']}"
    )
    assert audit == snapshot["audit_events"], (
        "AuditEvent objects must be preserved with identical ids and values.\n"
        f"live: {audit}\nexpected: {snapshot['audit_events']}"
    )


# --------------------------------------------------------------------------
# 15. backfill
# --------------------------------------------------------------------------
def test_reorder_rules_are_backfilled_from_live_stock_totals(gel_server):
    totals = _query_json(
        "select Part { sku, total := sum(.<part[is StockLevel].quantity) } order by .sku"
    )
    rules = _query_json(
        "select ReorderRule { part_sku := .part.sku, min_quantity, reorder_batch } "
        "order by .part.sku"
    )
    assert len(rules) == len(totals), (
        f"Expected exactly one ReorderRule per Part ({len(totals)}), got {len(rules)}."
    )
    by_sku = {}
    for rule in rules:
        assert rule["part_sku"] not in by_sku, (
            f"More than one ReorderRule links the part {rule['part_sku']}."
        )
        by_sku[rule["part_sku"]] = rule
    for part in totals:
        sku = part["sku"]
        assert sku in by_sku, f"Part {sku} has no ReorderRule."
        expected_min = int(part["total"]) // 2
        assert by_sku[sku]["min_quantity"] == expected_min, (
            f"ReorderRule for {sku} must have min_quantity {expected_min} "
            f"(live stock total {part['total']} // 2), got {by_sku[sku]['min_quantity']}."
        )
        assert by_sku[sku]["reorder_batch"] == 12, (
            f"ReorderRule for {sku} must have reorder_batch 12, got "
            f"{by_sku[sku]['reorder_batch']}."
        )


# --------------------------------------------------------------------------
# 16-19. constraints are really enforced
# --------------------------------------------------------------------------
def test_reorder_rule_part_is_exclusive(gel_server, snapshot):
    sku = snapshot["parts"][0]["sku"]
    proc = _query_expect_failure(
        "insert ReorderRule { "
        f"part := assert_exists((select Part filter .sku = '{sku}')), "
        "min_quantity := 1, reorder_batch := 1 }"
    )
    if proc.returncode == 0:
        _run_gel(["query", f"delete ReorderRule filter .part.sku = '{sku}' and .reorder_batch = 1"])
        pytest.fail(
            f"A second ReorderRule for part {sku} was accepted; `part` must carry an "
            "exclusive constraint."
        )
    assert "constraint" in (proc.stdout + proc.stderr).lower(), (
        "Inserting a duplicate ReorderRule should fail with a constraint violation, got:\n"
        f"{proc.stdout}\n{proc.stderr}"
    )


def test_reorder_rule_lower_bounds_are_enforced(gel_server, snapshot):
    sku = snapshot["parts"][0]["sku"]
    before = _query_json(
        f"select ReorderRule {{ min_quantity, reorder_batch }} filter .part.sku = '{sku}'"
    )
    assert len(before) == 1, f"Expected exactly one ReorderRule for {sku}, got {before}."

    bad_min = _query_expect_failure(
        f"update ReorderRule filter .part.sku = '{sku}' set {{ min_quantity := -1 }}"
    )
    assert bad_min.returncode != 0, (
        "Setting min_quantity to -1 must be rejected; the property must reject values below 0."
    )
    bad_batch = _query_expect_failure(
        f"update ReorderRule filter .part.sku = '{sku}' set {{ reorder_batch := 0 }}"
    )
    assert bad_batch.returncode != 0, (
        "Setting reorder_batch to 0 must be rejected; the property must reject values below 1."
    )
    after = _query_json(
        f"select ReorderRule {{ min_quantity, reorder_batch }} filter .part.sku = '{sku}'"
    )
    assert after == before, (
        f"Rejected updates must leave the ReorderRule for {sku} unchanged: {before} -> {after}."
    )


def test_recovered_part_sku_constraint_is_enforced(gel_server, snapshot):
    duplicate_sku = snapshot["parts"][0]["sku"]
    proc = _query_expect_failure(
        "insert Part { "
        f"sku := '{duplicate_sku}', description := 'dup sku probe', unit_price_cents := 1 }}"
    )
    if proc.returncode == 0:
        _run_gel(["query", "delete Part filter .description = 'dup sku probe'"])
        pytest.fail(f"A Part with the duplicate sku {duplicate_sku} was accepted.")
    assert "constraint" in (proc.stdout + proc.stderr).lower(), (
        "Duplicating a Part sku should fail with a constraint violation, got:\n"
        f"{proc.stdout}\n{proc.stderr}"
    )


def test_out_of_band_audit_event_constraint_is_enforced(gel_server, snapshot):
    duplicate_seq = snapshot["audit_events"][0]["seq"]
    proc = _query_expect_failure(
        "insert AuditEvent { event := 'dup seq probe', "
        f"seq := {duplicate_seq} }}"
    )
    if proc.returncode == 0:
        _run_gel(["query", "delete AuditEvent filter .event = 'dup seq probe'"])
        pytest.fail(
            f"An AuditEvent with the duplicate seq {duplicate_seq} was accepted; the exclusive "
            "constraint that only existed in the out-of-band revision was not preserved."
        )
    assert "constraint" in (proc.stdout + proc.stderr).lower(), (
        "Duplicating an AuditEvent seq should fail with a constraint violation, got:\n"
        f"{proc.stdout}\n{proc.stderr}"
    )


def test_stock_level_composite_constraint_is_enforced(gel_server, snapshot):
    existing = snapshot["stock_levels"][0]
    sku = existing["part_sku"]
    code = existing["warehouse_code"]
    proc = _query_expect_failure(
        "insert StockLevel { "
        f"part := assert_exists((select Part filter .sku = '{sku}')), "
        f"warehouse := assert_exists((select Warehouse filter .code = '{code}')), "
        "quantity := 777 }"
    )
    if proc.returncode == 0:
        _run_gel(["query", "delete StockLevel filter .quantity = 777"])
        pytest.fail(
            f"A second StockLevel for ({sku}, {code}) was accepted; the composite exclusive "
            "constraint was not preserved."
        )
    assert "constraint" in (proc.stdout + proc.stderr).lower(), (
        "Duplicating a (part, warehouse) stock level should fail with a constraint violation, "
        f"got:\n{proc.stdout}\n{proc.stderr}"
    )


# --------------------------------------------------------------------------
# 20. replay the whole history onto a brand new empty branch
# --------------------------------------------------------------------------
def test_history_replays_onto_a_fresh_empty_branch(gel_server):
    _, fs_revisions = _fs_history()
    _run_gel(["branch", "drop", REPLAY_BRANCH, "--non-interactive", "--force"])
    created = _run_gel(["branch", "create", "--empty", REPLAY_BRANCH])
    assert created.returncode == 0, (
        f"Could not create the empty verification branch: {created.stdout}\n{created.stderr}"
    )
    try:
        applied = _run_gel(
            ["migration", "apply", "--schema-dir", SCHEMA_DIR], branch=REPLAY_BRANCH
        )
        assert applied.returncode == 0, (
            "Applying dbschema/migrations to a brand-new empty branch must succeed, got exit "
            f"code {applied.returncode}.\nstdout: {applied.stdout}\nstderr: {applied.stderr}"
        )
        replayed = {row["name"] for row in _db_migrations(branch=REPLAY_BRANCH)}
        assert replayed == set(fs_revisions), (
            "Replaying the history must record exactly the on-disk revisions.\n"
            f"replayed: {sorted(replayed)}\non disk: {sorted(fs_revisions)}"
        )
        introspected = _introspect(
            [
                "default::Warehouse",
                "default::Part",
                "default::StockLevel",
                "default::AuditEvent",
                "default::ReorderRule",
            ],
            branch=REPLAY_BRANCH,
        )
        for type_name in (
            "default::Warehouse",
            "default::Part",
            "default::StockLevel",
            "default::AuditEvent",
            "default::ReorderRule",
        ):
            assert type_name in introspected, (
                f"{type_name} is missing after replaying the history onto an empty branch; "
                "the history does not reproduce the schema."
            )
        assert "seq" in introspected["default::AuditEvent"], (
            "default::AuditEvent.seq is missing after replaying the history onto an empty "
            "branch; the recovered out-of-band revision is not part of the replayed history."
        )
        assert "quantity" in introspected["default::StockLevel"], (
            "default::StockLevel.quantity is missing after replaying the history onto an "
            "empty branch."
        )
        assert "part" in introspected["default::ReorderRule"], (
            "default::ReorderRule.part is missing after replaying the history."
        )
        empty = _query_single("select count(Part)", branch=REPLAY_BRANCH)
        assert empty == 0, (
            f"The freshly replayed branch must contain no data, found {empty} Part objects."
        )
    finally:
        _run_gel(["branch", "drop", REPLAY_BRANCH, "--non-interactive", "--force"])
