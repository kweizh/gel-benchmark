"""Final-state verification for the gel_double_entry_ledger_transfers_py task.

Everything is checked against the real, running local Gel instance: the applied
schema is introspected and probed with raw EdgeQL, and the public API of
`/home/user/ledger/ledger_api.py` is driven with the async Gel client.

All data created here is prefixed with a per-run random token so that leftovers
from the executor's own experiments can never influence the outcome.
"""

import asyncio
import decimal
import glob
import importlib
import json
import os
import random
import subprocess
import sys
import time
import uuid

import pytest

PROJECT_DIR = "/home/user/ledger"
MIGRATIONS_GLOB = os.path.join(PROJECT_DIR, "dbschema", "migrations", "*.edgeql")
BASELINE_GLOB = "/opt/ledger-baseline/migrations/*.edgeql"
START_SCRIPT = "/usr/local/bin/gel-start.sh"

D = decimal.Decimal
TOKEN = uuid.uuid4().hex[:8]

SEEDED_ACCOUNTS = {
    "ACC-1001": D("1000.00"),
    "ACC-1002": D("500.00"),
    "ACC-1003": D("250.50"),
    "ACC-1004": D("0.00"),
}

STATEMENT_KEYS = ("transfer_id", "idempotency_key", "counterparty", "direction", "amount")


# ---------------------------------------------------------------------------
# infrastructure helpers
# ---------------------------------------------------------------------------


