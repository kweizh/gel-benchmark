"""Final-state verification for the gel_triggers_audit_trail_cli task.

Everything is verified against the real, running local Gel server through the
``gel`` CLI and through the two shell wrappers the executor had to write.

Because ``AuditEvent``/``AuditBatch`` rows are append-only, every "new rows"
expectation is evaluated as an id-set delta around the statement under test, and
all verifier-owned products use the reserved ``ZZ-`` sku prefix with a unique
suffix so repeated runs never collide.
"""

import glob
import json
import os
import re
import secrets
import shutil
import subprocess

import pytest

PROJECT_DIR = "/home/user/pricing-audit"
SCHEMA_FILE = os.path.join(PROJECT_DIR, "dbschema", "default.gel")
MIGRATIONS_DIR = os.path.join(PROJECT_DIR, "dbschema", "migrations")
SCRIPTS_DIR = os.path.join(PROJECT_DIR, "scripts")
APPLY_SCRIPT = os.path.join(SCRIPTS_DIR, "apply_price_change.sh")
REPORT_SCRIPT = os.path.join(SCRIPTS_DIR, "audit_report.sh")
REFERENCE_MIGRATION = "/opt/task-reference/00001-original.edgeql"
RUN_ID_FILE = "/logs/artifacts/run-id"

SEEDED_SKUS = [
    "AX-100",
    "AX-200",
    "BX-110",
    "BX-220",
    "CX-130",
    "CX-240",
    "DX-150",
    "SKU-FROZEN",
]

PRODUCT_TRIGGERS = {
    "log_product_insert": ("Insert", "Each"),
    "log_price_update": ("Update", "Each"),
    "log_product_delete": ("Delete", "Each"),
    "batch_insert_stats": ("Insert", "All"),
    "batch_update_stats": ("Update", "All"),
    "batch_delete_stats": ("Delete", "All"),
}

