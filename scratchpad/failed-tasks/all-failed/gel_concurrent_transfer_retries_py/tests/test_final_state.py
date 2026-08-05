"""Final-state verification for the gel_concurrent_transfer_retries_py task.

The transfer service is driven over HTTP exactly as specified, while every
database fact is verified independently with the Python Gel client (never
trusting the service's own answers).
"""

import json
import os
import signal
import subprocess
import time
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests

PROJECT_DIR = "/home/user/ledger"
APP_START_COMMAND = ["bash", "/home/user/ledger/start.sh"]
APP_LOG = "/tmp/ledger_app.log"
GEL_START_SCRIPT = "/usr/local/bin/start-gel.sh"

GEL_READY_URL = "http://localhost:5656/server/status/ready"
HOST = "127.0.0.1"
APP_PORT = 8080
BASE_URL = f"http://{HOST}:{APP_PORT}"
REQUEST_TIMEOUT = 120

SEEDED_ACC_PREFIX = "ACC-"
SEEDED_ACC_COUNT = 1000
SEEDED_ACC_TOTAL = 250000000
RESERVED_ACCOUNTS = {
    "RSV-AUDIT-1": 900000,
    "RSV-AUDIT-2": 125000,
    "RSV-AUDIT-3": 0,
    "RSV-AUDIT-4": 7,
}

APPLIED_KEYS = [
    "status",
    "transfer_id",
    "source_code",
    "target_code",
    "amount_cents",
    "source_balance_after",
    "target_balance_after",
]
REJECTED_KEYS = ["status", "transfer_id", "reason"]
ENTRY_KEYS = [
    "transfer_id",
    "source_code",
    "target_code",
    "amount_cents",
    "source_balance_after",
    "target_balance_after",
]

INITIAL_BALANCES = {
    "SER-A": 100000,
    "SER-B": 25000,
    "HOT-A": 5000000,
    "HOT-B": 5000000,
    "CAP-A": 1000,
    "CAP-B": 0,
    "DUP-A": 10000,
    "DUP-B": 0,
    "EDG-A": 50,
    "EDG-B": 0,
}

