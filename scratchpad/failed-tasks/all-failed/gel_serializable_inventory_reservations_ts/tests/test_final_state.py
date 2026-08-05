"""Final-state verification for the Gel serializable inventory reservation service.

Every functional requirement is checked by driving the real command-line adapter
(`npx tsx src/cli.ts`) against the real local Gel instance, and by issuing raw
EdgeQL through the `gel` CLI.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pytest

PROJECT_DIR = "/home/user/inventory"
SRC_DIR = os.path.join(PROJECT_DIR, "src")
MODULE_FILE = os.path.join(SRC_DIR, "reservations.ts")
CLI_FILE = os.path.join(SRC_DIR, "cli.ts")
MIGRATIONS_DIR = os.path.join(PROJECT_DIR, "dbschema", "migrations")
START_GEL = "/usr/local/bin/start-gel"

CLI_COMMAND = ["npx", "tsx", "src/cli.ts"]
CLI_TIMEOUT = 300

REQUIRED_EXPORTS = (
    "resetCatalog",
    "reserve",
    "reserveMany",
    "release",
    "expireDue",
    "snapshot",
    "getRetryAttempts",
)


# --------------------------------------------------------------------------- #
# Fixtures & helpers
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="session")
def client() -> None:
    """Guarantee the local Gel server is up.

    Any test that touches the database, either through the service CLI or
    through the `gel` CLI, must depend on this fixture.
    """
    assert os.path.isfile(START_GEL), f"{START_GEL} helper script is missing."
    proc = subprocess.run([START_GEL], capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, (
        "Failed to start the local Gel server.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


def _env() -> Dict[str, str]:
    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    env["NPM_CONFIG_UPDATE_NOTIFIER"] = "false"
    return env


def _parse_last_json_line(stdout: str, context: str) -> Any:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    assert lines, f"{context}: the command produced no output on stdout."
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError as exc:  # pragma: no cover - assertion path
        raise AssertionError(
            f"{context}: the last non-empty stdout line is not valid JSON "
            f"({exc}).\nLast line: {lines[-1]!r}\nFull stdout:\n{stdout}"
        ) from exc


def run_cli(payload: Dict[str, Any], timeout: int = CLI_TIMEOUT) -> Any:
    """Run one CLI command and return the decoded JSON result."""
    proc = subprocess.run(
        CLI_COMMAND,
        cwd=PROJECT_DIR,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_env(),
    )
    context = f"CLI command {payload.get('op')!r}"
    assert proc.returncode == 0, (
        f"{context} exited with code {proc.returncode}; expected 0.\n"
        f"payload: {json.dumps(payload)[:2000]}\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    return _parse_last_json_line(proc.stdout, context)


def run_cli_parallel(
    payloads: Sequence[Dict[str, Any]], timeout: int = CLI_TIMEOUT
) -> List[Any]:
    """Start every CLI process before collecting any result, then decode them.

    Starting all of the processes up-front (rather than using a worker pool that
    might serialise them) is what actually forces transaction conflicts in the
    database.
    """
    procs: List[Tuple[Dict[str, Any], "subprocess.Popen[str]"]] = []
    tmpdir = tempfile.mkdtemp(prefix="gel-reservations-")
    try:
        for index, payload in enumerate(payloads):
            stdin_path = os.path.join(tmpdir, f"payload-{index}.json")
            with open(stdin_path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(payload))
            with open(stdin_path, "r", encoding="utf-8") as stdin_file:
                proc = subprocess.Popen(
                    CLI_COMMAND,
                    cwd=PROJECT_DIR,
                    stdin=stdin_file,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=_env(),
                )
            procs.append((payload, proc))

        deadline = time.monotonic() + timeout
        results: List[Any] = []
        for payload, proc in procs:
            remaining = max(5.0, deadline - time.monotonic())
            try:
                stdout, stderr = proc.communicate(timeout=remaining)
            except subprocess.TimeoutExpired:  # pragma: no cover - assertion path
                proc.kill()
                raise AssertionError(
                    "A concurrent CLI process did not finish in time "
                    f"(payload: {json.dumps(payload)[:500]}). "
                    "The service may be deadlocking or retrying without bound."
                )
            context = f"concurrent CLI command {json.dumps(payload)[:300]}"
            assert proc.returncode == 0, (
                f"{context} exited with code {proc.returncode}; expected 0.\n"
                f"stdout: {stdout}\nstderr: {stderr}"
            )
            results.append(_parse_last_json_line(stdout, context))
        return results
    finally:
        for _, proc in procs:
            if proc.poll() is None:  # pragma: no cover - cleanup path
                proc.kill()
        shutil.rmtree(tmpdir, ignore_errors=True)


def gel_query(query: str, json_output: bool = False) -> subprocess.CompletedProcess:
    args = ["gel", "query"]
    if json_output:
        args.append("--output-format=json")
    args.append(query)
    return subprocess.run(
        args,
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=300,
        env=_env(),
    )


def gel_query_json(query: str) -> Any:
    proc = gel_query(query, json_output=True)
    assert proc.returncode == 0, (
        f"`gel query` failed for: {query}\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    return json.loads(proc.stdout)


def reset(items: Sequence[Dict[str, Any]]) -> None:
    result = run_cli({"op": "reset", "items": list(items)})
    assert isinstance(result, dict) and result.get("ok") is True, (
        f"The `reset` command must return {{\"ok\": true}}; got {result!r}"
    )
    snap = snapshot()
    assert len(snap["reservations"]) == 0, (
        f"`reset` must remove every reservation; snapshot still has "
        f"{len(snap['reservations'])}."
    )
    assert len(snap["ledger"]) == 0, (
        f"`reset` must remove every ledger entry; snapshot still has "
        f"{len(snap['ledger'])}."
    )
    assert [i["sku"] for i in snap["items"]] == sorted(i["sku"] for i in items), (
        "After `reset` the snapshot must contain exactly the seeded SKUs, sorted "
        f"ascending; got {[i['sku'] for i in snap['items']]!r}"
    )


def snapshot() -> Dict[str, Any]:
    snap = run_cli({"op": "snapshot"})
    assert isinstance(snap, dict), f"`snapshot` must return a JSON object; got {snap!r}"
    for key in ("items", "reservations", "ledger"):
        assert key in snap, f"`snapshot` result is missing the `{key}` key: {snap!r}"
        assert isinstance(snap[key], list), (
            f"`snapshot`.{key} must be a JSON array; got {snap[key]!r}"
        )
    return snap


def items_by_sku(snap: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {item["sku"]: item for item in snap["items"]}


def reservations_by_key(snap: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {res["idempotencyKey"]: res for res in snap["reservations"]}


def ledger_tuples(snap: Dict[str, Any]) -> List[Tuple[str, str, int]]:
    return sorted((row["sku"], row["kind"], row["delta"]) for row in snap["ledger"])


def assert_item(
    snap: Dict[str, Any], sku: str, stock: int, reserved: int
) -> None:
    item = items_by_sku(snap).get(sku)
    assert item is not None, f"SKU {sku!r} is missing from the snapshot: {snap['items']!r}"
    assert item["stock"] == stock, (
        f"{sku}: expected stock={stock}, got {item['stock']} ({item!r})"
    )
    assert item["reserved"] == reserved, (
        f"{sku}: expected reserved={reserved}, got {item['reserved']} ({item!r})"
    )
    assert item["available"] == stock - reserved, (
        f"{sku}: `available` must equal stock - reserved = {stock - reserved}, "
        f"got {item['available']} ({item!r})"
    )


def reserve_request(
    key: str,
    basket: Sequence[Dict[str, Any]],
    expires_at: Optional[str] = None,
) -> Dict[str, Any]:
    request: Dict[str, Any] = {"idempotencyKey": key, "basket": list(basket)}
    if expires_at is not None:
        request["expiresAt"] = expires_at
    return request


def do_reserve(
    key: str,
    basket: Sequence[Dict[str, Any]],
    expires_at: Optional[str] = None,
) -> Dict[str, Any]:
    outcome = run_cli({"op": "reserve", "request": reserve_request(key, basket, expires_at)})
    assert isinstance(outcome, dict), f"`reserve` must return a JSON object; got {outcome!r}"
    assert "status" in outcome, f"`reserve` outcome is missing `status`: {outcome!r}"
    return outcome


def assert_reserved(outcome: Dict[str, Any], idempotent: Optional[bool] = None) -> str:
    assert outcome.get("status") == "reserved", (
        f"Expected a successful reservation, got {outcome!r}"
    )
    reservation_id = outcome.get("reservationId")
    assert isinstance(reservation_id, str) and reservation_id, (
        f"A successful reservation must carry a non-empty string `reservationId`; got {outcome!r}"
    )
    assert isinstance(outcome.get("idempotent"), bool), (
        f"A successful reservation must carry a boolean `idempotent`; got {outcome!r}"
    )
    if idempotent is not None:
        assert outcome["idempotent"] is idempotent, (
            f"Expected `idempotent` to be {idempotent}; got {outcome!r}"
        )
    return reservation_id


def assert_rejected(
    outcome: Dict[str, Any],
    reason: str,
    details: Optional[List[str]] = None,
) -> None:
    assert outcome.get("status") == "rejected", (
        f"Expected a rejection with reason {reason!r}, got {outcome!r}"
    )
    assert outcome.get("reason") == reason, (
        f"Expected reason {reason!r}, got {outcome.get('reason')!r} ({outcome!r})"
    )
    assert isinstance(outcome.get("details"), list), (
        f"A rejection must carry a `details` array of strings; got {outcome!r}"
    )
    assert all(isinstance(entry, str) for entry in outcome["details"]), (
        f"Every `details` entry must be a string; got {outcome['details']!r}"
    )
    if details is not None:
        assert outcome["details"] == details, (
            f"Expected details {details!r}, got {outcome['details']!r} ({outcome!r})"
        )


# --------------------------------------------------------------------------- #
# 1. Project layout & migrations
# --------------------------------------------------------------------------- #


def test_service_module_and_cli_exist() -> None:
    assert os.path.isfile(MODULE_FILE), (
        f"The service module {MODULE_FILE} was not created."
    )
    assert os.path.isfile(CLI_FILE), (
        f"The command-line adapter {CLI_FILE} was not created."
    )
    cli_source = open(CLI_FILE, encoding="utf-8").read()
    assert "./reservations" in cli_source, (
        "src/cli.ts must import its behaviour from `./reservations`."
    )


def test_service_module_exports_required_api() -> None:
    source = open(MODULE_FILE, encoding="utf-8").read()
    for name in REQUIRED_EXPORTS:
        pattern = (
            r"export\s+(?:async\s+)?(?:function|const|let|var|class)\s+" + re.escape(name) + r"\b"
            r"|export\s*\{[^}]*\b" + re.escape(name) + r"\b[^}]*\}"
        )
        assert re.search(pattern, source), (
            f"src/reservations.ts does not export `{name}`; the module must export "
            f"all of {', '.join(REQUIRED_EXPORTS)}."
        )


def test_migration_files_exist() -> None:
    assert os.path.isdir(MIGRATIONS_DIR), (
        f"No migration directory at {MIGRATIONS_DIR}; the schema must be migrated."
    )
    migrations = [f for f in os.listdir(MIGRATIONS_DIR) if f.endswith(".edgeql")]
    assert migrations, (
        f"{MIGRATIONS_DIR} contains no .edgeql migration file; the schema change "
        "must be recorded as a migration."
    )


def test_migration_history_is_in_sync(client: None) -> None:
    proc = subprocess.run(
        ["gel", "migration", "status"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=300,
        env=_env(),
    )
    combined = (proc.stdout + "\n" + proc.stderr).lower()
    assert proc.returncode == 0, (
        "`gel migration status` reports the database is not in sync with "
        f"dbschema/ (exit code {proc.returncode}).\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    for bad in ("not in sync", "out of sync", "unapplied", "pending"):
        assert bad not in combined, (
            f"`gel migration status` output mentions {bad!r}, so the migration "
            f"history is not in sync.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
        )


# --------------------------------------------------------------------------- #
# 2. Schema shape
# --------------------------------------------------------------------------- #


def test_schema_declares_required_types_and_pointers(client: None) -> None:
    rows = gel_query_json(
        "select schema::ObjectType { name, pointers: { name } } "
        "filter .name in {"
        "'default::StockItem', 'default::Reservation', "
        "'default::ReservationLine', 'default::LedgerEntry'}"
    )
    found = {row["name"]: {p["name"] for p in row["pointers"]} for row in rows}
    expected = {
        "default::StockItem": {"sku", "stock", "reserved"},
        "default::Reservation": {"key", "state"},
        "default::ReservationLine": {"reservation", "item", "quantity"},
        "default::LedgerEntry": {"reservation", "item", "delta", "kind"},
    }
    for type_name, pointers in expected.items():
        assert type_name in found, (
            f"The schema does not declare {type_name}; found only {sorted(found)}."
        )
        missing = pointers - found[type_name]
        assert not missing, (
            f"{type_name} is missing the required pointer(s) {sorted(missing)}; "
            f"it declares {sorted(found[type_name])}."
        )


# --------------------------------------------------------------------------- #
# 3. Retry budget
# --------------------------------------------------------------------------- #


def test_transaction_retry_budget_is_configured(client: None) -> None:
    result = run_cli({"op": "retryAttempts"})
    assert isinstance(result, dict) and "attempts" in result, (
        f"`retryAttempts` must return an object with an `attempts` key; got {result!r}"
    )
    attempts = result["attempts"]
    assert isinstance(attempts, int) and not isinstance(attempts, bool), (
        f"`attempts` must be an integer; got {attempts!r}"
    )
    assert attempts >= 16, (
        f"The transaction retry budget must be at least 16 attempts; got {attempts}."
    )


# --------------------------------------------------------------------------- #
# 4-5. Happy path & snapshot ordering
# --------------------------------------------------------------------------- #


def test_single_reservation_happy_path(client: None) -> None:
    reset([{"sku": "ALPHA", "stock": 10}, {"sku": "BETA", "stock": 4}])
    outcome = do_reserve(
        "k-single",
        [{"sku": "ALPHA", "quantity": 3}, {"sku": "BETA", "quantity": 2}],
    )
    reservation_id = assert_reserved(outcome, idempotent=False)

    snap = snapshot()
    assert_item(snap, "ALPHA", stock=10, reserved=3)
    assert_item(snap, "BETA", stock=4, reserved=2)

    assert len(snap["reservations"]) == 1, (
        f"Expected exactly one reservation, got {snap['reservations']!r}"
    )
    reservation = snap["reservations"][0]
    assert reservation["reservationId"] == reservation_id, (
        "The reservation in the snapshot must carry the id returned by `reserve`; "
        f"got {reservation!r} vs {reservation_id!r}"
    )
    assert reservation["idempotencyKey"] == "k-single", f"Unexpected key: {reservation!r}"
    assert reservation["state"] == "active", f"Expected an active reservation: {reservation!r}"
    assert reservation["lines"] == [
        {"sku": "ALPHA", "quantity": 3},
        {"sku": "BETA", "quantity": 2},
    ], f"Unexpected reservation lines (must be sorted by sku): {reservation['lines']!r}"

    assert ledger_tuples(snap) == [("ALPHA", "reserve", -3), ("BETA", "reserve", -2)], (
        f"Expected exactly two reserve ledger rows, got {snap['ledger']!r}"
    )
    for row in snap["ledger"]:
        assert row["reservationId"] == reservation_id, (
            f"Ledger row is attached to the wrong reservation: {row!r}"
        )


def test_snapshot_is_sorted(client: None) -> None:
    reset([{"sku": "ZETA", "stock": 5}, {"sku": "ALPHA", "stock": 5}, {"sku": "MIKE", "stock": 5}])
    do_reserve(
        "k-sorted",
        [
            {"sku": "ZETA", "quantity": 1},
            {"sku": "ALPHA", "quantity": 1},
            {"sku": "MIKE", "quantity": 1},
        ],
    )
    do_reserve("k-sorted-2", [{"sku": "ALPHA", "quantity": 1}])

    snap = snapshot()
    skus = [item["sku"] for item in snap["items"]]
    assert skus == sorted(skus), f"`items` must be sorted by sku ascending; got {skus!r}"

    ids = [res["reservationId"] for res in snap["reservations"]]
    assert ids == sorted(ids), (
        f"`reservations` must be sorted by reservationId ascending; got {ids!r}"
    )

    for reservation in snap["reservations"]:
        line_skus = [line["sku"] for line in reservation["lines"]]
        assert line_skus == sorted(line_skus), (
            f"Reservation lines must be sorted by sku ascending; got {line_skus!r}"
        )


# --------------------------------------------------------------------------- #
# 6-8. Idempotency contract
# --------------------------------------------------------------------------- #


def test_idempotent_replay_is_a_no_op(client: None) -> None:
    reset([{"sku": "ALPHA", "stock": 10}, {"sku": "BETA", "stock": 4}])
    basket = [{"sku": "ALPHA", "quantity": 3}, {"sku": "BETA", "quantity": 2}]
    first = assert_reserved(do_reserve("k-single", basket), idempotent=False)

    replay = do_reserve("k-single", basket)
    second = assert_reserved(replay, idempotent=True)
    assert second == first, (
        f"A replay must return the original reservationId {first!r}; got {second!r}"
    )

    snap = snapshot()
    assert_item(snap, "ALPHA", stock=10, reserved=3)
    assert_item(snap, "BETA", stock=4, reserved=2)
    assert len(snap["reservations"]) == 1, (
        f"A replay must not create a second reservation; got {snap['reservations']!r}"
    )
    assert len(snap["ledger"]) == 2, (
        f"A replay must not append ledger rows; got {snap['ledger']!r}"
    )


def test_idempotency_key_conflict_is_rejected(client: None) -> None:
    reset([{"sku": "ALPHA", "stock": 10}, {"sku": "BETA", "stock": 4}])
    assert_reserved(
        do_reserve(
            "k-single", [{"sku": "ALPHA", "quantity": 3}, {"sku": "BETA", "quantity": 2}]
        ),
        idempotent=False,
    )

    conflict = do_reserve("k-single", [{"sku": "ALPHA", "quantity": 1}])
    assert_rejected(conflict, "IDEMPOTENCY_KEY_CONFLICT", details=[])

    snap = snapshot()
    assert_item(snap, "ALPHA", stock=10, reserved=3)
    assert len(snap["ledger"]) == 2, (
        f"A rejected replay must not append ledger rows; got {snap['ledger']!r}"
    )


def test_basket_equality_ignores_line_order(client: None) -> None:
    reset([{"sku": "ALPHA", "stock": 10}, {"sku": "BETA", "stock": 4}])
    first = assert_reserved(
        do_reserve(
            "k-single", [{"sku": "ALPHA", "quantity": 3}, {"sku": "BETA", "quantity": 2}]
        ),
        idempotent=False,
    )

    reordered = do_reserve(
        "k-single", [{"sku": "BETA", "quantity": 2}, {"sku": "ALPHA", "quantity": 3}]
    )
    assert assert_reserved(reordered, idempotent=True) == first, (
        "A replay whose basket has the same (sku, quantity) pairs in a different "
        f"order must be treated as identical; got {reordered!r}"
    )


# --------------------------------------------------------------------------- #
# 9-13. Rejection taxonomy & precedence
# --------------------------------------------------------------------------- #


def test_unknown_sku_lists_every_unknown_sku(client: None) -> None:
    reset([{"sku": "ALPHA", "stock": 5}])
    outcome = do_reserve(
        "k-unknown",
        [
            {"sku": "ZULU", "quantity": 1},
            {"sku": "ALPHA", "quantity": 1},
            {"sku": "MIKE", "quantity": 1},
        ],
    )
    assert_rejected(outcome, "UNKNOWN_SKU", details=["MIKE", "ZULU"])

    snap = snapshot()
    assert_item(snap, "ALPHA", stock=5, reserved=0)
    assert snap["reservations"] == [], (
        f"A rejected reservation must not be persisted; got {snap['reservations']!r}"
    )
    assert snap["ledger"] == [], (
        f"A rejected reservation must not append ledger rows; got {snap['ledger']!r}"
    )


def test_unknown_sku_outranks_insufficient_stock(client: None) -> None:
    reset([{"sku": "ALPHA", "stock": 1}])
    outcome = do_reserve(
        "k-mixed", [{"sku": "ALPHA", "quantity": 9}, {"sku": "ZULU", "quantity": 1}]
    )
    assert_rejected(outcome, "UNKNOWN_SKU", details=["ZULU"])


def test_multi_item_basket_is_atomic(client: None) -> None:
    reset([{"sku": "ALPHA", "stock": 5}, {"sku": "BETA", "stock": 0}])
    outcome = do_reserve(
        "k-partial", [{"sku": "ALPHA", "quantity": 2}, {"sku": "BETA", "quantity": 1}]
    )
    assert_rejected(outcome, "INSUFFICIENT_STOCK", details=["BETA"])

    snap = snapshot()
    assert_item(snap, "ALPHA", stock=5, reserved=0)
    assert_item(snap, "BETA", stock=0, reserved=0)
    assert snap["reservations"] == [], (
        f"A basket that cannot be fully reserved must leave no reservation; "
        f"got {snap['reservations']!r}"
    )
    assert snap["ledger"] == [], (
        f"A basket that cannot be fully reserved must leave no ledger rows; "
        f"got {snap['ledger']!r}"
    )


def test_insufficient_stock_lists_every_offending_sku(client: None) -> None:
    reset([{"sku": "ALPHA", "stock": 0}, {"sku": "BETA", "stock": 0}])
    outcome = do_reserve(
        "k-both", [{"sku": "BETA", "quantity": 1}, {"sku": "ALPHA", "quantity": 1}]
    )
    assert_rejected(outcome, "INSUFFICIENT_STOCK", details=["ALPHA", "BETA"])


@pytest.mark.parametrize(
    "key,basket",
    [
        ("", [{"sku": "ALPHA", "quantity": 1}]),
        ("k-bad1", []),
        ("k-bad2", [{"sku": "ALPHA", "quantity": 0}]),
        ("k-bad3", [{"sku": "ALPHA", "quantity": -2}]),
        ("k-bad4", [{"sku": "ALPHA", "quantity": 1}, {"sku": "ALPHA", "quantity": 1}]),
    ],
)
def test_invalid_requests_are_rejected(
    client: None, key: str, basket: List[Dict[str, Any]]
) -> None:
    reset([{"sku": "ALPHA", "stock": 5}])
    outcome = do_reserve(key, basket)
    assert_rejected(outcome, "INVALID_REQUEST")

    snap = snapshot()
    assert snap["reservations"] == [], (
        f"An invalid request must not create a reservation; got {snap['reservations']!r}"
    )
    assert_item(snap, "ALPHA", stock=5, reserved=0)


# --------------------------------------------------------------------------- #
# 14-17. Concurrency invariants
# --------------------------------------------------------------------------- #


def test_no_oversell_under_concurrent_processes(client: None) -> None:
    reset([{"sku": "WIDGET", "stock": 3}])
    payloads = [
        {
            "op": "reserve",
            "request": reserve_request(f"race-{i}", [{"sku": "WIDGET", "quantity": 1}]),
        }
        for i in range(8)
    ]
    outcomes = run_cli_parallel(payloads)

    succeeded = [o for o in outcomes if o.get("status") == "reserved"]
    rejected = [o for o in outcomes if o.get("status") != "reserved"]
    assert len(succeeded) == 3, (
        "With a stock budget of 3 and 8 concurrent single-unit reservations, exactly "
        f"3 must succeed; got {len(succeeded)}.\nOutcomes: {outcomes!r}"
    )
    assert len({o["reservationId"] for o in succeeded}) == 3, (
        f"The 3 successful reservations must have distinct ids; got {succeeded!r}"
    )
    for outcome in succeeded:
        assert outcome.get("idempotent") is False, (
            f"Distinct idempotency keys must each create a new reservation; got {outcome!r}"
        )
    for outcome in rejected:
        assert_rejected(outcome, "INSUFFICIENT_STOCK", details=["WIDGET"])

    snap = snapshot()
    assert_item(snap, "WIDGET", stock=3, reserved=3)
    assert len(snap["reservations"]) == 3, (
        f"Exactly 3 reservations must be persisted; got {snap['reservations']!r}"
    )
    assert all(res["state"] == "active" for res in snap["reservations"]), (
        f"All surviving reservations must be active; got {snap['reservations']!r}"
    )
    assert ledger_tuples(snap) == [("WIDGET", "reserve", -1)] * 3, (
        f"Expected exactly three (WIDGET, reserve, -1) ledger rows; got {snap['ledger']!r}"
    )


def test_concurrent_multi_item_baskets_stay_atomic(client: None) -> None:
    reset([{"sku": "ALPHA", "stock": 5}, {"sku": "BETA", "stock": 5}])
    payloads = [
        {
            "op": "reserve",
            "request": reserve_request(
                f"pair-{i}",
                [{"sku": "ALPHA", "quantity": 1}, {"sku": "BETA", "quantity": 1}],
            ),
        }
        for i in range(10)
    ]
    outcomes = run_cli_parallel(payloads)

    succeeded = [o for o in outcomes if o.get("status") == "reserved"]
    rejected = [o for o in outcomes if o.get("status") != "reserved"]
    assert len(succeeded) == 5, (
        "With 5 units of each SKU and 10 concurrent two-line baskets, exactly 5 must "
        f"succeed; got {len(succeeded)}.\nOutcomes: {outcomes!r}"
    )
    for outcome in rejected:
        assert_rejected(outcome, "INSUFFICIENT_STOCK")
        assert outcome["details"], (
            f"An INSUFFICIENT_STOCK rejection must name at least one SKU; got {outcome!r}"
        )
        assert set(outcome["details"]) <= {"ALPHA", "BETA"}, (
            f"`details` may only name SKUs from the basket; got {outcome['details']!r}"
        )

    snap = snapshot()
    assert_item(snap, "ALPHA", stock=5, reserved=5)
    assert_item(snap, "BETA", stock=5, reserved=5)
    assert len(snap["reservations"]) == 5, (
        f"Exactly 5 reservations must be persisted; got {snap['reservations']!r}"
    )
    assert len(snap["ledger"]) == 10, (
        f"Expected 10 ledger rows (2 per successful reservation); got {snap['ledger']!r}"
    )


def test_concurrent_replays_of_one_key_reserve_once(client: None) -> None:
    reset([{"sku": "GADGET", "stock": 9}])
    payloads = [
        {
            "op": "reserve",
            "request": reserve_request("dup-key", [{"sku": "GADGET", "quantity": 2}]),
        }
        for _ in range(6)
    ]
    outcomes = run_cli_parallel(payloads)

    ids = set()
    for outcome in outcomes:
        ids.add(assert_reserved(outcome))
    assert len(ids) == 1, (
        "Six concurrent replays of the same idempotency key and basket must all "
        f"return the same reservationId; got {ids!r}\nOutcomes: {outcomes!r}"
    )

    snap = snapshot()
    assert len(snap["reservations"]) == 1, (
        f"Exactly one reservation must exist; got {snap['reservations']!r}"
    )
    assert_item(snap, "GADGET", stock=9, reserved=2)
    assert ledger_tuples(snap) == [("GADGET", "reserve", -2)], (
        f"Exactly one ledger row must have been appended; got {snap['ledger']!r}"
    )


def test_in_process_fan_out_respects_stock_budget(client: None) -> None:
    reset([{"sku": "BOLT", "stock": 7}])
    requests = [
        reserve_request(f"many-{i}", [{"sku": "BOLT", "quantity": 1}]) for i in range(20)
    ]
    result = run_cli({"op": "reserveMany", "requests": requests})
    assert isinstance(result, dict) and isinstance(result.get("outcomes"), list), (
        f"`reserveMany` must return {{\"outcomes\": [...]}}; got {result!r}"
    )
    outcomes = result["outcomes"]
    assert len(outcomes) == 20, (
        f"`reserveMany` must return one outcome per request; got {len(outcomes)}"
    )

    succeeded = [o for o in outcomes if o.get("status") == "reserved"]
    rejected = [o for o in outcomes if o.get("status") != "reserved"]
    assert len(succeeded) == 7, (
        f"With a stock budget of 7, exactly 7 of 20 requests must succeed; "
        f"got {len(succeeded)}.\nOutcomes: {outcomes!r}"
    )
    assert len({o["reservationId"] for o in succeeded}) == 7, (
        f"The successful reservations must have distinct ids; got {succeeded!r}"
    )
    for outcome in rejected:
        assert_rejected(outcome, "INSUFFICIENT_STOCK", details=["BOLT"])

    snap = snapshot()
    assert_item(snap, "BOLT", stock=7, reserved=7)
    assert len(snap["reservations"]) == 7, (
        f"Exactly 7 reservations must be persisted; got {snap['reservations']!r}"
    )
    assert ledger_tuples(snap) == [("BOLT", "reserve", -1)] * 7, (
        f"Expected exactly seven (BOLT, reserve, -1) ledger rows; got {snap['ledger']!r}"
    )


# --------------------------------------------------------------------------- #
# 18-20. Release lifecycle
# --------------------------------------------------------------------------- #


def test_release_frees_stock_and_allows_re_reservation(client: None) -> None:
    reset([{"sku": "ALPHA", "stock": 2}])
    reservation_id = assert_reserved(
        do_reserve("k-rel", [{"sku": "ALPHA", "quantity": 2}]), idempotent=False
    )

    blocked = do_reserve("k-rel2", [{"sku": "ALPHA", "quantity": 2}])
    assert_rejected(blocked, "INSUFFICIENT_STOCK", details=["ALPHA"])

    released = run_cli({"op": "release", "reservationId": reservation_id})
    assert released.get("status") == "released", (
        f"Releasing an active reservation must succeed; got {released!r}"
    )
    assert released.get("reservationId") == reservation_id, (
        f"`release` must echo the released reservationId; got {released!r}"
    )

    snap = snapshot()
    assert_item(snap, "ALPHA", stock=2, reserved=0)
    assert reservations_by_key(snap)["k-rel"]["state"] == "released", (
        f"The reservation must be marked released; got {snap['reservations']!r}"
    )
    assert ledger_tuples(snap) == [("ALPHA", "release", 2), ("ALPHA", "reserve", -2)], (
        f"Expected one reserve and one release ledger row; got {snap['ledger']!r}"
    )

    again = do_reserve("k-rel2", [{"sku": "ALPHA", "quantity": 2}])
    assert_reserved(again, idempotent=False)
    assert_item(snapshot(), "ALPHA", stock=2, reserved=2)


def test_release_edge_cases(client: None) -> None:
    reset([{"sku": "ALPHA", "stock": 2}])
    reservation_id = assert_reserved(
        do_reserve("k-rel", [{"sku": "ALPHA", "quantity": 2}]), idempotent=False
    )
    run_cli({"op": "release", "reservationId": reservation_id})

    repeat = run_cli({"op": "release", "reservationId": reservation_id})
    assert_rejected(repeat, "ALREADY_RELEASED", details=[])

    missing = run_cli(
        {"op": "release", "reservationId": "00000000-0000-0000-0000-000000000000"}
    )
    assert_rejected(missing, "UNKNOWN_RESERVATION", details=[])

    malformed = run_cli({"op": "release", "reservationId": "not-a-uuid"})
    assert_rejected(malformed, "UNKNOWN_RESERVATION", details=[])

    assert_item(snapshot(), "ALPHA", stock=2, reserved=0)


def test_replay_after_release_does_not_re_reserve(client: None) -> None:
    reset([{"sku": "ALPHA", "stock": 4}])
    basket = [{"sku": "ALPHA", "quantity": 4}]
    reservation_id = assert_reserved(do_reserve("k-replay", basket), idempotent=False)
    run_cli({"op": "release", "reservationId": reservation_id})

    replay = do_reserve("k-replay", basket)
    assert assert_reserved(replay, idempotent=True) == reservation_id, (
        "Replaying the key of a released reservation must return that reservation; "
        f"got {replay!r}"
    )

    snap = snapshot()
    assert_item(snap, "ALPHA", stock=4, reserved=0)
    assert len(snap["ledger"]) == 2, (
        f"The replay must not append ledger rows; got {snap['ledger']!r}"
    )
    assert reservations_by_key(snap)["k-replay"]["state"] == "released", (
        f"The reservation must still be released; got {snap['reservations']!r}"
    )


# --------------------------------------------------------------------------- #
# 21-22. Expiry & ledger consistency
# --------------------------------------------------------------------------- #


def _seed_expiry_state() -> Dict[str, str]:
    reset([{"sku": "ALPHA", "stock": 10}])
    ids = {
        "k-exp": assert_reserved(
            do_reserve(
                "k-exp", [{"sku": "ALPHA", "quantity": 3}], expires_at="2001-01-01T00:00:00Z"
            ),
            idempotent=False,
        ),
        "k-future": assert_reserved(
            do_reserve(
                "k-future",
                [{"sku": "ALPHA", "quantity": 2}],
                expires_at="2999-01-01T00:00:00Z",
            ),
            idempotent=False,
        ),
        "k-noexp": assert_reserved(
            do_reserve("k-noexp", [{"sku": "ALPHA", "quantity": 1}]), idempotent=False
        ),
    }
    return ids


def test_expiry_releases_only_due_reservations(client: None) -> None:
    ids = _seed_expiry_state()
    assert_item(snapshot(), "ALPHA", stock=10, reserved=6)

    result = run_cli({"op": "expire", "now": "2020-01-01T00:00:00Z"})
    assert isinstance(result, dict) and isinstance(result.get("released"), list), (
        f"`expire` must return {{\"released\": [...]}}; got {result!r}"
    )
    assert result["released"] == [ids["k-exp"]], (
        "Only the reservation whose expiry instant has passed may be released; "
        f"got {result['released']!r} (expected [{ids['k-exp']!r}])"
    )

    snap = snapshot()
    assert_item(snap, "ALPHA", stock=10, reserved=3)
    by_key = reservations_by_key(snap)
    assert by_key["k-exp"]["state"] == "released", f"k-exp must be released: {by_key!r}"
    assert by_key["k-future"]["state"] == "active", f"k-future must stay active: {by_key!r}"
    assert by_key["k-noexp"]["state"] == "active", f"k-noexp must stay active: {by_key!r}"

    repeat = run_cli({"op": "expire", "now": "2020-01-01T00:00:00Z"})
    assert repeat["released"] == [], (
        f"A second `expire` at the same instant must release nothing; got {repeat!r}"
    )
    snap_after = snapshot()
    assert_item(snap_after, "ALPHA", stock=10, reserved=3)
    assert len(snap_after["ledger"]) == len(snap["ledger"]), (
        "A no-op `expire` must not append ledger rows: "
        f"{len(snap['ledger'])} -> {len(snap_after['ledger'])}"
    )


def test_ledger_explains_reserved_amounts(client: None) -> None:
    _seed_expiry_state()
    run_cli({"op": "expire", "now": "2020-01-01T00:00:00Z"})

    snap = snapshot()
    known_ids = {res["reservationId"] for res in snap["reservations"]}
    totals: Dict[str, int] = {item["sku"]: 0 for item in snap["items"]}
    for row in snap["ledger"]:
        assert row["kind"] in ("reserve", "release"), (
            f"Ledger `kind` must be 'reserve' or 'release'; got {row!r}"
        )
        assert row["sku"] in totals, f"Ledger row names an unknown SKU: {row!r}"
        assert row["reservationId"] in known_ids, (
            f"Ledger row refers to a reservation that is not in the snapshot: {row!r}"
        )
        if row["kind"] == "reserve":
            assert row["delta"] < 0, f"A reserve ledger row must be negative: {row!r}"
        else:
            assert row["delta"] > 0, f"A release ledger row must be positive: {row!r}"
        totals[row["sku"]] += row["delta"]

    for item in snap["items"]:
        assert item["reserved"] == -totals[item["sku"]], (
            f"{item['sku']}: reserved={item['reserved']} does not equal the negated "
            f"ledger sum {-totals[item['sku']]}."
        )


# --------------------------------------------------------------------------- #
# 23-24. Database-enforced invariants (raw EdgeQL, bypassing the service)
# --------------------------------------------------------------------------- #


def test_database_rejects_invariant_violations(client: None) -> None:
    _seed_expiry_state()
    run_cli({"op": "expire", "now": "2020-01-01T00:00:00Z"})
    assert_item(snapshot(), "ALPHA", stock=10, reserved=3)

    forbidden = [
        (
            "reserved above stock",
            "update StockItem filter .sku = 'ALPHA' set { reserved := .stock + 1 }",
        ),
        (
            "negative reserved",
            "update StockItem filter .sku = 'ALPHA' set { reserved := -1 }",
        ),
        (
            "duplicate sku",
            "insert StockItem { sku := 'ALPHA', stock := 1 }",
        ),
        (
            "duplicate idempotency key",
            "insert Reservation { key := 'k-noexp' }",
        ),
    ]
    for label, statement in forbidden:
        proc = gel_query(statement)
        assert proc.returncode != 0, (
            f"The database accepted a statement that violates the {label} invariant: "
            f"{statement!r}\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
        )

    rows = gel_query_json(
        "select StockItem { sku, stock, reserved } filter .sku = 'ALPHA'"
    )
    assert len(rows) == 1, f"Expected exactly one ALPHA StockItem; got {rows!r}"
    assert rows[0]["stock"] == 10 and rows[0]["reserved"] == 3, (
        "None of the rejected statements may have taken effect; ALPHA is now "
        f"{rows[0]!r} instead of stock=10 reserved=3."
    )


def test_migration_history_still_in_sync_at_the_end(client: None) -> None:
    proc = subprocess.run(
        ["gel", "migration", "status"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=300,
        env=_env(),
    )
    assert proc.returncode == 0, (
        "`gel migration status` no longer reports an in-sync schema after the test "
        f"run (exit code {proc.returncode}).\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