REPORT_KEYS = {
    "sku",
    "inserts",
    "updates",
    "deletes",
    "events",
    "net_price_change_cents",
}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def sh(args, timeout=300):
    return subprocess.run(
        list(args),
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def gel_raw(query, timeout=300):
    return sh(["gel", "query", "-F", "json", query], timeout=timeout)


def gel_json(query, timeout=300):
    proc = gel_raw(query, timeout=timeout)
    assert proc.returncode == 0, (
        f"`gel query` failed ({proc.returncode}) for {query!r}:\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    return json.loads(proc.stdout)


def unique_suffix():
    base = "local"
    try:
        with open(RUN_ID_FILE) as handle:
            candidate = handle.read().strip()
        if candidate:
            base = re.sub(r"[^A-Za-z0-9]", "", candidate)[:16] or "local"
    except OSError:
        pass
    return f"{base}-{secrets.token_hex(3)}"


def events_by_id():
    rows = gel_json(
        "select AuditEvent { id, action, sku, old_price_cents, new_price_cents, "
        "summary, recorded_at }"
    )
    return {row["id"]: row for row in rows}


def batches_by_id():
    rows = gel_json("select AuditBatch { id, kind, row_count, recorded_at }")
    return {row["id"]: row for row in rows}


def product(sku):
    rows = gel_json(
        "select Product { sku, name, price_cents, stock, revision, price_history } "
        f"filter .sku = '{sku}'"
    )
    return rows[0] if rows else None


class Step:
    """Records the audit rows created by a single statement/command."""

    def __init__(self, label):
        self.label = label
        self.new_events = []
        self.new_batches = []
        self.proc = None


def measure(label, action):
    before_events = set(events_by_id())
    before_batches = set(batches_by_id())
    step = Step(label)
    step.proc = action()
    after_events = events_by_id()
    after_batches = batches_by_id()
    step.new_events = [
        after_events[i] for i in sorted(set(after_events) - before_events)
    ]
    step.new_batches = [
        after_batches[i] for i in sorted(set(after_batches) - before_batches)
    ]
    return step


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def server():
    """Guarantee the local Gel server is reachable (idempotent)."""
    starter = shutil.which("gel-start.sh") or "/usr/local/bin/gel-start.sh"
    proc = subprocess.run([starter], capture_output=True, text=True, timeout=900)
    assert proc.returncode == 0, (
        "gel-start.sh could not bring up the local Gel server:\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    return True


@pytest.fixture(scope="session")
def scenario(server):
    """Drive the whole audit workflow once and record every observation."""
    assert os.path.isfile(APPLY_SCRIPT), f"Missing {APPLY_SCRIPT}."
    assert os.path.isfile(REPORT_SCRIPT), f"Missing {REPORT_SCRIPT}."

    uniq = unique_suffix()
    sku = {
        "a1": f"ZZ-A1-{uniq}",
        "b1": f"ZZ-B1-{uniq}",
        "b2": f"ZZ-B2-{uniq}",
        "b3": f"ZZ-B3-{uniq}",
        "direct": f"ZZ-DIRECT-{uniq}",
        "bad": f"ZZ-BAD-{uniq}",
        "missing": f"ZZ-MISSING-{uniq}",
        "norow": f"ZZ-NO-SUCH-ROW-{uniq}",
        "hacked": f"ZZ-HACKED-{uniq}",
    }
    data = {"sku": sku, "uniq": uniq}

    # Remove leftovers from any previous verification run before baselines.
    gel_raw("delete Product filter .sku like 'ZZ-%'")

    # 5. insert trigger + insert rewrites
    data["insert"] = measure(
        "insert a1",
        lambda: gel_raw(
            "insert Product { "
            f"sku := '{sku['a1']}', name := 'Verifier A1', "
            "price_cents := 1000, stock := 5 }"
        ),
    )
    data["a1_after_insert"] = product(sku["a1"])

    # 6. happy path price change
    data["price_change"] = measure(
        "apply_price_change happy",
        lambda: sh([APPLY_SCRIPT, sku["a1"], "1250"]),
    )
    data["a1_after_change"] = product(sku["a1"])

    # 7. no-op price change
    data["noop_change"] = measure(
        "apply_price_change noop",
        lambda: sh([APPLY_SCRIPT, sku["a1"], "1250"]),
    )
    data["a1_after_noop"] = product(sku["a1"])

    # 8. non-price update through raw EdgeQL
    data["non_price_update"] = measure(
        "raw non-price update",
        lambda: gel_raw(
            f"update Product filter .sku = '{sku['a1']}' "
            "set { stock := 99, name := 'Verifier A1 renamed' }"
        ),
    )
    data["a1_after_non_price"] = product(sku["a1"])

    # 9. error paths (must not touch the database at all)
    error_cases = [
        ("missing_arg", [sku["a1"]], 4),
        ("too_many_args", [sku["a1"], "1250", "extra"], 4),
        ("zero_price", [sku["a1"], "0"], 3),
        ("negative_price", [sku["a1"], "-5"], 3),
        ("fractional_price", [sku["a1"], "12.5"], 3),
        ("leading_zero_price", [sku["a1"], "0123"], 3),
        ("unknown_sku", [sku["missing"], "500"], 2),
        ("precedence", [sku["missing"], "0"], 3),
    ]
    before_events = set(events_by_id())
    before_batches = set(batches_by_id())
    data["errors"] = {}
    for name, args, expected_rc in error_cases:
        data["errors"][name] = (sh([APPLY_SCRIPT] + args), expected_rc, args)
    after_events = set(events_by_id())
    after_batches = set(batches_by_id())
    data["errors_new_events"] = sorted(after_events - before_events)
    data["errors_new_batches"] = sorted(after_batches - before_batches)
    data["a1_after_errors"] = product(sku["a1"])

    # 10. multi-row insert in a single statement
    data["multi_insert"] = measure(
        "multi insert",
        lambda: gel_raw(
            "for p in {"
            f"('{sku['b1']}', 200), ('{sku['b2']}', 300), ('{sku['b3']}', 400)"
            "} union (insert Product { sku := p.0, "
            "name := 'Verifier ' ++ p.0, price_cents := p.1 })"
        ),
    )

    # 11. multi-row update in a single statement
    data["multi_update"] = measure(
        "multi update",
        lambda: gel_raw(
            "update Product filter .sku in "
            f"{{'{sku['b1']}', '{sku['b2']}'}} set {{ price_cents := 777 }}"
        ),
    )

    # 12. zero-row statement
    data["zero_row_update"] = measure(
        "zero row update",
        lambda: gel_raw(
            f"update Product filter .sku = '{sku['norow']}' "
            "set { price_cents := 500 }"
        ),
    )

    # 13. delete auditing
    data["delete"] = measure(
        "delete b3",
        lambda: gel_raw(f"delete Product filter .sku = '{sku['b3']}'"),
    )

    # 14. append-only enforcement
    data["forbidden_update"] = measure(
        "forbidden audit update",
        lambda: gel_raw(
            f"update AuditEvent filter .sku = '{sku['a1']}' "
            f"set {{ sku := '{sku['hacked']}' }}"
        ),
    )
    data["forbidden_delete"] = measure(
        "forbidden audit delete",
        lambda: gel_raw(f"delete AuditEvent filter .sku = '{sku['a1']}'"),
    )
    data["a1_event_count"] = gel_json(
        f"select count((select AuditEvent filter .sku = '{sku['a1']}'))"
    )[0]
    data["hacked_event_count"] = gel_json(
        f"select count((select AuditEvent filter .sku = '{sku['hacked']}'))"
    )[0]

    # 15. summary is derived, not supplied
    data["direct_insert"] = measure(
        "direct audit insert",
        lambda: gel_raw(
            "insert AuditEvent { action := 'update', "
            f"sku := '{sku['direct']}', old_price_cents := 10, "
            "new_price_cents := 25, summary := 'BOGUS' }"
        ),
    )

    # 16. action domain enforcement
    data["bad_action"] = measure(
        "bad action insert",
        lambda: gel_raw(
            "insert AuditEvent { action := 'bogus', "
            f"sku := '{sku['bad']}', old_price_cents := 1, new_price_cents := 2 }}"
        ),
    )
    data["bad_action_count"] = gel_json(
        f"select count((select AuditEvent filter .sku = '{sku['bad']}'))"
    )[0]

    # 17. the report (must be the last mutation-free observation)
    data["report_proc"] = sh([REPORT_SCRIPT])
    data["distinct_skus"] = gel_json("select distinct AuditEvent.sku")

    # 4. seeded data / frozen fixture
    data["seeded"] = gel_json("select Product { sku } filter .sku in {"
                              + ", ".join(f"'{s}'" for s in SEEDED_SKUS)
                              + "}")
    data["frozen"] = product("SKU-FROZEN")

    # 2/3. schema introspection
    data["triggers"] = gel_json(
        "select schema::ObjectType { name, triggers: { name, kinds, timing, "
        "scope, condition } } "
        "filter .name in {'default::Product', 'default::AuditEvent'}"
    )
    data["props"] = gel_json(
        "select schema::ObjectType { name, properties: { name, required, "
        "rewrites: { kind } } } filter .name in "
        "{'default::Product', 'default::AuditEvent', 'default::AuditBatch'}"
    )

    # 1. migration state
    data["migration_status"] = sh(["gel", "migration", "status"])
    return data


def triggers_of(scenario, type_name):
    for row in scenario["triggers"]:
        if row["name"] == type_name:
            return {t["name"]: t for t in row["triggers"]}
    raise AssertionError(f"Object type {type_name} was not found in the schema.")


def props_of(scenario, type_name):
    for row in scenario["props"]:
        if row["name"] == type_name:
            return {p["name"]: p for p in row["properties"]}
    raise AssertionError(f"Object type {type_name} was not found in the schema.")


def report(scenario):
    proc = scenario["report_proc"]
    assert proc.returncode == 0, (
        f"audit_report.sh exited {proc.returncode}; stderr={proc.stderr}"
    )
    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"audit_report.sh stdout is not valid JSON ({exc}): {proc.stdout!r}"
        ) from exc
    assert isinstance(parsed, list), (
        f"audit_report.sh must print a JSON array, got {type(parsed).__name__}."
    )
    return parsed


def report_entry(scenario, sku):
    for entry in report(scenario):
        if entry.get("sku") == sku:
            return entry
    raise AssertionError(
        f"The audit report has no entry for sku {sku!r}: {report(scenario)}"
    )


# --------------------------------------------------------------------------- #
# 1. project & migration state
# --------------------------------------------------------------------------- #
def test_migration_status_up_to_date(scenario):
    proc = scenario["migration_status"]
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"`gel migration status` failed: {combined}"
    assert "up to date" in combined, (
        f"`gel migration status` must report the database is up to date: {combined}"
    )


def test_original_migration_untouched_and_new_migration_added(scenario):
    files = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql")))
    first = [f for f in files if os.path.basename(f).startswith("00001-")]
    assert len(first) == 1, (
        f"Exactly one 00001-* migration must remain in {MIGRATIONS_DIR}, got {files}."
    )
    assert open(first[0]).read() == open(REFERENCE_MIGRATION).read(), (
        "The pre-existing 00001 migration file was modified; it must stay byte-identical."
    )
    assert len(files) >= 2, (
        f"At least one additional migration must have been created, got {files}."
    )


def test_schema_file_declares_the_audit_subsystem(scenario):
    content = open(SCHEMA_FILE).read()
    expected = [
        "AuditEvent",
        "AuditBatch",
        "revision",
        "price_history",
        "summary",
        "log_product_insert",
        "log_price_update",
        "log_product_delete",
        "batch_insert_stats",
        "batch_update_stats",
        "batch_delete_stats",
        "forbid_mutation",
    ]
    missing = [token for token in expected if token not in content]
    assert not missing, (
        f"{SCHEMA_FILE} must declare the audit subsystem; missing: {missing}"
    )


# --------------------------------------------------------------------------- #
# 2. trigger introspection
# --------------------------------------------------------------------------- #
def test_product_triggers_are_declared_with_required_names(scenario):
    found = triggers_of(scenario, "default::Product")
    missing = [name for name in PRODUCT_TRIGGERS if name not in found]
    assert not missing, (
        f"default::Product is missing the required triggers {missing}; "
        f"found {sorted(found)}."
    )


def test_product_triggers_have_required_kinds_timing_and_scope(scenario):
    found = triggers_of(scenario, "default::Product")
    for name, (kind, scope) in PRODUCT_TRIGGERS.items():
        trig = found[name]
        assert trig["kinds"] == [kind], (
            f"Trigger {name} must react to {kind} only, got {trig['kinds']}."
        )
        assert trig["timing"] == "After", (
            f"Trigger {name} must run after the statement, got {trig['timing']}."
        )
        assert trig["scope"] == scope, (
            f"Trigger {name} must have {scope} granularity, got {trig['scope']}."
        )


def test_price_update_trigger_is_conditional(scenario):
    trig = triggers_of(scenario, "default::Product")["log_price_update"]
    assert trig["condition"], (
        "log_price_update must be conditional so unchanged prices are not logged; "
        f"condition={trig['condition']!r}"
    )


def test_audit_event_has_forbid_mutation_trigger(scenario):
    found = triggers_of(scenario, "default::AuditEvent")
    assert "forbid_mutation" in found, (
        f"default::AuditEvent must declare a `forbid_mutation` trigger, found {sorted(found)}."
    )
    trig = found["forbid_mutation"]
    assert set(trig["kinds"]) == {"Update", "Delete"}, (
        f"forbid_mutation must react to Update and Delete, got {trig['kinds']}."
    )
    assert trig["scope"] == "Each", (
        f"forbid_mutation must be a per-row trigger, got {trig['scope']}."
    )


# --------------------------------------------------------------------------- #
# 3. rewrite introspection
# --------------------------------------------------------------------------- #
def test_product_bookkeeping_properties_have_rewrites(scenario):
    props = props_of(scenario, "default::Product")
    assert "revision" in props, "default::Product must declare a `revision` property."
    assert "price_history" in props, (
        "default::Product must declare a `price_history` property."
    )
    assert props["revision"]["required"], "`revision` must be a required property."
    assert props["price_history"]["required"], (
        "`price_history` must be a required property."
    )
    assert "Update" in {r["kind"] for r in props["revision"]["rewrites"]}, (
        "`revision` must be maintained by an update mutation rewrite, got "
        f"{props['revision']['rewrites']}."
    )
    assert {"Insert", "Update"} <= {
        r["kind"] for r in props["price_history"]["rewrites"]
    }, (
        "`price_history` must be maintained by insert and update mutation rewrites, got "
        f"{props['price_history']['rewrites']}."
    )


def test_audit_event_summary_has_insert_rewrite(scenario):
    props = props_of(scenario, "default::AuditEvent")
    assert "summary" in props, "default::AuditEvent must declare a `summary` property."
    assert props["summary"]["required"], "`summary` must be a required property."
    assert "Insert" in {r["kind"] for r in props["summary"]["rewrites"]}, (
        "`summary` must be derived by an insert mutation rewrite, got "
        f"{props['summary']['rewrites']}."
    )


def test_recorded_at_properties_are_required(scenario):
    for type_name in ("default::AuditEvent", "default::AuditBatch"):
        props = props_of(scenario, type_name)
        assert "recorded_at" in props, (
            f"{type_name} must declare a `recorded_at` property."
        )
        assert props["recorded_at"]["required"], (
            f"{type_name}.recorded_at must be required."
        )


# --------------------------------------------------------------------------- #
# 4. seeded data / original schema preserved
# --------------------------------------------------------------------------- #
def test_seeded_products_preserved(scenario):
    skus = sorted(row["sku"] for row in scenario["seeded"])
    assert skus == sorted(SEEDED_SKUS), (
        f"All eight seeded products must still exist; found {skus}."
    )


def test_frozen_product_untouched(scenario):
    frozen = scenario["frozen"]
    assert frozen is not None, "The SKU-FROZEN verification fixture disappeared."
    assert frozen["revision"] == 1, (
        f"SKU-FROZEN must still have revision 1, got {frozen['revision']}."
    )
    assert frozen["price_history"] == [], (
        f"SKU-FROZEN must still have an empty price_history, got {frozen['price_history']}."
    )


def test_original_product_properties_preserved(scenario):
    props = props_of(scenario, "default::Product")
    for name in ("sku", "name", "price_cents", "stock"):
        assert name in props, (
            f"The original Product property `{name}` must be preserved, found {sorted(props)}."
        )


# --------------------------------------------------------------------------- #
# 5. insert auditing
# --------------------------------------------------------------------------- #
def test_insert_creates_one_audit_event(scenario):
    step = scenario["insert"]
    assert step.proc.returncode == 0, (
        f"Inserting the verifier product failed: {step.proc.stderr}"
    )
    assert len(step.new_events) == 1, (
        f"An insert must create exactly one AuditEvent, got {step.new_events}."
    )
    event = step.new_events[0]
    sku = scenario["sku"]["a1"]
    assert event["action"] == "insert", f"Expected action 'insert', got {event['action']!r}."
    assert event["sku"] == sku, f"Expected sku {sku!r}, got {event['sku']!r}."
    assert event["old_price_cents"] is None, (
        f"old_price_cents must be empty for an insert, got {event['old_price_cents']!r}."
    )
    assert event["new_price_cents"] == 1000, (
        f"new_price_cents must be 1000, got {event['new_price_cents']!r}."
    )
    assert event["summary"] == f"INSERT {sku} price=1000", (
        f"Unexpected summary: {event['summary']!r}"
    )


def test_insert_rewrites_initialize_bookkeeping(scenario):
    prod = scenario["a1_after_insert"]
    assert prod is not None, "The inserted verifier product is missing."
    assert prod["revision"] == 1, f"A new product must start at revision 1, got {prod['revision']}."
    assert prod["price_history"] == [1000], (
        f"A new product's price_history must be [1000], got {prod['price_history']}."
    )


def test_insert_creates_one_batch_row(scenario):
    batches = scenario["insert"].new_batches
    assert len(batches) == 1, (
        f"A single insert statement must create exactly one AuditBatch row, got {batches}."
    )
    assert batches[0]["kind"] == "insert", (
        f"Expected kind 'insert', got {batches[0]['kind']!r}."
    )
    assert batches[0]["row_count"] == 1, (
        f"Expected row_count 1, got {batches[0]['row_count']}."
    )


# --------------------------------------------------------------------------- #
# 6. apply_price_change.sh happy path
# --------------------------------------------------------------------------- #
def test_apply_price_change_stdout_and_exit_code(scenario):
    step = scenario["price_change"]
    sku = scenario["sku"]["a1"]
    assert step.proc.returncode == 0, (
        f"apply_price_change.sh failed ({step.proc.returncode}): {step.proc.stderr}"
    )
    assert step.proc.stdout == f"{sku} 1000 -> 1250 revision=2\n", (
        f"Unexpected stdout: {step.proc.stdout!r}"
    )


def test_apply_price_change_records_update_event(scenario):
    step = scenario["price_change"]
    sku = scenario["sku"]["a1"]
    assert len(step.new_events) == 1, (
        f"A real price change must create exactly one AuditEvent, got {step.new_events}."
    )
    event = step.new_events[0]
    assert event["action"] == "update", f"Expected action 'update', got {event['action']!r}."
    assert event["old_price_cents"] == 1000, (
        f"Expected old_price_cents 1000, got {event['old_price_cents']!r}."
    )
    assert event["new_price_cents"] == 1250, (
        f"Expected new_price_cents 1250, got {event['new_price_cents']!r}."
    )
    assert event["summary"] == f"UPDATE {sku} price=1000->1250", (
        f"Unexpected summary: {event['summary']!r}"
    )


def test_apply_price_change_updates_bookkeeping_and_batch(scenario):
    prod = scenario["a1_after_change"]
    assert prod["price_cents"] == 1250, (
        f"The new price must be persisted, got {prod['price_cents']}."
    )
    assert prod["revision"] == 2, f"Expected revision 2, got {prod['revision']}."
    assert prod["price_history"] == [1000, 1250], (
        f"Expected price_history [1000, 1250], got {prod['price_history']}."
    )
    batches = scenario["price_change"].new_batches
    assert len(batches) == 1, (
        f"apply_price_change.sh must run exactly one mutating statement, got {batches}."
    )
    assert (batches[0]["kind"], batches[0]["row_count"]) == ("update", 1), (
        f"Expected an update batch of 1 row, got {batches[0]}."
    )


# --------------------------------------------------------------------------- #
# 7. no-op price change
# --------------------------------------------------------------------------- #
def test_noop_price_change_still_succeeds_and_bumps_revision(scenario):
    step = scenario["noop_change"]
    sku = scenario["sku"]["a1"]
    assert step.proc.returncode == 0, (
        f"A no-op price change must still succeed: {step.proc.stderr}"
    )
    assert step.proc.stdout == f"{sku} 1250 -> 1250 revision=3\n", (
        f"Unexpected stdout: {step.proc.stdout!r}"
    )
    prod = scenario["a1_after_noop"]
    assert prod["revision"] == 3, f"Expected revision 3, got {prod['revision']}."
    assert prod["price_history"] == [1000, 1250], (
        f"An unchanged price must not extend price_history, got {prod['price_history']}."
    )


def test_noop_price_change_creates_no_audit_event_but_one_batch(scenario):
    step = scenario["noop_change"]
    assert step.new_events == [], (
        f"An update that does not change the price must not be audited, got {step.new_events}."
    )
    assert len(step.new_batches) == 1, (
        f"The statement must still produce one AuditBatch row, got {step.new_batches}."
    )
    assert (step.new_batches[0]["kind"], step.new_batches[0]["row_count"]) == (
        "update",
        1,
    ), f"Expected an update batch of 1 row, got {step.new_batches[0]}."


# --------------------------------------------------------------------------- #
# 8. non-price update
# --------------------------------------------------------------------------- #
def test_non_price_update_bumps_revision_without_audit_event(scenario):
    step = scenario["non_price_update"]
    assert step.proc.returncode == 0, (
        f"The raw non-price update failed: {step.proc.stderr}"
    )
    assert step.new_events == [], (
        f"A non-price update must not create an AuditEvent, got {step.new_events}."
    )
    assert len(step.new_batches) == 1 and step.new_batches[0]["row_count"] == 1, (
        f"Expected exactly one AuditBatch row of 1 row, got {step.new_batches}."
    )
    prod = scenario["a1_after_non_price"]
    assert prod["revision"] == 4, f"Expected revision 4, got {prod['revision']}."
    assert prod["price_history"] == [1000, 1250], (
        f"price_history must be untouched, got {prod['price_history']}."
    )


# --------------------------------------------------------------------------- #
# 9. error paths
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "case,expected_stderr",
    [
        ("missing_arg", "ERROR: usage: apply_price_change.sh <sku> <new_price_cents>"),
        ("too_many_args", "ERROR: usage: apply_price_change.sh <sku> <new_price_cents>"),
        ("zero_price", "ERROR: invalid price: 0"),
        ("negative_price", "ERROR: invalid price: -5"),
        ("fractional_price", "ERROR: invalid price: 12.5"),
        ("leading_zero_price", "ERROR: invalid price: 0123"),
        ("unknown_sku", "ERROR: unknown sku: "),
        ("precedence", "ERROR: invalid price: 0"),
    ],
)
def test_apply_price_change_error_paths(scenario, case, expected_stderr):
    proc, expected_rc, args = scenario["errors"][case]
    assert proc.returncode == expected_rc, (
        f"apply_price_change.sh {args} must exit {expected_rc}, got {proc.returncode} "
        f"(stdout={proc.stdout!r}, stderr={proc.stderr!r})."
    )
    assert proc.stdout == "", (
        f"apply_price_change.sh {args} must print nothing to stdout, got {proc.stdout!r}."
    )
    assert expected_stderr in proc.stderr, (
        f"apply_price_change.sh {args} stderr must contain {expected_stderr!r}, "
        f"got {proc.stderr!r}."
    )


def test_error_paths_leave_no_side_effects(scenario):
    assert scenario["errors_new_events"] == [], (
        f"Failed invocations must not create AuditEvent rows, got {scenario['errors_new_events']}."
    )
    assert scenario["errors_new_batches"] == [], (
        f"Failed invocations must not create AuditBatch rows, got {scenario['errors_new_batches']}."
    )
    prod = scenario["a1_after_errors"]
    assert prod["revision"] == 4, (
        f"Failed invocations must not bump revision, got {prod['revision']}."
    )
    assert prod["price_cents"] == 1250, (
        f"Failed invocations must not change the price, got {prod['price_cents']}."
    )


# --------------------------------------------------------------------------- #
# 10-12. per-statement batch semantics
# --------------------------------------------------------------------------- #
def test_multi_row_insert_audits_each_row_once(scenario):
    step = scenario["multi_insert"]
    assert step.proc.returncode == 0, f"The multi-row insert failed: {step.proc.stderr}"
    expected = {
        (scenario["sku"]["b1"], 200),
        (scenario["sku"]["b2"], 300),
        (scenario["sku"]["b3"], 400),
    }
    actual = {(e["sku"], e["new_price_cents"]) for e in step.new_events}
    assert len(step.new_events) == 3 and actual == expected, (
        f"Expected one insert AuditEvent per row {expected}, got {step.new_events}."
    )
    assert all(e["action"] == "insert" for e in step.new_events), (
        f"All three events must have action 'insert', got {step.new_events}."
    )


def test_multi_row_insert_produces_single_batch_row(scenario):
    batches = scenario["multi_insert"].new_batches
    assert len(batches) == 1, (
        f"One statement must yield exactly one AuditBatch row, got {batches}."
    )
    assert (batches[0]["kind"], batches[0]["row_count"]) == ("insert", 3), (
        f"Expected kind 'insert' with row_count 3, got {batches[0]}."
    )


def test_multi_row_update_audits_each_row_and_one_batch(scenario):
    step = scenario["multi_update"]
    assert step.proc.returncode == 0, f"The multi-row update failed: {step.proc.stderr}"
    expected = {
        (scenario["sku"]["b1"], 200, 777),
        (scenario["sku"]["b2"], 300, 777),
    }
    actual = {
        (e["sku"], e["old_price_cents"], e["new_price_cents"]) for e in step.new_events
    }
    assert len(step.new_events) == 2 and actual == expected, (
        f"Expected update events {expected}, got {step.new_events}."
    )
    batches = step.new_batches
    assert len(batches) == 1 and (batches[0]["kind"], batches[0]["row_count"]) == (
        "update",
        2,
    ), f"Expected a single update AuditBatch of 2 rows, got {batches}."


def test_zero_row_statement_still_records_one_batch_row(scenario):
    step = scenario["zero_row_update"]
    assert step.proc.returncode == 0, (
        f"An update matching no rows should still succeed: {step.proc.stderr}"
    )
    assert step.new_events == [], (
        f"A statement that modified nothing must not be audited per row, got {step.new_events}."
    )
    assert len(step.new_batches) == 1, (
        f"A zero-row statement must still record exactly one AuditBatch row, got {step.new_batches}."
    )
    assert (
        step.new_batches[0]["kind"],
        step.new_batches[0]["row_count"],
    ) == ("update", 0), (
        f"Expected kind 'update' with row_count 0, got {step.new_batches[0]}."
    )


# --------------------------------------------------------------------------- #
# 13. delete auditing
# --------------------------------------------------------------------------- #
def test_delete_is_audited(scenario):
    step = scenario["delete"]
    sku = scenario["sku"]["b3"]
    assert step.proc.returncode == 0, f"Deleting the product failed: {step.proc.stderr}"
    assert len(step.new_events) == 1, (
        f"A delete must create exactly one AuditEvent, got {step.new_events}."
    )
    event = step.new_events[0]
    assert event["action"] == "delete", f"Expected action 'delete', got {event['action']!r}."
    assert event["sku"] == sku, f"Expected sku {sku!r}, got {event['sku']!r}."
    assert event["old_price_cents"] == 400, (
        f"Expected old_price_cents 400, got {event['old_price_cents']!r}."
    )
    assert event["new_price_cents"] is None, (
        f"new_price_cents must be empty for a delete, got {event['new_price_cents']!r}."
    )
    assert event["summary"] == f"DELETE {sku} price=400", (
        f"Unexpected summary: {event['summary']!r}"
    )
    batches = step.new_batches
    assert len(batches) == 1 and (batches[0]["kind"], batches[0]["row_count"]) == (
        "delete",
        1,
    ), f"Expected a single delete AuditBatch of 1 row, got {batches}."


# --------------------------------------------------------------------------- #
# 14. append-only enforcement
# --------------------------------------------------------------------------- #
def test_audit_event_update_is_rejected(scenario):
    proc = scenario["forbidden_update"].proc
    assert proc.returncode != 0, (
        f"Updating an AuditEvent must fail, but the command succeeded: {proc.stdout!r}"
    )
    assert "AuditEvent is append-only" in proc.stderr, (
        f"The failure must mention 'AuditEvent is append-only', got {proc.stderr!r}."
    )


def test_audit_event_delete_is_rejected(scenario):
    proc = scenario["forbidden_delete"].proc
    assert proc.returncode != 0, (
        f"Deleting an AuditEvent must fail, but the command succeeded: {proc.stdout!r}"
    )
    assert "AuditEvent is append-only" in proc.stderr, (
        f"The failure must mention 'AuditEvent is append-only', got {proc.stderr!r}."
    )


def test_rejected_mutations_left_audit_rows_intact(scenario):
    assert scenario["a1_event_count"] == 2, (
        "The audited product must still have exactly its 2 AuditEvent rows, got "
        f"{scenario['a1_event_count']}."
    )
    assert scenario["hacked_event_count"] == 0, (
        "No AuditEvent may carry the tampered sku, got "
        f"{scenario['hacked_event_count']} rows."
    )


# --------------------------------------------------------------------------- #
# 15-16. anti-bypass on AuditEvent itself
# --------------------------------------------------------------------------- #
def test_supplied_summary_is_overwritten_by_the_database(scenario):
    step = scenario["direct_insert"]
    sku = scenario["sku"]["direct"]
    assert step.proc.returncode == 0, (
        f"Appending an AuditEvent by hand must be allowed: {step.proc.stderr}"
    )
    assert len(step.new_events) == 1, (
        f"Expected exactly one new AuditEvent, got {step.new_events}."
    )
    event = step.new_events[0]
    assert event["summary"] == f"UPDATE {sku} price=10->25", (
        f"The caller-supplied summary must be replaced by the derived one, got "
        f"{event['summary']!r}."
    )
    assert event["recorded_at"], (
        f"recorded_at must be filled in automatically, got {event['recorded_at']!r}."
    )


def test_invalid_action_is_rejected(scenario):
    step = scenario["bad_action"]
    assert step.proc.returncode != 0, (
        f"An AuditEvent with an unsupported action must be rejected: {step.proc.stdout!r}"
    )
    assert scenario["bad_action_count"] == 0, (
        f"The rejected AuditEvent must not be stored, got {scenario['bad_action_count']} rows."
    )


# --------------------------------------------------------------------------- #
# 17. audit_report.sh
# --------------------------------------------------------------------------- #
def test_report_shape_and_ordering(scenario):
    entries = report(scenario)
    for entry in entries:
        assert set(entry) == REPORT_KEYS, (
            f"Every report entry must have exactly the keys {sorted(REPORT_KEYS)}, got "
            f"{sorted(entry)}."
        )
    skus = [entry["sku"] for entry in entries]
    assert skus == sorted(skus), f"Report entries must be sorted by sku ascending: {skus}"
    assert len(skus) == len(set(skus)), f"Report entries must be unique per sku: {skus}"


def test_report_covers_exactly_the_audited_skus(scenario):
    entries = report(scenario)
    assert {entry["sku"] for entry in entries} == set(scenario["distinct_skus"]), (
        "The report must contain exactly the skus present in AuditEvent; report="
        f"{sorted(entry['sku'] for entry in entries)} db={sorted(scenario['distinct_skus'])}"
    )
    assert all(entry["events"] > 0 for entry in entries), (
        f"No report entry may have events = 0: {entries}"
    )
    assert "SKU-FROZEN" not in {entry["sku"] for entry in entries}, (
        "SKU-FROZEN has no audit rows, so it must not appear in the report."
    )


def test_report_totals_are_internally_consistent(scenario):
    for entry in report(scenario):
        assert entry["events"] == (
            entry["inserts"] + entry["updates"] + entry["deletes"]
        ), f"events must be the sum of inserts/updates/deletes: {entry}"


@pytest.mark.parametrize(
    "key,expected",
    [
        ("a1", {"inserts": 1, "updates": 1, "deletes": 0, "events": 2,
                "net_price_change_cents": 250}),
        ("b1", {"inserts": 1, "updates": 1, "deletes": 0, "events": 2,
                "net_price_change_cents": 577}),
        ("b2", {"inserts": 1, "updates": 1, "deletes": 0, "events": 2,
                "net_price_change_cents": 477}),
        ("b3", {"inserts": 1, "updates": 0, "deletes": 1, "events": 2,
                "net_price_change_cents": 0}),
        ("direct", {"inserts": 0, "updates": 1, "deletes": 0, "events": 1,
                    "net_price_change_cents": 15}),
    ],
)
def test_report_aggregates_per_sku(scenario, key, expected):
    sku = scenario["sku"][key]
    entry = report_entry(scenario, sku)
    actual = {name: entry[name] for name in expected}
    assert actual == expected, (
        f"Unexpected report aggregate for {sku}: expected {expected}, got {actual}."
    )


# --------------------------------------------------------------------------- #
# 18. script hygiene
# --------------------------------------------------------------------------- #
def test_task_scripts_are_executable_files(scenario):
    for path in (APPLY_SCRIPT, REPORT_SCRIPT):
        assert os.path.isfile(path), f"{path} must exist as a regular file."
        assert os.access(path, os.X_OK), f"{path} must be executable."


def test_no_client_library_code_was_added(scenario):
    patterns = [
        re.compile(r"^\s*import\s+gel\b", re.M),
        re.compile(r"^\s*import\s+edgedb\b", re.M),
        re.compile(r"^\s*from\s+gel\b", re.M),
        re.compile(r"""require\(\s*['"](gel|edgedb)['"]\s*\)"""),
        re.compile(r"""from\s+['"](gel|edgedb)['"]"""),
    ]
    offenders = []
    for root, dirs, files in os.walk(PROJECT_DIR):
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "__pycache__"}]
        for name in files:
            if not name.endswith((".py", ".ts", ".tsx", ".js", ".mjs", ".go")):
                continue
            path = os.path.join(root, name)
            try:
                text = open(path, errors="replace").read()
            except OSError:
                continue
            if any(pattern.search(text) for pattern in patterns):
                offenders.append(path)
    assert offenders == [], (
        "The audit subsystem must be built with SDL/EdgeQL and the gel CLI only; found "
        f"files using a Gel client library: {offenders}"
    )