def _run_gel(args, timeout=180):
    return subprocess.run(
        ["gel"] + args,
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.fixture(scope="session")
def gel_server():
    """Start the local Gel server (idempotent) and wait until it answers queries."""
    proc = subprocess.run(
        ["bash", START_SCRIPT], capture_output=True, text=True, timeout=300
    )
    print("gel-start.sh stdout:\n" + (proc.stdout or ""))
    print("gel-start.sh stderr:\n" + (proc.stderr or ""))
    deadline = time.time() + 180
    last = ""
    while time.time() < deadline:
        probe = _run_gel(["query", "-F", "json", "select 1"], timeout=60)
        if probe.returncode == 0:
            return True
        last = (probe.stdout or "") + (probe.stderr or "")
        time.sleep(2)
    raise AssertionError(
        "The local Gel server never became reachable; last probe output: " + last
    )


def _query_list(query):
    """Run a query through the CLI and always return the result set as a list."""
    proc = _run_gel(["query", "-F", "json", query])
    assert proc.returncode == 0, (
        f"`gel query` failed for {query!r}: stdout={proc.stdout} stderr={proc.stderr}"
    )
    data = json.loads(proc.stdout)
    return data if isinstance(data, list) else [data]


def _import_ledger_api():
    if PROJECT_DIR not in sys.path:
        sys.path.insert(0, PROJECT_DIR)
    try:
        return importlib.import_module("ledger_api")
    except Exception as exc:  # pragma: no cover - reported as a test failure
        raise AssertionError(
            f"`ledger_api` could not be imported from {PROJECT_DIR}: {exc!r}"
        ) from exc


@pytest.fixture(scope="session")
def api(gel_server):
    return _import_ledger_api()


def run_async(body, timeout=180, concurrency=None):
    """Run `body(client)` with a fresh async Gel client."""
    import gel

    async def main():
        if concurrency is None:
            client = gel.create_async_client()
        else:
            client = gel.create_async_client(concurrency=concurrency)
        try:
            return await asyncio.wait_for(body(client), timeout=timeout)
        finally:
            await client.aclose()

    return asyncio.run(main())


def gel_errors():
    import gel.errors

    return gel.errors


# ---------------------------------------------------------------------------
# small query helpers (executed through the async client)
# ---------------------------------------------------------------------------


async def _transfer_row(client, key):
    return await client.query_single(
        """
        select Transfer {
            tid := <str>.id,
            amount_str := <str>.amount,
            sender_code := .sender.code,
            recipient_code := .recipient.code,
            entry_count := count(.entries),
            entry_sum := <str>sum(.entries.amount),
        }
        filter .idempotency_key = <str>$key
        """,
        key=key,
    )


async def _entries_of(client, key):
    return await client.query(
        """
        select LedgerEntry {
            account_code := .account.code,
            amount_str := <str>.amount,
        }
        filter .transfer.idempotency_key = <str>$key
        order by .amount
        """,
        key=key,
    )


async def _entry_count(client):
    return await client.query_single("select count(LedgerEntry)")


async def _transfer_count_with_prefix(client, prefix):
    return await client.query_single(
        "select count((select Transfer filter .idempotency_key like <str>$p ++ '%'))",
        p=prefix,
    )






# ---------------------------------------------------------------------------
# 1. project / migration state
# ---------------------------------------------------------------------------


def test_a_new_migration_was_created_without_touching_the_baseline():
    migrations = sorted(glob.glob(MIGRATIONS_GLOB))
    assert len(migrations) >= 2, (
        "Expected the baked migration plus at least one new migration in "
        f"dbschema/migrations, found: {migrations}"
    )
    baseline = sorted(glob.glob(BASELINE_GLOB))
    assert len(baseline) == 1, (
        f"The baked baseline migration snapshot is missing: {BASELINE_GLOB}"
    )
    with open(baseline[0], "rb") as handle:
        expected = handle.read()
    with open(migrations[0], "rb") as handle:
        actual = handle.read()
    assert actual == expected, (
        f"The pre-existing migration {os.path.basename(migrations[0])} was modified; "
        "it must be left byte-identical."
    )


def test_migration_history_is_in_sync(gel_server):
    proc = _run_gel(["migration", "status"])
    assert proc.returncode == 0, (
        "`gel migration status` does not report an up-to-date branch, so the new "
        f"schema is not migrated: stdout={proc.stdout} stderr={proc.stderr}"
    )


# ---------------------------------------------------------------------------
# 2. schema shape
# ---------------------------------------------------------------------------


def _introspect(type_name):
    rows = _query_list(
        """
        select schema::ObjectType {
            name,
            properties: { name, required, cardinality, target: { name } },
            links: { name, required, cardinality, target: { name } },
        }
        filter .name = '%s'
        """
        % type_name
    )
    assert len(rows) == 1, f"Object type {type_name} does not exist: {rows}"
    row = rows[0]
    props = {p["name"]: p for p in row["properties"]}
    links = {l["name"]: l for l in row["links"]}
    return props, links


def test_account_type_shape(gel_server):
    props, links = _introspect("default::Account")
    assert "code" in props, "`Account.code` is missing."
    assert props["code"]["required"] is True, "`Account.code` must be required."
    assert props["code"]["cardinality"] == "One", "`Account.code` must be single."
    assert "opening_balance" in props, "`Account.opening_balance` is missing."
    assert props["opening_balance"]["required"] is True, (
        "`Account.opening_balance` must be required."
    )
    pointer = props.get("balance") or links.get("balance")
    assert pointer is not None, "`Account.balance` is missing."
    assert pointer["cardinality"] == "One", "`Account.balance` must be single-valued."


def test_transfer_type_shape(gel_server):
    props, links = _introspect("default::Transfer")
    assert "idempotency_key" in props, "`Transfer.idempotency_key` is missing."
    assert props["idempotency_key"]["required"] is True, (
        "`Transfer.idempotency_key` must be required."
    )
    assert "amount" in props, "`Transfer.amount` is missing."
    assert props["amount"]["required"] is True, "`Transfer.amount` must be required."
    assert "created_at" in props, "`Transfer.created_at` is missing."
    for name in ("sender", "recipient"):
        assert name in links, f"`Transfer.{name}` link is missing."
        assert links[name]["required"] is True, f"`Transfer.{name}` must be required."
        assert links[name]["cardinality"] == "One", f"`Transfer.{name}` must be single."
        assert links[name]["target"]["name"] == "default::Account", (
            f"`Transfer.{name}` must point at `default::Account`, got "
            f"{links[name]['target']['name']}"
        )
    assert "entries" in links, "`Transfer.entries` is missing."
    assert links["entries"]["cardinality"] == "Many", (
        "`Transfer.entries` must be a multi (Many) pointer."
    )
    assert links["entries"]["target"]["name"] == "default::LedgerEntry", (
        "`Transfer.entries` must point at `default::LedgerEntry`, got "
        f"{links['entries']['target']['name']}"
    )


def test_ledger_entry_type_shape(gel_server):
    props, links = _introspect("default::LedgerEntry")
    assert "amount" in props, "`LedgerEntry.amount` is missing."
    assert props["amount"]["required"] is True, "`LedgerEntry.amount` must be required."
    for name, target in (("account", "default::Account"), ("transfer", "default::Transfer")):
        assert name in links, f"`LedgerEntry.{name}` link is missing."
        assert links[name]["required"] is True, f"`LedgerEntry.{name}` must be required."
        assert links[name]["cardinality"] == "One", f"`LedgerEntry.{name}` must be single."
        assert links[name]["target"]["name"] == target, (
            f"`LedgerEntry.{name}` must point at `{target}`, got "
            f"{links[name]['target']['name']}"
        )


def test_seeded_accounts_are_preserved(gel_server):
    rows = _query_list(
        "select Account { code, ob := <str>.opening_balance } "
        "filter .code in {'ACC-1001', 'ACC-1002', 'ACC-1003', 'ACC-1004'}"
    )
    actual = {row["code"]: D(row["ob"]) for row in rows}
    for code, expected in SEEDED_ACCOUNTS.items():
        assert code in actual, f"Pre-existing account {code} disappeared."
        assert actual[code] == expected, (
            f"`opening_balance` of {code} changed from {expected} to {actual[code]}."
        )


# ---------------------------------------------------------------------------
# 3. reference fixture data (created through the public API)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def reference(api):
    """Two accounts and one committed transfer used by the raw-EdgeQL probes."""

    async def body(client):
        await api.create_account(
            client, code=f"{TOKEN}-RA", opening_balance=D("40.00")
        )
        await api.create_account(client, code=f"{TOKEN}-RB", opening_balance=D("0.00"))
        record = await api.create_transfer(
            client,
            sender=f"{TOKEN}-RA",
            recipient=f"{TOKEN}-RB",
            amount=D("4.00"),
            idempotency_key=f"{TOKEN}-ref",
        )
        return record

    record = run_async(body)
    assert isinstance(record, dict) and "id" in record, (
        f"`create_transfer` must return a dict containing 'id', got {record!r}"
    )
    return record


# ---------------------------------------------------------------------------
# 4. database-level rejections (raw EdgeQL, bypassing ledger_api)
# ---------------------------------------------------------------------------


def _expect_rejected(coro_body, description):
    """Assert that a raw EdgeQL statement is refused by the database itself."""
    errors = gel_errors()
    with pytest.raises(errors.ExecutionError) as excinfo:
        run_async(coro_body)
    print(f"{description} was rejected with {type(excinfo.value).__name__}")


def test_database_rejects_non_positive_transfer_amounts(reference):
    for bad in ("-5.00", "0.00"):

        async def body(client, bad=bad):
            return await client.execute(
                """
                insert Transfer {
                    idempotency_key := <str>$key,
                    amount := <decimal>$amount,
                    sender := assert_single((select Account filter .code = <str>$s)),
                    recipient := assert_single((select Account filter .code = <str>$r)),
                }
                """,
                key=f"{TOKEN}-raw-amount-{bad}",
                amount=D(bad),
                s=f"{TOKEN}-RA",
                r=f"{TOKEN}-RB",
            )

        _expect_rejected(body, f"a Transfer with amount {bad}")

    def check(client):
        return _transfer_count_with_prefix(client, f"{TOKEN}-raw-amount")

    assert run_async(check) == 0, (
        "A Transfer with a non-positive amount was persisted by the database."
    )


def test_database_rejects_self_transfer(reference):
    async def body(client):
        return await client.execute(
            """
            insert Transfer {
                idempotency_key := <str>$key,
                amount := <decimal>'1.00',
                sender := assert_single((select Account filter .code = 'ACC-1001')),
                recipient := assert_single((select Account filter .code = 'ACC-1001')),
            }
            """,
            key=f"{TOKEN}-raw-self",
        )

    _expect_rejected(body, "a Transfer whose sender equals its recipient")

    def check(client):
        return _transfer_count_with_prefix(client, f"{TOKEN}-raw-self")

    assert run_async(check) == 0, "A self-transfer was persisted by the database."


def test_database_rejects_duplicate_idempotency_key(reference):
    async def body(client):
        return await client.execute(
            """
            insert Transfer {
                idempotency_key := <str>$key,
                amount := <decimal>'1.00',
                sender := assert_single((select Account filter .code = <str>$s)),
                recipient := assert_single((select Account filter .code = <str>$r)),
            }
            """,
            key=f"{TOKEN}-ref",
            s=f"{TOKEN}-RB",
            r=f"{TOKEN}-RA",
        )

    _expect_rejected(body, "a Transfer reusing an existing idempotency_key")

    def check(client):
        return _transfer_count_with_prefix(client, f"{TOKEN}-ref")

    assert run_async(check) == 1, (
        "Reusing an idempotency_key must not create a second Transfer."
    )


def test_database_rejects_duplicate_account_code(gel_server):
    async def body(client):
        return await client.execute(
            "insert Account { code := 'ACC-1001', opening_balance := <decimal>'0.00' }"
        )

    _expect_rejected(body, "a second Account with code ACC-1001")

    def check(client):
        return client.query_single(
            "select count((select Account filter .code = 'ACC-1001'))"
        )

    assert run_async(check) == 1, "A duplicate ACC-1001 account was persisted."


def test_database_rejects_zero_ledger_entry(reference):
    transfer_id = uuid.UUID(reference["id"])

    async def body(client):
        return await client.execute(
            """
            insert LedgerEntry {
                amount := <decimal>'0',
                account := assert_single((select Account filter .code = 'ACC-1004')),
                transfer := assert_single((select Transfer filter .id = <uuid>$tid)),
            }
            """,
            tid=transfer_id,
        )

    _expect_rejected(body, "a LedgerEntry with amount 0")


def test_database_rejects_two_entries_for_the_same_account_and_transfer(reference):
    transfer_id = uuid.UUID(reference["id"])

    async def body(client):
        return await client.execute(
            """
            insert LedgerEntry {
                amount := <decimal>'1.00',
                account := assert_single((select Account filter .code = <str>$code)),
                transfer := assert_single((select Transfer filter .id = <uuid>$tid)),
            }
            """,
            code=f"{TOKEN}-RA",
            tid=transfer_id,
        )

    _expect_rejected(body, "a second LedgerEntry for one (account, transfer) pair")

    def check(client):
        return _transfer_row(client, f"{TOKEN}-ref")

    row = run_async(check)
    assert row.entry_count == 2, (
        f"The reference transfer must still have exactly 2 entries, has {row.entry_count}."
    )


# ---------------------------------------------------------------------------
# 5. balance is derived, not stored
# ---------------------------------------------------------------------------


def test_balance_and_entries_are_derived_not_stored(api, gel_server):
    """A transfer written with raw EdgeQL must still be reflected by balance/entries."""
    funder = f"{TOKEN}-CMPF"
    code = f"{TOKEN}-CMP"

    async def body(client):
        await api.create_account(client, code=funder, opening_balance=D("10.00"))
        created = await api.create_account(client, code=code, opening_balance=D("0.00"))
        before = (
            await api.get_balance(client, account=funder),
            await api.get_balance(client, account=code),
        )
        raw = await client.query_single(
            """
            with
                moved := (insert Transfer {
                    idempotency_key := <str>$key,
                    amount := <decimal>'7.00',
                    sender := assert_single((select Account filter .code = <str>$funder)),
                    recipient := assert_single((select Account filter .code = <str>$code)),
                }),
                debit := (insert LedgerEntry {
                    amount := <decimal>'-7.00',
                    account := assert_single((select Account filter .code = <str>$funder)),
                    transfer := moved,
                }),
                credit := (insert LedgerEntry {
                    amount := <decimal>'7.00',
                    account := assert_single((select Account filter .code = <str>$code)),
                    transfer := moved,
                })
            select {
                tid := <str>moved.id,
                debit_id := <str>debit.id,
                credit_id := <str>credit.id,
            }
            """,
            key=f"{TOKEN}-raw-pair",
            funder=funder,
            code=code,
        )
        after = (
            await api.get_balance(client, account=funder),
            await api.get_balance(client, account=code),
        )
        linked = await client.query_single(
            "select count((select Transfer filter .id = <uuid>$tid).entries)",
            tid=uuid.UUID(raw.tid),
        )
        await client.execute(
            "delete LedgerEntry filter .id in {<uuid>$d, <uuid>$c}",
            d=uuid.UUID(raw.debit_id),
            c=uuid.UUID(raw.credit_id),
        )
        await client.execute(
            "delete Transfer filter .id = <uuid>$tid", tid=uuid.UUID(raw.tid)
        )
        restored = (
            await api.get_balance(client, account=funder),
            await api.get_balance(client, account=code),
        )
        return created, before, after, linked, restored

    created, before, after, linked, restored = run_async(body)
    assert created["balance"] == "0.00", (
        "A new account with opening_balance 0.00 must report balance '0.00', got "
        f"{created['balance']!r}"
    )
    assert before == ("10.00", "0.00"), f"Unexpected starting balances: {before}"
    assert after == ("3.00", "7.00"), (
        "`Account.balance` must be derived from the linked LedgerEntry objects: after a "
        "7.00 movement written directly with EdgeQL the balances must be "
        f"('3.00', '7.00'), got {after}"
    )
    assert linked == 2, (
        "`Transfer.entries` must be computed from the LedgerEntry objects that point at "
        f"the transfer (expected 2, got {linked})."
    )
    assert restored == ("10.00", "0.00"), (
        f"After deleting the raw entries the balances must return to the opening values, got {restored}"
    )


# ---------------------------------------------------------------------------
# 6. account creation
# ---------------------------------------------------------------------------


def test_create_account_happy_path_and_duplicate(api):
    async def body(client):
        created = await api.create_account(
            client, code=f"{TOKEN}-A", opening_balance=D("100.00")
        )
        duplicate = None
        try:
            await api.create_account(
                client, code=f"{TOKEN}-A", opening_balance=D("5.00")
            )
        except api.DuplicateAccount as exc:
            duplicate = exc
        count = await client.query_single(
            "select count((select Account filter .code = <str>$c))", c=f"{TOKEN}-A"
        )
        return created, duplicate, count

    created, duplicate, count = run_async(body)
    assert set(created) == {"id", "code", "balance"}, (
        f"`create_account` must return exactly the keys id, code, balance; got {sorted(created)}"
    )
    uuid.UUID(str(created["id"]))
    assert created["code"] == f"{TOKEN}-A", f"Wrong code echoed back: {created!r}"
    assert created["balance"] == "100.00", (
        f"Expected balance '100.00', got {created['balance']!r}"
    )
    assert duplicate is not None, (
        "Creating a second account with an existing code must raise DuplicateAccount."
    )
    assert count == 1, f"Expected exactly one {TOKEN}-A account, found {count}."


def test_create_account_rejects_invalid_opening_balance(api):
    async def body(client):
        raised = {}
        for label, value in (("negative", D("-1.00")), ("sub_cent", D("0.005"))):
            try:
                await api.create_account(
                    client,
                    code=f"{TOKEN}-BAD-{label}",
                    opening_balance=value,
                )
                raised[label] = None
            except api.InvalidAmount as exc:
                raised[label] = exc
        count = await client.query_single(
            "select count((select Account filter .code like <str>$p ++ '%'))",
            p=f"{TOKEN}-BAD-",
        )
        return raised, count

    raised, count = run_async(body)
    for label in ("negative", "sub_cent"):
        assert raised[label] is not None, (
            f"create_account with a {label} opening_balance must raise InvalidAmount."
        )
    assert count == 0, "A rejected create_account call must not persist an Account."


# ---------------------------------------------------------------------------
# 7. single transfer, replay and negative cases
# ---------------------------------------------------------------------------


def test_single_transfer_creates_balanced_entries(api):
    async def body(client):
        await api.create_account(client, code=f"{TOKEN}-B", opening_balance=D("0.00"))
        record = await api.create_transfer(
            client,
            sender=f"{TOKEN}-A",
            recipient=f"{TOKEN}-B",
            amount=D("30.25"),
            idempotency_key=f"{TOKEN}-k1",
        )
        sender_balance = await api.get_balance(client, account=f"{TOKEN}-A")
        recipient_balance = await api.get_balance(client, account=f"{TOKEN}-B")
        row = await _transfer_row(client, f"{TOKEN}-k1")
        entries = await _entries_of(client, f"{TOKEN}-k1")
        return record, sender_balance, recipient_balance, row, entries

    record, sender_balance, recipient_balance, row, entries = run_async(body)
    assert set(record) == {
        "id",
        "sender",
        "recipient",
        "amount",
        "idempotency_key",
        "created",
    }, f"`create_transfer` returned unexpected keys: {sorted(record)}"
    uuid.UUID(str(record["id"]))
    assert record["created"] is True, "A brand new transfer must report created=True."
    assert record["amount"] == "30.25", f"Expected amount '30.25', got {record['amount']!r}"
    assert record["sender"] == f"{TOKEN}-A" and record["recipient"] == f"{TOKEN}-B", (
        f"Wrong sender/recipient echoed back: {record!r}"
    )
    assert record["idempotency_key"] == f"{TOKEN}-k1", f"Wrong key echoed: {record!r}"
    assert sender_balance == "69.75", (
        f"Sender balance after transferring 30.25 out of 100.00 must be '69.75', got {sender_balance!r}"
    )
    assert recipient_balance == "30.25", (
        f"Recipient balance must be '30.25', got {recipient_balance!r}"
    )
    assert row is not None, "The transfer was not persisted."
    assert str(row.tid) == str(record["id"]), (
        "The returned id must be the id of the stored Transfer."
    )
    assert row.entry_count == 2, (
        f"A transfer must produce exactly two ledger entries, got {row.entry_count}."
    )
    assert D(row.entry_sum) == D("0"), (
        f"The two entries must sum to zero, got {row.entry_sum}."
    )
    got = {entry.account_code: D(entry.amount_str) for entry in entries}
    assert got == {f"{TOKEN}-A": D("-30.25"), f"{TOKEN}-B": D("30.25")}, (
        f"Expected a -30.25 debit on the sender and a +30.25 credit on the recipient, got {got}"
    )


def test_replaying_the_same_idempotency_key_does_not_move_money(api):
    async def body(client):
        replay = await api.create_transfer(
            client,
            sender=f"{TOKEN}-A",
            recipient=f"{TOKEN}-B",
            amount=D("99.00"),
            idempotency_key=f"{TOKEN}-k1",
        )
        sender_balance = await api.get_balance(client, account=f"{TOKEN}-A")
        recipient_balance = await api.get_balance(client, account=f"{TOKEN}-B")
        count = await client.query_single(
            "select count((select Transfer filter .idempotency_key = <str>$k))",
            k=f"{TOKEN}-k1",
        )
        entries = await _entries_of(client, f"{TOKEN}-k1")
        return replay, sender_balance, recipient_balance, count, len(entries)

    replay, sender_balance, recipient_balance, count, entry_count = run_async(body)
    assert replay["created"] is False, (
        "Replaying a used idempotency key must report created=False."
    )
    assert replay["amount"] == "30.25", (
        "Replaying must return the stored transfer, so the amount must stay '30.25', "
        f"got {replay['amount']!r}"
    )
    assert count == 1, f"Exactly one Transfer may carry the key, found {count}."
    assert entry_count == 2, f"The replay must not add entries, found {entry_count}."
    assert sender_balance == "69.75" and recipient_balance == "30.25", (
        f"Balances must be untouched by a replay, got {sender_balance!r}/{recipient_balance!r}"
    )


def test_invalid_transfers_are_rejected_without_side_effects(api):
    cases = [
        ("zero", dict(amount=D("0.00")), "InvalidAmount"),
        ("negative", dict(amount=D("-1.00")), "InvalidAmount"),
        ("sub_cent", dict(amount=D("0.005")), "InvalidAmount"),
        (
            "same_account",
            dict(sender=f"{TOKEN}-A", recipient=f"{TOKEN}-A", amount=D("1.00")),
            "SameAccountTransfer",
        ),
        (
            "unknown_recipient",
            dict(recipient=f"{TOKEN}-nope", amount=D("1.00")),
            "UnknownAccount",
        ),
        (
            "unknown_sender",
            dict(sender=f"{TOKEN}-nope", amount=D("1.00")),
            "UnknownAccount",
        ),
        (
            "insufficient",
            dict(sender=f"{TOKEN}-B", recipient=f"{TOKEN}-A", amount=D("30.26")),
            "InsufficientFunds",
        ),
        (
            "amount_beats_unknown_account",
            dict(recipient=f"{TOKEN}-nope", amount=D("-1.00")),
            "InvalidAmount",
        ),
        (
            "same_account_beats_unknown_account",
            dict(
                sender=f"{TOKEN}-nope",
                recipient=f"{TOKEN}-nope",
                amount=D("5.00"),
            ),
            "SameAccountTransfer",
        ),
    ]

    async def body(client):
        before_entries = await _entry_count(client)
        outcomes = {}
        for label, overrides, expected in cases:
            kwargs = dict(
                sender=f"{TOKEN}-A",
                recipient=f"{TOKEN}-B",
                amount=D("1.00"),
                idempotency_key=f"{TOKEN}-neg-{label}",
            )
            kwargs.update(overrides)
            expected_exc = getattr(api, expected)
            try:
                await api.create_transfer(client, **kwargs)
                outcomes[label] = "no-exception"
            except expected_exc:
                outcomes[label] = "ok"
            except api.LedgerError as exc:
                outcomes[label] = type(exc).__name__
        after_entries = await _entry_count(client)
        leaked = await client.query_single(
            "select count((select Transfer filter .idempotency_key like <str>$p ++ '%'))",
            p=f"{TOKEN}-neg-",
        )
        sender_balance = await api.get_balance(client, account=f"{TOKEN}-A")
        recipient_balance = await api.get_balance(client, account=f"{TOKEN}-B")
        return outcomes, before_entries, after_entries, leaked, sender_balance, recipient_balance

    (
        outcomes,
        before_entries,
        after_entries,
        leaked,
        sender_balance,
        recipient_balance,
    ) = run_async(body)
    for label, _overrides, expected in cases:
        assert outcomes[label] == "ok", (
            f"create_transfer case {label!r} should raise {expected}, got {outcomes[label]}"
        )
    assert leaked == 0, (
        f"Rejected transfers must not be persisted, found {leaked} of them."
    )
    assert before_entries == after_entries, (
        "Rejected transfers must not create ledger entries "
        f"({before_entries} -> {after_entries})."
    )
    assert sender_balance == "69.75" and recipient_balance == "30.25", (
        f"Balances must be unchanged, got {sender_balance!r}/{recipient_balance!r}"
    )


def test_unknown_account_lookups_are_rejected(api):
    async def body(client):
        results = {}
        try:
            await api.get_balance(client, account=f"{TOKEN}-nope")
            results["balance"] = "no-exception"
        except api.UnknownAccount:
            results["balance"] = "ok"
        try:
            await api.get_statement(client, account=f"{TOKEN}-nope")
            results["statement"] = "no-exception"
        except api.UnknownAccount:
            results["statement"] = "ok"
        return results

    results = run_async(body)
    assert results["balance"] == "ok", "get_balance on an unknown code must raise UnknownAccount."
    assert results["statement"] == "ok", (
        "get_statement on an unknown code must raise UnknownAccount."
    )


def test_transfer_may_drain_an_account_exactly(api):
    async def body(client):
        record = await api.create_transfer(
            client,
            sender=f"{TOKEN}-B",
            recipient=f"{TOKEN}-A",
            amount=D("30.25"),
            idempotency_key=f"{TOKEN}-drain",
        )
        return record, await api.get_balance(client, account=f"{TOKEN}-B")

    record, balance = run_async(body)
    assert record["created"] is True, "Draining an account exactly must succeed."
    assert balance == "0.00", (
        f"A fully drained account must report balance '0.00', got {balance!r}"
    )


# ---------------------------------------------------------------------------
# 8. statement
# ---------------------------------------------------------------------------


def test_statement_ordering_shape_and_limit(api):
    async def body(client):
        await api.create_account(client, code=f"{TOKEN}-S", opening_balance=D("20.00"))
        await api.create_account(client, code=f"{TOKEN}-C1", opening_balance=D("0.00"))
        await api.create_account(client, code=f"{TOKEN}-C2", opening_balance=D("0.00"))
        await api.create_transfer(
            client,
            sender=f"{TOKEN}-S",
            recipient=f"{TOKEN}-C1",
            amount=D("5.00"),
            idempotency_key=f"{TOKEN}-s01",
        )
        await api.create_transfer(
            client,
            sender=f"{TOKEN}-S",
            recipient=f"{TOKEN}-C2",
            amount=D("1.50"),
            idempotency_key=f"{TOKEN}-s02",
        )
        await api.create_transfer(
            client,
            sender=f"{TOKEN}-C1",
            recipient=f"{TOKEN}-S",
            amount=D("2.00"),
            idempotency_key=f"{TOKEN}-s03",
        )
        full = await api.get_statement(client, account=f"{TOKEN}-S")
        limited = await api.get_statement(client, account=f"{TOKEN}-S", limit=2)
        balance = await api.get_balance(client, account=f"{TOKEN}-S")
        return full, limited, balance

    full, limited, balance = run_async(body)
    assert isinstance(full, list) and len(full) == 3, (
        f"The statement of {TOKEN}-S must list exactly 3 transfers, got {full!r}"
    )
    for item in full:
        assert set(item) == set(STATEMENT_KEYS), (
            f"Statement rows must carry exactly {sorted(STATEMENT_KEYS)}, got {sorted(item)}"
        )
        uuid.UUID(str(item["transfer_id"]))
    keys = [item["idempotency_key"] for item in full]
    assert keys == [f"{TOKEN}-s03", f"{TOKEN}-s02", f"{TOKEN}-s01"], (
        f"The statement must be ordered newest first, got {keys}"
    )
    assert [item["direction"] for item in full] == ["credit", "debit", "debit"], (
        f"Unexpected directions: {[item['direction'] for item in full]}"
    )
    assert [item["amount"] for item in full] == ["2.00", "-1.50", "-5.00"], (
        f"Unexpected signed amounts: {[item['amount'] for item in full]}"
    )
    assert [item["counterparty"] for item in full] == [
        f"{TOKEN}-C1",
        f"{TOKEN}-C2",
        f"{TOKEN}-C1",
    ], f"Unexpected counterparties: {[item['counterparty'] for item in full]}"
    assert [item["idempotency_key"] for item in limited] == [
        f"{TOKEN}-s03",
        f"{TOKEN}-s02",
    ], f"`limit=2` must return only the two newest rows, got {limited!r}"
    assert balance == "15.50", (
        f"Expected {TOKEN}-S to hold '15.50' after 20.00 - 5.00 - 1.50 + 2.00, got {balance!r}"
    )


# ---------------------------------------------------------------------------
# 9. concurrency
# ---------------------------------------------------------------------------


def test_concurrent_transfer_storm_keeps_the_ledger_balanced(api):
    senders = [f"{TOKEN}-CS{i}" for i in range(8)]
    recipients = [f"{TOKEN}-CR{i}" for i in range(8)]

    async def body(client):
        for code in senders:
            await api.create_account(client, code=code, opening_balance=D("50.00"))
        for code in recipients:
            await api.create_account(client, code=code, opening_balance=D("0.00"))

        plan = []
        for i, sender in enumerate(senders):
            for j in (i, (i + 1) % len(recipients)):
                plan.append((sender, recipients[j], f"{TOKEN}-storm-{i}{j}"))

        started = time.monotonic()
        results = await asyncio.gather(
            *(
                api.create_transfer(
                    client,
                    sender=sender,
                    recipient=recipient,
                    amount=D("1.00"),
                    idempotency_key=key,
                )
                for sender, recipient, key in plan
            )
        )
        elapsed = time.monotonic() - started

        stored = await client.query_single(
            "select count((select Transfer filter .idempotency_key like <str>$p ++ '%'))",
            p=f"{TOKEN}-storm-",
        )
        entry_counts = await client.query(
            """
            select (
                select Transfer filter .idempotency_key like <str>$p ++ '%'
            ) { n := count(.entries) }
            """,
            p=f"{TOKEN}-storm-",
        )
        entry_sum = await client.query_single(
            """
            select <str>sum((
                select LedgerEntry
                filter .transfer.idempotency_key like <str>$p ++ '%'
            ).amount)
            """,
            p=f"{TOKEN}-storm-",
        )
        balances = {}
        for code in senders + recipients:
            balances[code] = await api.get_balance(client, account=code)
        negatives = await client.query_single(
            "select count((select Account filter .balance < 0))"
        )
        return results, elapsed, stored, [row.n for row in entry_counts], entry_sum, balances, negatives

    results, elapsed, stored, entry_counts, entry_sum, balances, negatives = run_async(
        body, timeout=300, concurrency=8
    )
    assert len(results) == 16, f"Expected 16 results, got {len(results)}"
    assert all(item["created"] is True for item in results), (
        "Every one of the 16 concurrent transfers uses a distinct key, so each must "
        f"report created=True; got {[item['created'] for item in results]}"
    )
    assert len({item["id"] for item in results}) == 16, (
        "The 16 concurrent transfers must be 16 distinct Transfer objects."
    )
    assert stored == 16, f"Expected 16 stored transfers, found {stored}."
    assert entry_counts == [2] * 16, (
        f"Every concurrent transfer must have exactly two entries, got {entry_counts}"
    )
    assert D(entry_sum) == D("0"), (
        f"The entries created by the storm must sum to zero, got {entry_sum}"
    )
    for code in senders:
        assert balances[code] == "48.00", (
            f"Sender {code} sent 2 x 1.00 out of 50.00 and must hold '48.00', got {balances[code]!r}"
        )
    for code in recipients:
        assert balances[code] == "2.00", (
            f"Recipient {code} received 2 x 1.00 and must hold '2.00', got {balances[code]!r}"
        )
    assert negatives == 0, f"{negatives} account(s) ended up with a negative balance."
    assert elapsed < 120, (
        f"16 concurrent transfers took {elapsed:.1f}s, which suggests a lock-up."
    )


def test_concurrent_calls_sharing_one_key_create_one_transfer(api):
    sender = f"{TOKEN}-DS"
    recipient = f"{TOKEN}-DR"
    key = f"{TOKEN}-dup"

    async def body(client):
        await api.create_account(client, code=sender, opening_balance=D("20.00"))
        await api.create_account(client, code=recipient, opening_balance=D("0.00"))
        results = await asyncio.gather(
            *(
                api.create_transfer(
                    client,
                    sender=sender,
                    recipient=recipient,
                    amount=D("2.00"),
                    idempotency_key=key,
                )
                for _ in range(6)
            )
        )
        stored = await client.query_single(
            "select count((select Transfer filter .idempotency_key = <str>$k))", k=key
        )
        row = await _transfer_row(client, key)
        balances = (
            await api.get_balance(client, account=sender),
            await api.get_balance(client, account=recipient),
        )
        return results, stored, row, balances

    results, stored, row, balances = run_async(body, timeout=300, concurrency=6)
    assert stored == 1, (
        f"Six concurrent calls sharing one idempotency key must store exactly one Transfer, found {stored}."
    )
    ids = {str(item["id"]) for item in results}
    assert ids == {str(row.tid)}, (
        f"All six calls must return the id of the single stored Transfer, got {ids}"
    )
    created_flags = [item["created"] for item in results]
    assert created_flags.count(True) == 1, (
        f"Exactly one of the six calls may report created=True, got {created_flags}"
    )
    assert row.entry_count == 2, (
        f"The single stored Transfer must have exactly two entries, got {row.entry_count}."
    )
    assert balances == ("18.00", "2.00"), (
        f"Money must move exactly once: expected ('18.00', '2.00'), got {balances}"
    )


# ---------------------------------------------------------------------------
# 10. randomised model check and global invariants
# ---------------------------------------------------------------------------


def test_randomised_sequence_matches_an_independent_model(api):
    rng = random.Random()
    codes = [f"{TOKEN}-R{i}" for i in range(5)]
    openings = {
        code: (D(rng.randrange(0, 20001)) / D(100)).quantize(D("0.01"))
        for code in codes
    }
    plan = []
    for n in range(18):
        sender, recipient = rng.sample(codes, 2)
        amount = (D(rng.randrange(1, 12001)) / D(100)).quantize(D("0.01"))
        plan.append((sender, recipient, amount, f"{TOKEN}-rnd-{n:02d}"))

    model = dict(openings)
    expectations = []
    for sender, recipient, amount, key in plan:
        if model[sender] >= amount:
            model[sender] -= amount
            model[recipient] += amount
            expectations.append("ok")
        else:
            expectations.append("insufficient")

    async def body(client):
        for code in codes:
            await api.create_account(client, code=code, opening_balance=openings[code])
        observed = []
        for sender, recipient, amount, key in plan:
            try:
                await api.create_transfer(
                    client,
                    sender=sender,
                    recipient=recipient,
                    amount=amount,
                    idempotency_key=key,
                )
                observed.append("ok")
            except api.InsufficientFunds:
                observed.append("insufficient")
            except api.LedgerError as exc:
                observed.append(type(exc).__name__)
        balances = {}
        for code in codes:
            balances[code] = await api.get_balance(client, account=code)
        entry_sum = await client.query_single(
            """
            select <str>sum((
                select LedgerEntry
                filter .transfer.idempotency_key like <str>$p ++ '%'
            ).amount)
            """,
            p=f"{TOKEN}-rnd-",
        )
        stored = await client.query_single(
            "select count((select Transfer filter .idempotency_key like <str>$p ++ '%'))",
            p=f"{TOKEN}-rnd-",
        )
        return observed, balances, entry_sum, stored

    observed, balances, entry_sum, stored = run_async(body, timeout=300)
    assert observed == expectations, (
        "The API disagreed with the reference model.\n"
        f"openings={ {k: str(v) for k, v in openings.items()} }\n"
        f"plan={[(s, r, str(a)) for s, r, a, _ in plan]}\n"
        f"expected={expectations}\nobserved={observed}"
    )
    for code in codes:
        assert balances[code] == f"{model[code]:.2f}", (
            f"Balance of {code} should be {model[code]:.2f}, got {balances[code]!r}"
        )
    assert D(entry_sum) == D("0"), (
        f"The randomised transfers must sum to zero, got {entry_sum}"
    )
    assert stored == expectations.count("ok"), (
        f"Expected {expectations.count('ok')} stored transfers, found {stored}."
    )


def test_global_invariants_hold(api):
    async def body(client):
        entry_sum = await client.query_single(
            """
            select <str>sum((
                select LedgerEntry
                filter .transfer.idempotency_key like <str>$p ++ '%'
            ).amount)
            """,
            p=f"{TOKEN}-",
        )
        rows = await client.query(
            """
            select (select Account filter .code like <str>$p ++ '%') {
                code,
                bal := <str>.balance,
                ob := <str>.opening_balance,
                esum := <str>sum(.<account[is LedgerEntry].amount),
            }
            """,
            p=f"{TOKEN}-",
        )
        negatives = await client.query_single(
            "select count((select Account filter .balance < 0))"
        )
        return entry_sum, rows, negatives

    entry_sum, rows, negatives = run_async(body)
    assert D(entry_sum) == D("0"), (
        f"Double-entry invariant violated: all entries must sum to zero, got {entry_sum}"
    )
    assert len(rows) > 0, "No verification accounts were found in the database."
    for row in rows:
        assert D(row.bal) == D(row.ob) + D(row.esum), (
            f"balance of {row.code} ({row.bal}) must equal opening_balance ({row.ob}) "
            f"plus the sum of its entries ({row.esum})."
        )
        assert D(row.bal) >= D("0"), (
            f"Account {row.code} ended with a negative balance ({row.bal})."
        )
    assert negatives == 0, f"{negatives} account(s) hold a negative balance."