HOT_TRANSFERS = 96
HOT_CLIENTS = 8
HOT_FORWARD_AMOUNT = 100
HOT_BACKWARD_AMOUNT = 70
EXT_TRANSFERS = 24
EXT_AMOUNT = 5
CAP_TRANSFERS = 12
CAP_AMOUNT = 200
DUP_CLIENTS = 12
DUP_AMOUNT = 250


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _gel_server_ready(timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(GEL_READY_URL, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def _pids_listening(port: int):
    """Return PIDs owning a listening TCP socket on *port* (Linux /proc)."""
    hex_port = f"{port:04X}"
    inodes = set()
    for proc_file in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(proc_file) as fh:
                lines = fh.read().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) < 10:
                continue
            if fields[3] != "0A":  # TCP_LISTEN
                continue
            local = fields[1].split(":")
            if len(local) == 2 and local[1].upper() == hex_port:
                inodes.add(fields[9])
    if not inodes:
        return []

    pids = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        fd_dir = f"/proc/{entry}/fd"
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(os.path.join(fd_dir, fd))
            except OSError:
                continue
            if target.startswith("socket:[") and target[8:-1] in inodes:
                pids.append(int(entry))
                break
    return pids


def _kill_port_listeners(port: int) -> None:
    for pid in _pids_listening(port):
        for killer in (
            lambda p: os.killpg(os.getpgid(p), signal.SIGTERM),
            lambda p: os.kill(p, signal.SIGTERM),
        ):
            try:
                killer(pid)
            except Exception:
                continue
            break
    deadline = time.time() + 20
    while time.time() < deadline and _pids_listening(port):
        time.sleep(0.5)
    for pid in _pids_listening(port):
        for killer in (
            lambda p: os.killpg(os.getpgid(p), signal.SIGKILL),
            lambda p: os.kill(p, signal.SIGKILL),
        ):
            try:
                killer(pid)
            except Exception:
                continue
            break
    time.sleep(1)


def _app_healthy() -> bool:
    try:
        resp = requests.get(f"{BASE_URL}/health", timeout=5)
    except requests.RequestException:
        return False
    if resp.status_code != 200:
        return False
    try:
        return resp.json().get("status") == "ok"
    except ValueError:
        return False


class AppRunner:
    """Starts/stops `bash /home/user/ledger/start.sh` and tails its log."""

    def __init__(self):
        self.proc = None
        self._printed_lines = 0

    def start(self) -> None:
        _kill_port_listeners(APP_PORT)
        log = open(APP_LOG, "a", buffering=1)
        log.write(f"\n=== starting service at {time.strftime('%H:%M:%S')} ===\n")
        self.proc = subprocess.Popen(
            APP_START_COMMAND,
            cwd=PROJECT_DIR,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        deadline = time.time() + 120
        while time.time() < deadline:
            if _app_healthy():
                return
            if self.proc.poll() is not None:
                self.dump_log("service exited during startup")
                raise AssertionError(
                    "The start command "
                    f"{' '.join(APP_START_COMMAND)} exited with code "
                    f"{self.proc.returncode} before serving {BASE_URL}/health."
                )
            time.sleep(1)
        self.dump_log("service did not become healthy")
        raise AssertionError(
            f"The service did not answer GET {BASE_URL}/health with "
            '{"status": "ok"} within 120 seconds.'
        )

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            except Exception:
                pass
            try:
                self.proc.wait(timeout=20)
            except Exception:
                try:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                except Exception:
                    pass
        _kill_port_listeners(APP_PORT)
        self.proc = None

    def dump_log(self, tag: str) -> None:
        try:
            with open(APP_LOG) as fh:
                lines = fh.readlines()
        except OSError:
            return
        new = lines[self._printed_lines :]
        self._printed_lines = len(lines)
        print(f"===== [{tag}] service log =====")
        print("".join(new[-400:]))
        print(f"===== [{tag}] end of service log =====")


def post_transfer(payload):
    resp = requests.post(
        f"{BASE_URL}/transfers", json=payload, timeout=REQUEST_TIMEOUT
    )
    return _decode(resp)


def post_raw(body, content_type="application/json"):
    resp = requests.post(
        f"{BASE_URL}/transfers",
        data=body,
        headers={"Content-Type": content_type},
        timeout=REQUEST_TIMEOUT,
    )
    return _decode(resp)


def get_json(path):
    resp = requests.get(f"{BASE_URL}{path}", timeout=REQUEST_TIMEOUT)
    return _decode(resp)


def _decode(resp):
    try:
        body = json.loads(resp.text)
    except ValueError:
        body = None
    keys = list(body.keys()) if isinstance(body, dict) else None
    return {
        "status_code": resp.status_code,
        "body": body,
        "keys": keys,
        "text": resp.text,
    }


def _query_json(conn, query, **kwargs):
    return json.loads(conn.query_json(query, **kwargs))


def _balance(conn, code):
    rows = _query_json(
        conn,
        """
        select default::Account { balance_cents }
        filter .code = <str>$code
        """,
        code=code,
    )
    assert len(rows) == 1, f"Expected exactly one account with code {code}."
    return rows[0]["balance_cents"]


def _entries_for_account(conn, code):
    return _query_json(
        conn,
        """
        select default::LedgerEntry {
            transfer_id,
            amount_cents,
            source_balance_after,
            target_balance_after,
            source_code := .source.code,
            target_code := .target.code,
        }
        filter .source.code = <str>$code or .target.code = <str>$code
        """,
        code=code,
    )


def _entry(conn, transfer_id):
    rows = _query_json(
        conn,
        """
        select default::LedgerEntry {
            transfer_id,
            amount_cents,
            source_balance_after,
            target_balance_after,
            applied_at,
            source_code := .source.code,
            target_code := .target.code,
        }
        filter .transfer_id = <str>$tid
        """,
        tid=transfer_id,
    )
    return rows


def _entries_with_prefix(conn, prefix):
    return _query_json(
        conn,
        """
        select default::LedgerEntry {
            transfer_id,
            amount_cents,
            source_balance_after,
            target_balance_after,
            source_code := .source.code,
            target_code := .target.code,
        }
        filter .transfer_id like <str>$prefix ++ '%'
        """,
        prefix=prefix,
    )


def _chain_report(entries, code, starting_balance, current_balance):
    befores = []
    afters = []
    for entry in entries:
        if entry["source_code"] == code:
            after = entry["source_balance_after"]
            before = after + entry["amount_cents"]
        elif entry["target_code"] == code:
            after = entry["target_balance_after"]
            before = after - entry["amount_cents"]
        else:
            continue
        befores.append(before)
        afters.append(after)
    left = sorted(befores + [current_balance])
    right = sorted(afters + [starting_balance])
    return left, right


def _direct_transfer(conn, transfer_id, source_code, target_code, amount):
    """Apply a transfer straight against the database, in one transaction."""
    source_after = 0
    target_after = 0
    for tx in conn.transaction():
        with tx:
            rows = json.loads(
                tx.query_json(
                    """
                    select default::Account { code, balance_cents }
                    filter .code in {<str>$src, <str>$dst}
                    """,
                    src=source_code,
                    dst=target_code,
                )
            )
            balances = {row["code"]: row["balance_cents"] for row in rows}
            source_after = balances[source_code] - amount
            target_after = balances[target_code] + amount
            tx.query_json(
                """
                update default::Account
                filter .code = <str>$src
                set { balance_cents := <int64>$val }
                """,
                src=source_code,
                val=source_after,
            )
            tx.query_json(
                """
                update default::Account
                filter .code = <str>$dst
                set { balance_cents := <int64>$val }
                """,
                dst=target_code,
                val=target_after,
            )
            tx.query_json(
                """
                insert default::LedgerEntry {
                    transfer_id := <str>$tid,
                    amount_cents := <int64>$amount,
                    source := (
                        select default::Account filter .code = <str>$src
                    ),
                    target := (
                        select default::Account filter .code = <str>$dst
                    ),
                    source_balance_after := <int64>$sa,
                    target_balance_after := <int64>$ta,
                }
                """,
                tid=transfer_id,
                amount=amount,
                src=source_code,
                dst=target_code,
                sa=source_after,
                ta=target_after,
            )
    return source_after, target_after


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def gel_server():
    if not _gel_server_ready():
        proc = subprocess.run(
            ["bash", GEL_START_SCRIPT],
            capture_output=True,
            text=True,
            timeout=900,
        )
        if proc.returncode != 0 and not _gel_server_ready():
            pytest.fail(
                f"Could not start the local Gel server via {GEL_START_SCRIPT}: "
                f"rc={proc.returncode}\nstdout:\n{proc.stdout}\n"
                f"stderr:\n{proc.stderr}"
            )
    deadline = time.time() + 600
    while time.time() < deadline:
        if _gel_server_ready():
            break
        time.sleep(2)
    else:
        pytest.fail(
            f"The local Gel server never became ready (polled {GEL_READY_URL})."
        )
    return True


@pytest.fixture(scope="session")
def client(gel_server):
    import gel

    last_error = None
    conn = None
    deadline = time.time() + 180
    while time.time() < deadline:
        try:
            conn = gel.create_client(timeout=120).with_retry_options(
                # Many attempts with a small, capped backoff: this client
                # competes with the service under test for the same rows.
                gel.RetryOptions(
                    attempts=96,
                    backoff=lambda attempt: 0.05 + 0.05 * min(attempt, 8),
                )
            )
            conn.query_single("select 1")
            break
        except Exception as exc:  # pragma: no cover - startup robustness
            last_error = exc
            conn = None
            time.sleep(3)
    if conn is None:
        pytest.fail(f"Could not connect to the local Gel instance: {last_error!r}")
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture(scope="session")
def nonce():
    return uuid.uuid4().hex[:12]


@pytest.fixture(scope="session")
def codes(nonce):
    mapping = {name: f"VRF{nonce}-{name}" for name in INITIAL_BALANCES}
    mapping["MISSING"] = f"VRF{nonce}-NOPE"
    return mapping


@pytest.fixture(scope="session")
def tid(nonce):
    return lambda suffix: f"TR{nonce}-{suffix}"


@pytest.fixture(scope="session")
def fixture_accounts(client, codes):
    """Create the verification accounts directly in the database."""
    for name, balance in INITIAL_BALANCES.items():
        client.query_json(
            """
            insert default::Account {
                code := <str>$code,
                balance_cents := <int64>$balance,
            }
            """,
            code=codes[name],
            balance=balance,
        )
    total = _query_json(
        client, "select sum(default::Account.balance_cents)"
    )[0]
    return {"total_after_setup": total}


@pytest.fixture(scope="session")
def app(gel_server, fixture_accounts):
    if os.path.exists(APP_LOG):
        os.remove(APP_LOG)
    runner = AppRunner()
    runner.start()
    runner.dump_log("startup")
    try:
        yield runner
    finally:
        runner.dump_log("teardown")
        runner.stop()


@pytest.fixture(scope="session")
def serial_phase(app, client, codes, tid):
    results = {}
    results["ser_1"] = post_transfer(
        {
            "transfer_id": tid("ser-1"),
            "source_code": codes["SER-A"],
            "target_code": codes["SER-B"],
            "amount_cents": 3000,
        }
    )
    results["get_entry_1"] = get_json(f"/transfers/{tid('ser-1')}")
    results["get_account_a"] = get_json(f"/accounts/{codes['SER-A']}")
    results["ser_2"] = post_transfer(
        {
            "transfer_id": tid("ser-2"),
            "source_code": codes["SER-A"],
            "target_code": codes["SER-B"],
            "amount_cents": 97000,
        }
    )
    results["ser_3"] = post_transfer(
        {
            "transfer_id": tid("ser-3"),
            "source_code": codes["SER-A"],
            "target_code": codes["SER-B"],
            "amount_cents": 1,
        }
    )
    results["duplicate_ser_1"] = post_transfer(
        {
            "transfer_id": tid("ser-1"),
            "source_code": codes["DUP-A"],
            "target_code": codes["DUP-B"],
            "amount_cents": 5,
        }
    )
    results["unknown_account_read"] = get_json(f"/accounts/{codes['MISSING']}")
    results["unknown_transfer_read"] = get_json(f"/transfers/{tid('nope')}")
    app.dump_log("serial phase")
    return results


@pytest.fixture(scope="session")
def rejection_phase(serial_phase, client, codes, tid):
    before = {
        name: _balance(client, codes[name]) for name in INITIAL_BALANCES
    }
    cases = {}

    def payload(suffix, source, target, amount):
        return {
            "transfer_id": tid(suffix),
            "source_code": source,
            "target_code": target,
            "amount_cents": amount,
        }

    cases["unknown_source"] = (
        post_transfer(
            payload("rej-1", codes["MISSING"], codes["SER-B"], 100)
        ),
        404,
        "unknown_account",
        tid("rej-1"),
    )
    cases["unknown_target"] = (
        post_transfer(
            payload("rej-2", codes["SER-B"], codes["MISSING"], 100)
        ),
        404,
        "unknown_account",
        tid("rej-2"),
    )
    cases["same_account"] = (
        post_transfer(payload("rej-3", codes["SER-B"], codes["SER-B"], 100)),
        422,
        "same_account",
        tid("rej-3"),
    )
    cases["amount_zero"] = (
        post_transfer(payload("rej-4", codes["SER-B"], codes["SER-A"], 0)),
        422,
        "invalid_amount",
        tid("rej-4"),
    )
    cases["amount_negative"] = (
        post_transfer(payload("rej-5", codes["SER-B"], codes["SER-A"], -500)),
        422,
        "invalid_amount",
        tid("rej-5"),
    )
    cases["amount_fractional"] = (
        post_transfer(payload("rej-6", codes["SER-B"], codes["SER-A"], 10.5)),
        422,
        "invalid_amount",
        tid("rej-6"),
    )
    cases["amount_string"] = (
        post_transfer(payload("rej-7", codes["SER-B"], codes["SER-A"], "500")),
        422,
        "invalid_amount",
        tid("rej-7"),
    )
    cases["amount_boolean"] = (
        post_transfer(payload("rej-8", codes["SER-B"], codes["SER-A"], True)),
        422,
        "invalid_amount",
        tid("rej-8"),
    )
    cases["missing_amount_key"] = (
        post_transfer(
            {
                "transfer_id": tid("rej-9"),
                "source_code": codes["SER-B"],
                "target_code": codes["SER-A"],
            }
        ),
        400,
        "invalid_request",
        tid("rej-9"),
    )
    extra = payload("rej-10", codes["SER-B"], codes["SER-A"], 100)
    extra["note"] = "x"
    cases["extra_key"] = (
        post_transfer(extra),
        400,
        "invalid_request",
        tid("rej-10"),
    )
    cases["empty_source_code"] = (
        post_transfer(payload("rej-11", "", codes["SER-A"], 100)),
        400,
        "invalid_request",
        tid("rej-11"),
    )
    cases["non_string_transfer_id"] = (
        post_transfer(
            {
                "transfer_id": 123,
                "source_code": codes["SER-B"],
                "target_code": codes["SER-A"],
                "amount_cents": 100,
            }
        ),
        400,
        "invalid_request",
        None,
    )
    cases["not_json_body"] = (
        post_raw("not json"),
        400,
        "invalid_request",
        None,
    )
    cases["precedence_amount_over_unknown"] = (
        post_transfer(
            payload("rej-12", codes["MISSING"], f"{codes['MISSING']}-2", 0)
        ),
        422,
        "invalid_amount",
        tid("rej-12"),
    )
    cases["precedence_same_over_unknown"] = (
        post_transfer(
            payload("rej-13", codes["MISSING"], codes["MISSING"], 100)
        ),
        422,
        "same_account",
        tid("rej-13"),
    )
    cases["precedence_amount_over_duplicate"] = (
        post_transfer(payload("ser-1", codes["SER-B"], codes["SER-A"], 0)),
        422,
        "invalid_amount",
        tid("ser-1"),
    )

    after = {name: _balance(client, codes[name]) for name in INITIAL_BALANCES}
    rejected_ids = [tid(f"rej-{i}") for i in range(1, 14)]
    return {
        "cases": cases,
        "balances_before": before,
        "balances_after": after,
        "rejected_ids": rejected_ids,
    }


@pytest.fixture(scope="session")
def hot_phase(rejection_phase, app, client, codes, tid):
    payloads = []
    for index in range(HOT_TRANSFERS):
        if index % 2 == 0:
            payloads.append(
                {
                    "transfer_id": tid(f"hot-{index}"),
                    "source_code": codes["HOT-A"],
                    "target_code": codes["HOT-B"],
                    "amount_cents": HOT_FORWARD_AMOUNT,
                }
            )
        else:
            payloads.append(
                {
                    "transfer_id": tid(f"hot-{index}"),
                    "source_code": codes["HOT-B"],
                    "target_code": codes["HOT-A"],
                    "amount_cents": HOT_BACKWARD_AMOUNT,
                }
            )

    external_errors = []

    def external_writer():
        for index in range(EXT_TRANSFERS):
            try:
                _direct_transfer(
                    client,
                    tid(f"ext-{index}"),
                    codes["HOT-A"],
                    codes["HOT-B"],
                    EXT_AMOUNT,
                )
            except Exception as exc:  # pragma: no cover - reported below
                external_errors.append(repr(exc))
                return

    started = time.time()
    with ThreadPoolExecutor(max_workers=HOT_CLIENTS + 1) as pool:
        writer = pool.submit(external_writer)
        responses = list(pool.map(post_transfer, payloads))
        writer.result()
    elapsed = time.time() - started
    app.dump_log("parallel phase")

    return {
        "payloads": payloads,
        "responses": responses,
        "elapsed": elapsed,
        "external_errors": external_errors,
    }


@pytest.fixture(scope="session")
def cap_phase(hot_phase, app, client, codes, tid):
    payloads = [
        {
            "transfer_id": tid(f"cap-{index}"),
            "source_code": codes["CAP-A"],
            "target_code": codes["CAP-B"],
            "amount_cents": CAP_AMOUNT,
        }
        for index in range(CAP_TRANSFERS)
    ]
    with ThreadPoolExecutor(max_workers=CAP_TRANSFERS) as pool:
        responses = list(pool.map(post_transfer, payloads))
    app.dump_log("overdraft phase")
    return {"responses": responses}


@pytest.fixture(scope="session")
def dup_phase(cap_phase, app, client, codes, tid):
    payload = {
        "transfer_id": tid("dup"),
        "source_code": codes["DUP-A"],
        "target_code": codes["DUP-B"],
        "amount_cents": DUP_AMOUNT,
    }
    with ThreadPoolExecutor(max_workers=DUP_CLIENTS) as pool:
        responses = list(
            pool.map(post_transfer, [dict(payload) for _ in range(DUP_CLIENTS)])
        )
    app.dump_log("duplicate phase")
    return {"responses": responses}


@pytest.fixture(scope="session")
def edge_phase(dup_phase, app, client, codes, tid):
    results = {
        "over": post_transfer(
            {
                "transfer_id": tid("edg-1"),
                "source_code": codes["EDG-A"],
                "target_code": codes["EDG-B"],
                "amount_cents": 51,
            }
        ),
        "exact": post_transfer(
            {
                "transfer_id": tid("edg-2"),
                "source_code": codes["EDG-A"],
                "target_code": codes["EDG-B"],
                "amount_cents": 50,
            }
        ),
        "after_empty": post_transfer(
            {
                "transfer_id": tid("edg-3"),
                "source_code": codes["EDG-A"],
                "target_code": codes["EDG-B"],
                "amount_cents": 1,
            }
        ),
    }
    app.dump_log("boundary phase")
    return results


@pytest.fixture(scope="session")
def restart_phase(edge_phase, app, client, codes, nonce, tid):
    snapshot = _entries_with_prefix(client, f"TR{nonce}-")
    app.stop()
    app.start()
    app.dump_log("after restart")
    results = {
        "snapshot": snapshot,
        "entry_read": get_json(f"/transfers/{tid('ser-1')}"),
        "duplicate_again": post_transfer(
            {
                "transfer_id": tid("dup"),
                "source_code": codes["DUP-A"],
                "target_code": codes["DUP-B"],
                "amount_cents": DUP_AMOUNT,
            }
        ),
    }
    return results


@pytest.fixture(scope="session")
def post_restart_transfer(restart_phase, app, client, codes, tid):
    result = post_transfer(
        {
            "transfer_id": tid("post-1"),
            "source_code": codes["DUP-B"],
            "target_code": codes["DUP-A"],
            "amount_cents": DUP_AMOUNT,
        }
    )
    app.dump_log("post restart transfer")
    return result


# ---------------------------------------------------------------------------
# 1. schema shape
# ---------------------------------------------------------------------------
def test_ledger_entry_type_shape(client, fixture_accounts):
    rows = _query_json(
        client,
        """
        select schema::ObjectType {
            name,
            properties: { name, required, cardinality, target: { name } },
            links: { name, required, cardinality, target: { name } },
        }
        filter .name = 'default::LedgerEntry'
        """,
    )
    assert len(rows) == 1, (
        "Expected the object type 'default::LedgerEntry' to exist in branch "
        f"main, found {len(rows)} matching types."
    )
    props = {p["name"]: p for p in rows[0]["properties"]}
    expected_props = {
        "transfer_id": "std::str",
        "amount_cents": "std::int64",
        "source_balance_after": "std::int64",
        "target_balance_after": "std::int64",
        "applied_at": "std::datetime",
    }
    for name, target in expected_props.items():
        assert name in props, f"default::LedgerEntry is missing '{name}'."
        assert props[name]["required"] is True, (
            f"default::LedgerEntry.{name} must be required."
        )
        assert props[name]["cardinality"] == "One", (
            f"default::LedgerEntry.{name} must be single-valued."
        )
        assert props[name]["target"]["name"] == target, (
            f"default::LedgerEntry.{name} must have type {target}, found "
            f"{props[name]['target']['name']}."
        )

    links = {l["name"]: l for l in rows[0]["links"]}
    for name in ("source", "target"):
        assert name in links, f"default::LedgerEntry is missing link '{name}'."
        assert links[name]["required"] is True, (
            f"default::LedgerEntry.{name} must be a required link."
        )
        assert links[name]["cardinality"] == "One", (
            f"default::LedgerEntry.{name} must be a single link."
        )
        assert links[name]["target"]["name"] == "default::Account", (
            f"default::LedgerEntry.{name} must link to default::Account, found "
            f"{links[name]['target']['name']}."
        )


def test_transfer_id_is_unique(client, fixture_accounts):
    rows = _query_json(
        client,
        """
        select schema::ObjectType {
            properties: { name, constraints: { name } },
        }
        filter .name = 'default::LedgerEntry'
        """,
    )
    assert rows, "default::LedgerEntry could not be introspected."
    matches = [p for p in rows[0]["properties"] if p["name"] == "transfer_id"]
    assert matches, "default::LedgerEntry has no 'transfer_id' property."
    names = {c["name"] for c in matches[0]["constraints"]}
    assert "std::exclusive" in names, (
        "default::LedgerEntry.transfer_id must be unique (exclusive), found "
        f"constraints {sorted(names)}."
    )


def test_ledger_entry_insertable_by_other_writers(client, fixture_accounts, codes, nonce):
    """Another writer must be able to append an entry using only the
    documented pointers (everything else is optional or has a default)."""
    probe_id = f"PROBE{nonce}"
    try:
        client.query_json(
            """
            insert default::LedgerEntry {
                transfer_id := <str>$tid,
                amount_cents := <int64>1,
                source := (select default::Account filter .code = <str>$src),
                target := (select default::Account filter .code = <str>$dst),
                source_balance_after := <int64>0,
                target_balance_after := <int64>0,
            }
            """,
            tid=probe_id,
            src=codes["SER-A"],
            dst=codes["SER-B"],
        )
    except Exception as exc:
        pytest.fail(
            "An independent writer must be able to insert a default::LedgerEntry "
            "with only transfer_id, amount_cents, source, target, "
            "source_balance_after and target_balance_after (applied_at has to be "
            f"filled in automatically and any extra pointer must be optional or "
            f"have a default), but the insert failed: {exc!r}"
        )
    rows = _entry(client, probe_id)
    assert len(rows) == 1, (
        f"Expected the probe ledger entry {probe_id} to be stored once, found "
        f"{len(rows)}."
    )
    assert rows[0]["applied_at"], (
        "applied_at must be populated automatically when a LedgerEntry is "
        f"created, found {rows[0]['applied_at']!r}."
    )
    client.query_json(
        "delete default::LedgerEntry filter .transfer_id = <str>$tid",
        tid=probe_id,
    )


def test_account_type_still_intact(client, fixture_accounts):
    rows = _query_json(
        client,
        """
        select schema::ObjectType {
            properties: { name, required, cardinality, target: { name },
                          constraints: { name } },
        }
        filter .name = 'default::Account'
        """,
    )
    assert rows, "default::Account no longer exists in branch main."
    props = {p["name"]: p for p in rows[0]["properties"]}
    for name, target in (("code", "std::str"), ("balance_cents", "std::int64")):
        assert name in props, f"default::Account lost the property '{name}'."
        assert props[name]["required"] is True, (
            f"default::Account.{name} must still be required."
        )
        assert props[name]["cardinality"] == "One", (
            f"default::Account.{name} must still be single-valued."
        )
        assert props[name]["target"]["name"] == target, (
            f"default::Account.{name} must still be {target}."
        )
    code_constraints = {c["name"] for c in props["code"]["constraints"]}
    assert "std::exclusive" in code_constraints, (
        "default::Account.code must still be exclusive, found "
        f"{sorted(code_constraints)}."
    )


# ---------------------------------------------------------------------------
# 2. seeded data regression
# ---------------------------------------------------------------------------
def test_seeded_filler_accounts_untouched(client, fixture_accounts):
    count = _query_json(
        client,
        """
        select count((
            select default::Account filter .code like <str>$prefix ++ '%'
        ))
        """,
        prefix=SEEDED_ACC_PREFIX,
    )[0]
    assert count == SEEDED_ACC_COUNT, (
        f"Expected {SEEDED_ACC_COUNT} accounts with a code starting with "
        f"'{SEEDED_ACC_PREFIX}', found {count}."
    )
    total = _query_json(
        client,
        """
        select sum((
            select default::Account filter .code like <str>$prefix ++ '%'
        ).balance_cents)
        """,
        prefix=SEEDED_ACC_PREFIX,
    )[0]
    assert total == SEEDED_ACC_TOTAL, (
        f"The seeded '{SEEDED_ACC_PREFIX}' accounts must still hold "
        f"{SEEDED_ACC_TOTAL} cents in total, found {total}."
    )


def test_seeded_reserved_accounts_untouched(client, fixture_accounts):
    rows = _query_json(
        client,
        """
        select default::Account { code, balance_cents }
        filter .code like 'RSV-%'
        """,
    )
    found = {row["code"]: row["balance_cents"] for row in rows}
    assert found == RESERVED_ACCOUNTS, (
        f"The reserved accounts must still be {RESERVED_ACCOUNTS}, found "
        f"{found}."
    )


# ---------------------------------------------------------------------------
# 3. serial happy path
# ---------------------------------------------------------------------------
def test_serial_transfer_response(serial_phase, codes, tid):
    result = serial_phase["ser_1"]
    assert result["status_code"] == 201, (
        "POST /transfers for a valid transfer must answer 201, got "
        f"{result['status_code']} with body {result['text']!r}."
    )
    assert result["keys"] == APPLIED_KEYS, (
        f"The applied response must have exactly the keys {APPLIED_KEYS} in "
        f"that order, got {result['keys']}."
    )
    body = result["body"]
    assert body["status"] == "applied", (
        f"Expected status 'applied', got {body['status']!r}."
    )
    assert body["transfer_id"] == tid("ser-1"), (
        f"Expected transfer_id {tid('ser-1')!r}, got {body['transfer_id']!r}."
    )
    assert body["source_code"] == codes["SER-A"], (
        f"Expected source_code {codes['SER-A']!r}, got {body['source_code']!r}."
    )
    assert body["target_code"] == codes["SER-B"], (
        f"Expected target_code {codes['SER-B']!r}, got {body['target_code']!r}."
    )
    assert body["amount_cents"] == 3000, (
        f"Expected amount_cents 3000, got {body['amount_cents']!r}."
    )
    assert body["source_balance_after"] == 97000, (
        f"Expected source_balance_after 97000, got "
        f"{body['source_balance_after']!r}."
    )
    assert body["target_balance_after"] == 28000, (
        f"Expected target_balance_after 28000, got "
        f"{body['target_balance_after']!r}."
    )


def test_serial_transfer_persisted_in_database(serial_phase, client, codes, tid):
    rows = _entry(client, tid("ser-1"))
    assert len(rows) == 1, (
        f"Expected exactly one LedgerEntry with transfer_id {tid('ser-1')}, "
        f"found {len(rows)}."
    )
    entry = rows[0]
    assert entry["amount_cents"] == 3000, (
        f"Stored amount_cents must be 3000, found {entry['amount_cents']}."
    )
    assert entry["source_code"] == codes["SER-A"], (
        f"Stored source must be {codes['SER-A']}, found {entry['source_code']}."
    )
    assert entry["target_code"] == codes["SER-B"], (
        f"Stored target must be {codes['SER-B']}, found {entry['target_code']}."
    )
    assert entry["source_balance_after"] == 97000, (
        "Stored source_balance_after must be 97000, found "
        f"{entry['source_balance_after']}."
    )
    assert entry["target_balance_after"] == 28000, (
        "Stored target_balance_after must be 28000, found "
        f"{entry['target_balance_after']}."
    )
    assert entry["applied_at"], (
        "applied_at must be populated automatically on every LedgerEntry, "
        f"found {entry['applied_at']!r}."
    )


def test_get_transfer_returns_stored_entry(serial_phase, codes, tid):
    result = serial_phase["get_entry_1"]
    assert result["status_code"] == 200, (
        f"GET /transfers/{tid('ser-1')} must answer 200, got "
        f"{result['status_code']} with body {result['text']!r}."
    )
    assert result["keys"] == ENTRY_KEYS, (
        f"The ledger-entry response must have exactly {ENTRY_KEYS} in that "
        f"order, got {result['keys']}."
    )
    assert result["body"] == {
        "transfer_id": tid("ser-1"),
        "source_code": codes["SER-A"],
        "target_code": codes["SER-B"],
        "amount_cents": 3000,
        "source_balance_after": 97000,
        "target_balance_after": 28000,
    }, f"Unexpected ledger-entry body: {result['body']!r}."


def test_get_account_reports_balance(serial_phase, codes):
    result = serial_phase["get_account_a"]
    assert result["status_code"] == 200, (
        f"GET /accounts/{codes['SER-A']} must answer 200, got "
        f"{result['status_code']} with body {result['text']!r}."
    )
    assert result["keys"] == ["code", "balance_cents"], (
        "The account response must have exactly the keys ['code', "
        f"'balance_cents'] in that order, got {result['keys']}."
    )
    assert result["body"] == {
        "code": codes["SER-A"],
        "balance_cents": 97000,
    }, f"Unexpected account body: {result['body']!r}."


def test_transfer_of_entire_balance_is_allowed(serial_phase, client, codes):
    result = serial_phase["ser_2"]
    assert result["status_code"] == 201, (
        "Transferring the entire remaining balance must be applied (201), got "
        f"{result['status_code']} with body {result['text']!r}."
    )
    body = result["body"]
    assert body["source_balance_after"] == 0, (
        f"Expected source_balance_after 0, got {body['source_balance_after']!r}."
    )
    assert body["target_balance_after"] == 125000, (
        "Expected target_balance_after 125000, got "
        f"{body['target_balance_after']!r}."
    )
    assert _balance(client, codes["SER-A"]) == 0, (
        f"{codes['SER-A']} must be drained to 0 in the database."
    )
    assert _balance(client, codes["SER-B"]) == 125000, (
        f"{codes['SER-B']} must hold 125000 in the database."
    )


def test_insufficient_funds_rejected(serial_phase, client, codes, tid):
    result = serial_phase["ser_3"]
    assert result["status_code"] == 409, (
        "A transfer larger than the source balance must answer 409, got "
        f"{result['status_code']} with body {result['text']!r}."
    )
    assert result["keys"] == REJECTED_KEYS, (
        f"A rejection body must have exactly {REJECTED_KEYS} in that order, "
        f"got {result['keys']}."
    )
    assert result["body"] == {
        "status": "rejected",
        "transfer_id": tid("ser-3"),
        "reason": "insufficient_funds",
    }, f"Unexpected rejection body: {result['body']!r}."
    assert _entry(client, tid("ser-3")) == [], (
        f"No LedgerEntry may exist for the rejected transfer {tid('ser-3')}."
    )
    assert _balance(client, codes["SER-A"]) == 0, (
        f"{codes['SER-A']} must stay at 0 after a rejected transfer."
    )
    assert _balance(client, codes["SER-B"]) == 125000, (
        f"{codes['SER-B']} must stay at 125000 after a rejected transfer."
    )


def test_duplicate_transfer_id_rejected(serial_phase, client, codes, tid):
    result = serial_phase["duplicate_ser_1"]
    assert result["status_code"] == 409, (
        "Re-using a transfer_id must answer 409, got "
        f"{result['status_code']} with body {result['text']!r}."
    )
    assert result["body"] == {
        "status": "rejected",
        "transfer_id": tid("ser-1"),
        "reason": "duplicate_transfer",
    }, f"Unexpected rejection body: {result['body']!r}."
    rows = _entry(client, tid("ser-1"))
    assert len(rows) == 1, (
        f"There must still be exactly one LedgerEntry for {tid('ser-1')}, "
        f"found {len(rows)}."
    )
    assert rows[0]["amount_cents"] == 3000, (
        "The original ledger entry must not be overwritten by a duplicate "
        f"submission, found amount_cents {rows[0]['amount_cents']}."
    )
    assert _balance(client, codes["DUP-A"]) == INITIAL_BALANCES["DUP-A"], (
        f"{codes['DUP-A']} must be untouched by a duplicate submission."
    )
    assert _balance(client, codes["DUP-B"]) == INITIAL_BALANCES["DUP-B"], (
        f"{codes['DUP-B']} must be untouched by a duplicate submission."
    )


def test_unknown_account_read(serial_phase, codes):
    result = serial_phase["unknown_account_read"]
    assert result["status_code"] == 404, (
        f"GET /accounts/{codes['MISSING']} must answer 404, got "
        f"{result['status_code']} with body {result['text']!r}."
    )
    assert result["keys"] == ["status", "reason"], (
        "The 404 account body must have exactly the keys ['status', 'reason'] "
        f"in that order, got {result['keys']}."
    )
    assert result["body"] == {
        "status": "rejected",
        "reason": "unknown_account",
    }, f"Unexpected body: {result['body']!r}."


def test_unknown_transfer_read(serial_phase, tid):
    result = serial_phase["unknown_transfer_read"]
    assert result["status_code"] == 404, (
        f"GET /transfers/{tid('nope')} must answer 404, got "
        f"{result['status_code']} with body {result['text']!r}."
    )
    assert result["keys"] == ["status", "reason"], (
        "The 404 transfer body must have exactly the keys ['status', 'reason'] "
        f"in that order, got {result['keys']}."
    )
    assert result["body"] == {
        "status": "rejected",
        "reason": "unknown_transfer",
    }, f"Unexpected body: {result['body']!r}."


# ---------------------------------------------------------------------------
# 4. rejection semantics and precedence
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "case_name",
    [
        "unknown_source",
        "unknown_target",
        "same_account",
        "amount_zero",
        "amount_negative",
        "amount_fractional",
        "amount_string",
        "amount_boolean",
        "missing_amount_key",
        "extra_key",
        "empty_source_code",
        "non_string_transfer_id",
        "not_json_body",
        "precedence_amount_over_unknown",
        "precedence_same_over_unknown",
        "precedence_amount_over_duplicate",
    ],
)
def test_rejection_case(rejection_phase, case_name):
    result, expected_status, expected_reason, expected_tid = rejection_phase[
        "cases"
    ][case_name]
    assert result["status_code"] == expected_status, (
        f"Case '{case_name}' must answer {expected_status}, got "
        f"{result['status_code']} with body {result['text']!r}."
    )
    assert result["keys"] == REJECTED_KEYS, (
        f"Case '{case_name}' must answer with exactly {REJECTED_KEYS} in that "
        f"order, got {result['keys']}."
    )
    assert result["body"]["status"] == "rejected", (
        f"Case '{case_name}' must report status 'rejected', got "
        f"{result['body']['status']!r}."
    )
    assert result["body"]["reason"] == expected_reason, (
        f"Case '{case_name}' must report reason {expected_reason!r}, got "
        f"{result['body']['reason']!r}."
    )
    assert result["body"]["transfer_id"] == expected_tid, (
        f"Case '{case_name}' must echo transfer_id {expected_tid!r}, got "
        f"{result['body']['transfer_id']!r}."
    )


def test_rejections_left_no_ledger_entries(rejection_phase, client):
    for transfer_id in rejection_phase["rejected_ids"]:
        assert _entry(client, transfer_id) == [], (
            f"The rejected transfer {transfer_id} must not have created a "
            "LedgerEntry."
        )


def test_rejections_changed_no_balance(rejection_phase):
    assert rejection_phase["balances_after"] == rejection_phase[
        "balances_before"
    ], (
        "Rejected transfers must not change any balance. Before: "
        f"{rejection_phase['balances_before']}, after: "
        f"{rejection_phase['balances_after']}."
    )


# ---------------------------------------------------------------------------
# 5. parallel conflicting workload
# ---------------------------------------------------------------------------
def test_parallel_workload_all_applied(hot_phase):
    assert hot_phase["external_errors"] == [], (
        "The independent writer that applies its own transfers directly against "
        "the database could not complete them while the service was serving "
        "requests (the service must stay correct and must not block or break "
        f"other writers): {hot_phase['external_errors']}."
    )
    bad = [
        (payload["transfer_id"], result["status_code"], result["text"])
        for payload, result in zip(hot_phase["payloads"], hot_phase["responses"])
        if result["status_code"] != 201
    ]
    assert not bad, (
        f"All {HOT_TRANSFERS} concurrent transfers must be applied with 201; "
        f"these were not: {bad[:10]}"
    )
    for payload, result in zip(hot_phase["payloads"], hot_phase["responses"]):
        assert result["keys"] == APPLIED_KEYS, (
            f"Applied response for {payload['transfer_id']} must have keys "
            f"{APPLIED_KEYS}, got {result['keys']}."
        )
        assert result["body"]["status"] == "applied", (
            f"Response for {payload['transfer_id']} must report 'applied', got "
            f"{result['body']['status']!r}."
        )


def test_parallel_workload_final_balances(hot_phase, client, codes):
    expected_a = (
        INITIAL_BALANCES["HOT-A"]
        - (HOT_TRANSFERS // 2) * HOT_FORWARD_AMOUNT
        + (HOT_TRANSFERS // 2) * HOT_BACKWARD_AMOUNT
        - EXT_TRANSFERS * EXT_AMOUNT
    )
    expected_b = (
        INITIAL_BALANCES["HOT-B"]
        + (HOT_TRANSFERS // 2) * HOT_FORWARD_AMOUNT
        - (HOT_TRANSFERS // 2) * HOT_BACKWARD_AMOUNT
        + EXT_TRANSFERS * EXT_AMOUNT
    )
    actual_a = _balance(client, codes["HOT-A"])
    actual_b = _balance(client, codes["HOT-B"])
    assert actual_a == expected_a, (
        f"{codes['HOT-A']} must hold {expected_a} after the concurrent "
        f"workload (no lost updates), found {actual_a}."
    )
    assert actual_b == expected_b, (
        f"{codes['HOT-B']} must hold {expected_b} after the concurrent "
        f"workload (no lost updates), found {actual_b}."
    )
    assert actual_a + actual_b == (
        INITIAL_BALANCES["HOT-A"] + INITIAL_BALANCES["HOT-B"]
    ), (
        "Money must be conserved between the two accounts: "
        f"{actual_a} + {actual_b} != "
        f"{INITIAL_BALANCES['HOT-A'] + INITIAL_BALANCES['HOT-B']}."
    )


def test_parallel_workload_exactly_one_entry_each(hot_phase, client, nonce):
    entries = _entries_with_prefix(client, f"TR{nonce}-hot-")
    assert len(entries) == HOT_TRANSFERS, (
        f"Expected exactly {HOT_TRANSFERS} ledger entries for the concurrent "
        f"workload, found {len(entries)}."
    )
    ids = [entry["transfer_id"] for entry in entries]
    assert len(set(ids)) == HOT_TRANSFERS, (
        f"Ledger entries must be unique per transfer_id, found duplicates in "
        f"{len(ids)} rows."
    )
    external = _entries_with_prefix(client, f"TR{nonce}-ext-")
    assert len(external) == EXT_TRANSFERS, (
        f"The {EXT_TRANSFERS} ledger entries written directly by the "
        f"concurrent writer must still be present, found {len(external)}."
    )


def test_parallel_responses_match_stored_entries(hot_phase, client, nonce):
    stored = {
        entry["transfer_id"]: entry
        for entry in _entries_with_prefix(client, f"TR{nonce}-hot-")
    }
    for payload, result in zip(hot_phase["payloads"], hot_phase["responses"]):
        transfer_id = payload["transfer_id"]
        assert transfer_id in stored, (
            f"The service reported {transfer_id} as applied but no LedgerEntry "
            "was persisted."
        )
        entry = stored[transfer_id]
        body = result["body"]
        assert entry["amount_cents"] == payload["amount_cents"], (
            f"Stored amount for {transfer_id} must be "
            f"{payload['amount_cents']}, found {entry['amount_cents']}."
        )
        assert entry["source_code"] == payload["source_code"], (
            f"Stored source for {transfer_id} must be "
            f"{payload['source_code']}, found {entry['source_code']}."
        )
        assert entry["target_code"] == payload["target_code"], (
            f"Stored target for {transfer_id} must be "
            f"{payload['target_code']}, found {entry['target_code']}."
        )
        assert entry["source_balance_after"] == body["source_balance_after"], (
            f"For {transfer_id} the response reported source_balance_after "
            f"{body['source_balance_after']} but the ledger stored "
            f"{entry['source_balance_after']}."
        )
        assert entry["target_balance_after"] == body["target_balance_after"], (
            f"For {transfer_id} the response reported target_balance_after "
            f"{body['target_balance_after']} but the ledger stored "
            f"{entry['target_balance_after']}."
        )


@pytest.mark.parametrize("account", ["HOT-A", "HOT-B"])
def test_parallel_workload_audit_chain(hot_phase, client, codes, account):
    code = codes[account]
    entries = _entries_for_account(client, code)
    left, right = _chain_report(
        entries, code, INITIAL_BALANCES[account], _balance(client, code)
    )
    assert left == right, (
        f"The recorded balances for {code} do not form an unbroken chain: the "
        "implied pre-transfer balances plus the current balance "
        f"({left}) must equal the recorded post-transfer balances plus the "
        f"starting balance ({right})."
    )


# ---------------------------------------------------------------------------
# 6. overdraft race
# ---------------------------------------------------------------------------
def test_overdraft_race_exact_outcome(cap_phase, client, codes):
    responses = cap_phase["responses"]
    applied = [r for r in responses if r["status_code"] == 201]
    rejected = [r for r in responses if r["status_code"] == 409]
    expected_applied = INITIAL_BALANCES["CAP-A"] // CAP_AMOUNT
    assert len(applied) + len(rejected) == CAP_TRANSFERS, (
        "Every concurrent overdraft attempt must answer either 201 or 409, "
        f"got status codes {[r['status_code'] for r in responses]}."
    )
    assert len(applied) == expected_applied, (
        f"Exactly {expected_applied} of the {CAP_TRANSFERS} concurrent "
        f"transfers of {CAP_AMOUNT} from a balance of "
        f"{INITIAL_BALANCES['CAP-A']} may be applied, found {len(applied)}."
    )
    for result in rejected:
        assert result["body"]["reason"] == "insufficient_funds", (
            "Overdrafting attempts must be rejected with 'insufficient_funds', "
            f"got {result['body']['reason']!r}."
        )
    assert _balance(client, codes["CAP-A"]) == 0, (
        f"{codes['CAP-A']} must end at 0 and never go negative, found "
        f"{_balance(client, codes['CAP-A'])}."
    )
    assert _balance(client, codes["CAP-B"]) == INITIAL_BALANCES["CAP-A"], (
        f"{codes['CAP-B']} must have received "
        f"{INITIAL_BALANCES['CAP-A']} cents in total."
    )


def test_overdraft_race_ledger_rows(cap_phase, client, nonce):
    entries = _entries_with_prefix(client, f"TR{nonce}-cap-")
    expected = INITIAL_BALANCES["CAP-A"] // CAP_AMOUNT
    assert len(entries) == expected, (
        f"Exactly {expected} ledger entries may exist for the overdraft race, "
        f"found {len(entries)}."
    )


@pytest.mark.parametrize("account", ["CAP-A", "CAP-B"])
def test_overdraft_race_audit_chain(cap_phase, client, codes, account):
    code = codes[account]
    entries = _entries_for_account(client, code)
    left, right = _chain_report(
        entries, code, INITIAL_BALANCES[account], _balance(client, code)
    )
    assert left == right, (
        f"The recorded balances for {code} do not form an unbroken chain: "
        f"{left} != {right}."
    )


# ---------------------------------------------------------------------------
# 7. duplicate storm
# ---------------------------------------------------------------------------
def test_duplicate_storm_single_winner(dup_phase, client, codes, tid):
    responses = dup_phase["responses"]
    applied = [r for r in responses if r["status_code"] == 201]
    duplicates = [
        r
        for r in responses
        if r["status_code"] == 409
        and isinstance(r["body"], dict)
        and r["body"].get("reason") == "duplicate_transfer"
    ]
    assert len(applied) == 1, (
        f"Exactly one of the {DUP_CLIENTS} simultaneous submissions of "
        f"{tid('dup')} may be applied, found {len(applied)} "
        f"(status codes: {[r['status_code'] for r in responses]})."
    )
    assert len(duplicates) == DUP_CLIENTS - 1, (
        f"The other {DUP_CLIENTS - 1} submissions must be rejected with 409 "
        f"'duplicate_transfer', found {len(duplicates)}."
    )
    entries = _entry(client, tid("dup"))
    assert len(entries) == 1, (
        f"Exactly one LedgerEntry may exist for {tid('dup')}, found "
        f"{len(entries)}."
    )
    assert _balance(client, codes["DUP-A"]) == (
        INITIAL_BALANCES["DUP-A"] - DUP_AMOUNT
    ), (
        f"{codes['DUP-A']} must be debited exactly once "
        f"({INITIAL_BALANCES['DUP-A'] - DUP_AMOUNT}), found "
        f"{_balance(client, codes['DUP-A'])}."
    )
    assert _balance(client, codes["DUP-B"]) == DUP_AMOUNT, (
        f"{codes['DUP-B']} must be credited exactly once ({DUP_AMOUNT}), found "
        f"{_balance(client, codes['DUP-B'])}."
    )


# ---------------------------------------------------------------------------
# 8. boundary amounts
# ---------------------------------------------------------------------------
def test_boundary_amounts(edge_phase, client, codes, nonce, tid):
    over = edge_phase["over"]
    assert over["status_code"] == 409, (
        "A transfer of 51 from a balance of 50 must answer 409, got "
        f"{over['status_code']} with body {over['text']!r}."
    )
    assert over["body"]["reason"] == "insufficient_funds", (
        f"Expected reason 'insufficient_funds', got {over['body']['reason']!r}."
    )
    exact = edge_phase["exact"]
    assert exact["status_code"] == 201, (
        "A transfer of exactly the full balance must answer 201, got "
        f"{exact['status_code']} with body {exact['text']!r}."
    )
    assert exact["body"]["source_balance_after"] == 0, (
        "Expected source_balance_after 0, got "
        f"{exact['body']['source_balance_after']!r}."
    )
    empty = edge_phase["after_empty"]
    assert empty["status_code"] == 409, (
        "A transfer of 1 from an empty account must answer 409, got "
        f"{empty['status_code']} with body {empty['text']!r}."
    )
    assert empty["body"]["reason"] == "insufficient_funds", (
        f"Expected reason 'insufficient_funds', got {empty['body']['reason']!r}."
    )
    assert _balance(client, codes["EDG-A"]) == 0, (
        f"{codes['EDG-A']} must end at 0, found "
        f"{_balance(client, codes['EDG-A'])}."
    )
    assert _balance(client, codes["EDG-B"]) == INITIAL_BALANCES["EDG-A"], (
        f"{codes['EDG-B']} must hold {INITIAL_BALANCES['EDG-A']}, found "
        f"{_balance(client, codes['EDG-B'])}."
    )
    entries = _entries_with_prefix(client, f"TR{nonce}-edg-")
    assert len(entries) == 1, (
        "Exactly one of the three boundary attempts may be recorded, found "
        f"{len(entries)} entries."
    )
    assert entries[0]["transfer_id"] == tid("edg-2"), (
        f"The recorded boundary entry must be {tid('edg-2')}, found "
        f"{entries[0]['transfer_id']}."
    )


# ---------------------------------------------------------------------------
# 9. restart / re-runnability / append-only
# ---------------------------------------------------------------------------
def test_service_restarts_and_serves_old_data(restart_phase, codes, tid):
    result = restart_phase["entry_read"]
    assert result["status_code"] == 200, (
        f"After a restart GET /transfers/{tid('ser-1')} must still answer 200, "
        f"got {result['status_code']} with body {result['text']!r}."
    )
    assert result["body"] == {
        "transfer_id": tid("ser-1"),
        "source_code": codes["SER-A"],
        "target_code": codes["SER-B"],
        "amount_cents": 3000,
        "source_balance_after": 97000,
        "target_balance_after": 28000,
    }, f"Unexpected ledger-entry body after restart: {result['body']!r}."


def test_duplicate_still_rejected_after_restart(restart_phase, tid):
    result = restart_phase["duplicate_again"]
    assert result["status_code"] == 409, (
        f"Re-submitting {tid('dup')} after a restart must answer 409, got "
        f"{result['status_code']} with body {result['text']!r}."
    )
    assert result["body"]["reason"] == "duplicate_transfer", (
        f"Expected reason 'duplicate_transfer', got "
        f"{result['body']['reason']!r}."
    )


def test_transfer_after_restart(post_restart_transfer, client, codes, tid):
    result = post_restart_transfer
    assert result["status_code"] == 201, (
        f"A fresh transfer {tid('post-1')} after the restart must answer 201, "
        f"got {result['status_code']} with body {result['text']!r}."
    )
    assert result["body"]["source_balance_after"] == 0, (
        "Expected source_balance_after 0, got "
        f"{result['body']['source_balance_after']!r}."
    )
    assert result["body"]["target_balance_after"] == (
        INITIAL_BALANCES["DUP-A"]
    ), (
        f"Expected target_balance_after {INITIAL_BALANCES['DUP-A']}, got "
        f"{result['body']['target_balance_after']!r}."
    )
    assert _balance(client, codes["DUP-B"]) == 0, (
        f"{codes['DUP-B']} must be back to 0, found "
        f"{_balance(client, codes['DUP-B'])}."
    )
    assert _balance(client, codes["DUP-A"]) == INITIAL_BALANCES["DUP-A"], (
        f"{codes['DUP-A']} must be back to {INITIAL_BALANCES['DUP-A']}, found "
        f"{_balance(client, codes['DUP-A'])}."
    )


def test_ledger_is_append_only(post_restart_transfer, restart_phase, client):
    snapshot = {
        entry["transfer_id"]: entry for entry in restart_phase["snapshot"]
    }
    assert snapshot, "No ledger entries were captured before the restart."
    current = {}
    for transfer_id in snapshot:
        rows = _entry(client, transfer_id)
        assert len(rows) == 1, (
            f"The ledger entry {transfer_id} must still exist exactly once "
            f"after the restart, found {len(rows)}."
        )
        current[transfer_id] = rows[0]
    for transfer_id, before in snapshot.items():
        after = current[transfer_id]
        for field in (
            "amount_cents",
            "source_code",
            "target_code",
            "source_balance_after",
            "target_balance_after",
        ):
            assert after[field] == before[field], (
                f"Ledger entries are append-only: {transfer_id}.{field} "
                f"changed from {before[field]!r} to {after[field]!r}."
            )


# ---------------------------------------------------------------------------
# 10. CLI sanity and global conservation
# ---------------------------------------------------------------------------
def test_ledger_count_via_gel_cli(post_restart_transfer, client, gel_server):
    count = _query_json(client, "select count(default::LedgerEntry)")[0]
    proc = subprocess.run(
        ["gel", "query", "select count(default::LedgerEntry)"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, (
        "'gel query' must succeed against the local instance: "
        f"rc={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert str(count) in proc.stdout, (
        f"'gel query' reported {proc.stdout!r} but the client counted {count} "
        "LedgerEntry objects."
    )


def test_total_money_conserved(post_restart_transfer, client, fixture_accounts):
    total = _query_json(client, "select sum(default::Account.balance_cents)")[0]
    assert total == fixture_accounts["total_after_setup"], (
        "The sum of all account balances must be unchanged by transfers: "
        f"expected {fixture_accounts['total_after_setup']}, found {total}."
    )
