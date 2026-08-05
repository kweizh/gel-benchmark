import concurrent.futures
import glob
import importlib
import json
import os
import re
import shutil
import socket
import subprocess
import time
import uuid

import pytest
import requests

PROJECT_DIR = "/home/user/harvest_api"
APP_DIR = os.path.join(PROJECT_DIR, "app")
QUERIES_DIR = os.path.join(APP_DIR, "queries")
REGENERATE_SCRIPT = os.path.join(PROJECT_DIR, "regenerate.sh")
SERVICE_LOG = "/tmp/harvest_api_service.log"
CHECKSUM_SCRIPT = "/opt/task/schema_checksum.py"
CHECKSUM_FILE = "/opt/task/schema.sha256"

HOST = "127.0.0.1"
PORT = 8099
BASE_URL = f"http://{HOST}:{PORT}"

START_COMMAND = ["python3", "-m", "app.server"]

QUERY_STEMS = [
    "list_region_growers",
    "get_batch_detail",
    "record_inspection",
    "region_totals",
]

# Seeded data model (see the seeding rules): grower i (1..12) belongs to
# NOR/SOU/EAS depending on (i - 1) % 3, and owns batches j = 1..5.
REGION_NAMES = {
    "NOR": "Northern Highlands",
    "SOU": "Southern Valley",
    "EAS": "Eastern Coast",
}
REGION_BY_INDEX = {0: "NOR", 1: "SOU", 2: "EAS"}
CERTIFICATIONS = {
    1: ["organic"],
    2: ["fairtrade", "organic"],
    3: ["fairtrade"],
    4: ["organic", "rainforest"],
    5: [],
}


def _region_code(i):
    return REGION_BY_INDEX[(i - 1) % 3]


def _growers_of(region_code):
    return [i for i in range(1, 13) if _region_code(i) == region_code]


def _slug(i):
    return f"grower-{i:02d}"


def _grower_name(i):
    return f"Grower {i:02d}"


def _batch_code(i, j):
    return f"BLK-{i * 100 + j}"


def _batch_kilograms(i, j):
    return float(100 * j + 10 * i)


def _batch_date(i, j):
    return f"2025-0{j}-{i:02d}"


def _expected_batch(i, j):
    return {
        "code": _batch_code(i, j),
        "kilograms": _batch_kilograms(i, j),
        "harvested_on": _batch_date(i, j),
        "certifications": sorted(CERTIFICATIONS[j]),
    }


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def _start_gel_server():
    gel_start = shutil.which("gel-start")
    assert gel_start is not None, "'gel-start' is not available in PATH."
    proc = subprocess.run(
        [gel_start], capture_output=True, text=True, timeout=900
    )
    assert proc.returncode == 0, (
        "'gel-start' failed to start the local Gel server.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


@pytest.fixture(scope="session")
def gel_client():
    """A connected blocking Gel client; guarantees the Gel server is ready."""
    _start_gel_server()
    gel = importlib.import_module("gel")
    client = gel.create_client()
    deadline = time.time() + 240
    last_error = None
    ready = False
    while time.time() < deadline:
        try:
            client.ensure_connected()
            assert client.query_single("select 1") == 1
            ready = True
            break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(2)
    if not ready:
        pytest.fail(
            f"The local Gel server never became ready: {last_error!r}"
        )
    try:
        yield client
    finally:
        client.close()


@pytest.fixture(scope="session", autouse=True)
def reset_mutable_state(gel_client):
    """Remove any Inspection/Defect objects so the run is repeatable."""
    gel_client.execute("delete Inspection")
    gel_client.execute("delete Defect")
    return True


def _port_open():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(2)
        return sock.connect_ex((HOST, PORT)) == 0


class ServiceHandle:
    """Owns the long-running HTTP service process under test."""

    def __init__(self):
        self.proc = None

    def start(self, timeout=120):
        log = open(SERVICE_LOG, "a", buffering=1)
        log.write(f"\n===== starting service at {time.ctime()} =====\n")
        self.proc = subprocess.Popen(
            START_COMMAND,
            cwd=PROJECT_DIR,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=os.environ.copy(),
        )
        deadline = time.time() + timeout
        last_error = None
        while time.time() < deadline:
            if self.proc.poll() is not None:
                pytest.fail(
                    "The service process exited with code "
                    f"{self.proc.returncode} while starting. Logs:\n"
                    f"{self.logs()}"
                )
            try:
                resp = requests.get(f"{BASE_URL}/healthz", timeout=5)
                if resp.status_code == 200:
                    return
                last_error = f"status {resp.status_code}"
            except requests.RequestException as exc:
                last_error = repr(exc)
            time.sleep(1)
        pytest.fail(
            f"GET {BASE_URL}/healthz did not answer 200 within {timeout}s "
            f"(last error: {last_error}). Logs:\n{self.logs()}"
        )

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=20)
        self.proc = None
        deadline = time.time() + 30
        while time.time() < deadline and _port_open():
            time.sleep(1)

    def restart(self):
        self.stop()
        self.start()

    def logs(self):
        if not os.path.isfile(SERVICE_LOG):
            return "<no service log>"
        with open(SERVICE_LOG, errors="replace") as handle:
            return handle.read()[-20000:]


@pytest.fixture(scope="session")
def service(gel_client, reset_mutable_state):
    handle = ServiceHandle()
    if os.path.isfile(SERVICE_LOG):
        os.remove(SERVICE_LOG)
    handle.start()
    try:
        yield handle
    finally:
        print("===== service logs =====")
        print(handle.logs())
        handle.stop()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _cast_pattern(type_expr, param):
    """Regex for an EdgeQL cast such as ``<array<str>>$name`` (whitespace-tolerant)."""
    inner = re.escape(type_expr).replace("\\ ", r"\s+")
    inner = inner.replace("<", r"\s*<\s*").replace(">", r"\s*>\s*")
    return re.compile(r"<\s*(?:required\s+)?" + inner + r"\s*>\s*\$" + param)


def _read_query(stem):
    path = os.path.join(QUERIES_DIR, f"{stem}.edgeql")
    assert os.path.isfile(path), f"Query file {path} does not exist."
    text = open(path).read()
    assert text.strip(), f"Query file {path} is empty."
    return path, text


def _generated_py_files():
    return sorted(
        p
        for p in glob.glob(os.path.join(QUERIES_DIR, "*.py"))
        if os.path.basename(p) != "__init__.py"
    )


def _generated_for(stem):
    return sorted(
        p
        for p in _generated_py_files()
        if os.path.basename(p).startswith(stem)
    )


def _handwritten_py_files():
    """Every hand-written Python file of the project (i.e. not generated code)."""
    found = []
    skip_markers = (
        os.path.abspath(QUERIES_DIR),
        os.path.join(PROJECT_DIR, "dbschema"),
    )
    for root, _dirs, files in os.walk(PROJECT_DIR):
        abs_root = os.path.abspath(root)
        if any(abs_root == marker or abs_root.startswith(marker + os.sep)
               for marker in skip_markers):
            continue
        if "__pycache__" in abs_root or "site-packages" in abs_root:
            continue
        if any(part.startswith(".") for part in abs_root.split(os.sep) if part):
            continue
        for name in files:
            if name.endswith(".py"):
                found.append(os.path.join(root, name))
    return sorted(found)


def _keys(obj):
    return list(obj.keys())


def _post(path, payload, expect=None):
    resp = requests.post(f"{BASE_URL}{path}", json=payload, timeout=30)
    if expect is not None:
        assert resp.status_code == expect, (
            f"POST {path} with {payload!r} returned {resp.status_code} "
            f"(expected {expect}); body: {resp.text[:2000]}"
        )
    return resp


def _get(path, expect=None):
    resp = requests.get(f"{BASE_URL}{path}", timeout=30)
    if expect is not None:
        assert resp.status_code == expect, (
            f"GET {path} returned {resp.status_code} (expected {expect}); "
            f"body: {resp.text[:2000]}"
        )
    return resp


def _grower_entry(payload, slug):
    for entry in payload["growers"]:
        if entry["slug"] == slug:
            return entry
    raise AssertionError(
        f"Grower {slug} missing from response: "
        f"{[g.get('slug') for g in payload['growers']]}"
    )


def _counts(gel_client):
    return json.loads(
        gel_client.query_single_json(
            """
            select {
              regions := count(Region),
              growers := count(Grower),
              batches := count(Batch),
              inspections := count(Inspection),
              defects := count(Defect),
            }
            """
        )
    )


# --------------------------------------------------------------------------- #
# 1-2. Query files exist and are parameterised as required
# --------------------------------------------------------------------------- #


def test_list_region_growers_query_file():
    path, text = _read_query("list_region_growers")
    for type_expr, param in [
        ("str", "region_code"),
        ("optional float64", "min_kilograms"),
        ("array<str>", "certifications"),
    ]:
        assert _cast_pattern(type_expr, param).search(text), (
            f"{path} does not declare the parameter <{type_expr}>${param}."
        )


def test_get_batch_detail_query_file():
    path, text = _read_query("get_batch_detail")
    assert _cast_pattern("str", "code").search(text), (
        f"{path} does not declare the parameter <str>$code."
    )


def test_record_inspection_query_file():
    path, text = _read_query("record_inspection")
    for type_expr, param in [
        ("str", "batch_code"),
        ("str", "inspector"),
        ("bool", "passed"),
        ("array<str>", "defect_codes"),
        ("int64", "severity"),
    ]:
        assert _cast_pattern(type_expr, param).search(text), (
            f"{path} does not declare the parameter <{type_expr}>${param}."
        )


def test_region_totals_query_file():
    path, text = _read_query("region_totals")
    assert _cast_pattern("array<str>", "region_codes").search(text), (
        f"{path} does not declare the parameter <array<str>>$region_codes."
    )


def test_record_inspection_is_a_single_nested_mutation():
    path, text = _read_query("record_inspection")
    inspection_inserts = re.findall(r"insert\s+Inspection\b", text, re.IGNORECASE)
    assert len(inspection_inserts) == 1, (
        f"{path} must insert exactly one Inspection, found "
        f"{len(inspection_inserts)} 'insert Inspection' occurrences."
    )
    assert re.search(r"insert\s+Defect\b", text, re.IGNORECASE), (
        f"{path} must create the Defect objects in the same query."
    )


def test_region_totals_aggregates_in_the_database():
    path, text = _read_query("region_totals")
    assert re.search(r"count\s*\(", text, re.IGNORECASE), (
        f"{path} must compute its counts in EdgeQL."
    )
    assert re.search(r"sum\s*\(", text, re.IGNORECASE), (
        f"{path} must compute the kilogram total in EdgeQL."
    )


# --------------------------------------------------------------------------- #
# 3. Generated modules are committed next to the query files
# --------------------------------------------------------------------------- #


def test_generated_modules_committed():
    generated = _generated_py_files()
    assert generated, (
        f"No generated Python module found in {QUERIES_DIR}; the code produced "
        "by Gel's Python code generator must be committed there."
    )
    for stem in QUERY_STEMS:
        assert _generated_for(stem), (
            f"No generated module for {stem}.edgeql found in {QUERIES_DIR} "
            f"(files present: {[os.path.basename(p) for p in generated]})."
        )
    unexpected = [
        os.path.basename(p)
        for p in generated
        if not any(os.path.basename(p).startswith(stem) for stem in QUERY_STEMS)
    ]
    assert not unexpected, (
        f"{QUERIES_DIR} contains Python files that do not belong to any query "
        f"file: {unexpected}"
    )


def test_generated_result_cardinalities():
    detail_modules = _generated_for("get_batch_detail")
    assert any(
        re.search(r"->\s*[\w\.\[\]]+\s*\|\s*None\s*:", open(p).read())
        or re.search(r"->\s*typing\.Optional\[", open(p).read())
        for p in detail_modules
    ), (
        "The generated module for get_batch_detail.edgeql does not return an "
        "optional (at most one) result; the query must yield at most one object."
    )
    list_modules = _generated_for("list_region_growers")
    assert any(
        re.search(r"->\s*(?:list|typing\.List)\[", open(p).read())
        for p in list_modules
    ), (
        "The generated module for list_region_growers.edgeql does not return a "
        "list of objects."
    )


# --------------------------------------------------------------------------- #
# 5. No hand-written queries anywhere outside the generated modules
# --------------------------------------------------------------------------- #


def test_no_handwritten_edgeql_outside_generated_modules():
    files = _handwritten_py_files()
    assert files, (
        f"No hand-written Python module found under {APP_DIR}; the service must "
        "be implemented there."
    )
    forbidden = [
        (
            re.compile(r"<\s*(?:optional\s+|required\s+)?[A-Za-z_][\w:]*"
                       r"(?:\s*<[^<>]*>\s*)?\s*>\s*\$"),
            "an EdgeQL parameter cast",
        ),
        (re.compile(r"array_unpack\s*\("), "the EdgeQL token 'array_unpack('"),
        (re.compile(r"filter\s+\."), "the EdgeQL token 'filter .'"),
    ]
    for path in files:
        text = open(path).read()
        for pattern, label in forbidden:
            assert not pattern.search(text), (
                f"{path} contains {label}; all database access must go through "
                "the generated functions and no hand-written file may contain "
                "EdgeQL."
            )


def test_generated_functions_are_the_ones_used():
    sources = "\n".join(open(path).read() for path in _handwritten_py_files())
    for stem in QUERY_STEMS:
        assert stem in sources, (
            f"The hand-written application code never references '{stem}', so "
            "the generated function for that query is not being used."
        )


# --------------------------------------------------------------------------- #
# 6. Schema, migrations and seed definition untouched
# --------------------------------------------------------------------------- #


def test_schema_and_migrations_unchanged():
    assert os.path.isfile(CHECKSUM_SCRIPT), (
        f"{CHECKSUM_SCRIPT} is missing from the image."
    )
    recorded = open(CHECKSUM_FILE).read().strip()
    proc = subprocess.run(
        ["python3", CHECKSUM_SCRIPT],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"Failed to recompute the schema checksum: {proc.stderr}"
    )
    assert proc.stdout.strip() == recorded, (
        "dbschema/ was modified: the checksum of default.gel plus the "
        f"migrations changed (expected {recorded}, got {proc.stdout.strip()})."
    )


def test_migrations_still_in_sync(gel_client):
    gel_cli = shutil.which("gel")
    assert gel_cli is not None, "The 'gel' CLI is not available in PATH."
    proc = subprocess.run(
        [gel_cli, "migration", "status"],
        capture_output=True,
        text=True,
        cwd=PROJECT_DIR,
        timeout=180,
    )
    combined = f"{proc.stdout}\n{proc.stderr}"
    assert proc.returncode == 0, (
        f"'gel migration status' failed: {combined}"
    )
    assert "up to date" in combined.lower(), (
        f"The database is no longer in sync with dbschema/migrations: {combined}"
    )


# --------------------------------------------------------------------------- #
# 7. Health endpoint
# --------------------------------------------------------------------------- #


def test_healthz(service):
    resp = _get("/healthz", expect=200)
    assert resp.headers.get("Content-Type", "").startswith("application/json"), (
        f"GET /healthz must answer with JSON, got "
        f"{resp.headers.get('Content-Type')!r}"
    )
    body = resp.json()
    assert body == {"status": "ok"}, f"Unexpected /healthz body: {body}"
    assert _keys(body) == ["status"], f"Unexpected /healthz keys: {_keys(body)}"


# --------------------------------------------------------------------------- #
# 8-13. POST /growers/search
# --------------------------------------------------------------------------- #


def test_search_without_filters(service):
    resp = _post("/growers/search", {"region_code": "NOR"}, expect=200)
    assert resp.headers.get("Content-Type", "").startswith("application/json"), (
        "POST /growers/search must answer with JSON, got "
        f"{resp.headers.get('Content-Type')!r}"
    )
    body = resp.json()
    assert _keys(body) == ["region_code", "growers"], (
        f"Unexpected top-level key order: {_keys(body)}"
    )
    assert body["region_code"] == "NOR", f"Unexpected region_code: {body}"
    slugs = [entry["slug"] for entry in body["growers"]]
    assert slugs == ["grower-01", "grower-04", "grower-07", "grower-10"], (
        f"Unexpected growers or ordering for region NOR: {slugs}"
    )

    entry = _grower_entry(body, "grower-01")
    assert _keys(entry) == [
        "slug",
        "name",
        "region",
        "batches",
        "matched_batches",
        "matched_kilograms",
    ], f"Unexpected grower key order: {_keys(entry)}"
    assert entry["name"] == "Grower 01", f"Unexpected grower name: {entry}"
    assert _keys(entry["region"]) == ["code", "name"], (
        f"Unexpected region key order: {_keys(entry['region'])}"
    )
    assert entry["region"] == {"code": "NOR", "name": "Northern Highlands"}, (
        f"Unexpected region payload: {entry['region']}"
    )
    assert [b["code"] for b in entry["batches"]] == [
        _batch_code(1, j) for j in range(1, 6)
    ], f"Unexpected batch ordering for grower-01: {entry['batches']}"
    assert _keys(entry["batches"][0]) == [
        "code",
        "kilograms",
        "harvested_on",
        "certifications",
    ], f"Unexpected batch key order: {_keys(entry['batches'][0])}"
    for j, batch in enumerate(entry["batches"], start=1):
        expected = _expected_batch(1, j)
        assert batch["kilograms"] == pytest.approx(expected["kilograms"]), (
            f"Unexpected kilograms for {expected['code']}: {batch}"
        )
        assert batch["harvested_on"] == expected["harvested_on"], (
            f"Unexpected harvested_on for {expected['code']}: {batch}"
        )
        assert batch["certifications"] == expected["certifications"], (
            f"Unexpected certifications for {expected['code']}: {batch}"
        )
    assert entry["matched_batches"] == 5, f"Unexpected matched_batches: {entry}"

    expected_totals = {
        "grower-01": 1550.0,
        "grower-04": 1700.0,
        "grower-07": 1850.0,
        "grower-10": 2000.0,
    }
    for slug, total in expected_totals.items():
        got = _grower_entry(body, slug)
        assert got["matched_batches"] == 5, (
            f"{slug} should report 5 matched batches: {got}"
        )
        assert got["matched_kilograms"] == pytest.approx(total), (
            f"{slug} should report {total} matched kilograms: {got}"
        )


def test_search_with_min_kilograms(service):
    body = _post(
        "/growers/search", {"region_code": "NOR", "min_kilograms": 400}, expect=200
    ).json()
    first = _grower_entry(body, "grower-01")
    assert [b["code"] for b in first["batches"]] == ["BLK-104", "BLK-105"], (
        f"Unexpected batches for grower-01 with min_kilograms=400: {first}"
    )
    assert first["matched_batches"] == 2, f"Unexpected matched_batches: {first}"
    assert first["matched_kilograms"] == pytest.approx(920.0), (
        f"Unexpected matched_kilograms for grower-01: {first}"
    )
    tenth = _grower_entry(body, "grower-10")
    assert [b["code"] for b in tenth["batches"]] == [
        "BLK-1003",
        "BLK-1004",
        "BLK-1005",
    ], f"Unexpected batches for grower-10 with min_kilograms=400: {tenth}"
    assert tenth["matched_kilograms"] == pytest.approx(1500.0), (
        f"Unexpected matched_kilograms for grower-10: {tenth}"
    )


def test_search_with_null_min_kilograms_matches_unfiltered(service):
    baseline = _post("/growers/search", {"region_code": "NOR"}, expect=200).json()
    with_null = _post(
        "/growers/search",
        {"region_code": "NOR", "min_kilograms": None},
        expect=200,
    ).json()
    assert with_null == baseline, (
        "A null min_kilograms must behave exactly like an absent one.\n"
        f"null: {json.dumps(with_null)[:1500]}\n"
        f"absent: {json.dumps(baseline)[:1500]}"
    )


def test_search_with_certifications(service):
    body = _post(
        "/growers/search",
        {"region_code": "NOR", "certifications": ["fairtrade"]},
        expect=200,
    ).json()
    first = _grower_entry(body, "grower-01")
    assert [b["code"] for b in first["batches"]] == ["BLK-102", "BLK-103"], (
        f"Unexpected batches for certifications=['fairtrade']: {first}"
    )
    assert first["matched_kilograms"] == pytest.approx(520.0), (
        f"Unexpected matched_kilograms: {first}"
    )

    body = _post(
        "/growers/search",
        {"region_code": "NOR", "certifications": ["organic", "rainforest"]},
        expect=200,
    ).json()
    first = _grower_entry(body, "grower-01")
    assert [b["code"] for b in first["batches"]] == [
        "BLK-101",
        "BLK-102",
        "BLK-104",
    ], f"Unexpected batches for certifications=['organic','rainforest']: {first}"


def test_search_with_empty_certifications_list(service):
    body = _post(
        "/growers/search",
        {"region_code": "SOU", "certifications": []},
        expect=200,
    ).json()
    slugs = [entry["slug"] for entry in body["growers"]]
    assert slugs == ["grower-02", "grower-05", "grower-08", "grower-11"], (
        f"Unexpected growers for region SOU: {slugs}"
    )
    for entry in body["growers"]:
        assert entry["matched_batches"] == 5, (
            f"An empty certifications list must not filter anything: {entry}"
        )


def test_search_combined_filters_keep_growers_without_matches(service):
    body = _post(
        "/growers/search",
        {
            "region_code": "NOR",
            "min_kilograms": 500,
            "certifications": ["rainforest"],
        },
        expect=200,
    ).json()
    slugs = [entry["slug"] for entry in body["growers"]]
    assert slugs == ["grower-01", "grower-04", "grower-07", "grower-10"], (
        f"Growers without matching batches must still be listed: {slugs}"
    )
    for slug in ["grower-01", "grower-04", "grower-07"]:
        entry = _grower_entry(body, slug)
        assert entry["batches"] == [], (
            f"{slug} should have no matching batch here: {entry}"
        )
        assert entry["matched_batches"] == 0, f"{slug}: {entry}"
        assert entry["matched_kilograms"] == pytest.approx(0.0), f"{slug}: {entry}"
    tenth = _grower_entry(body, "grower-10")
    assert [b["code"] for b in tenth["batches"]] == ["BLK-1004"], (
        f"Unexpected batches for grower-10: {tenth}"
    )
    assert tenth["matched_kilograms"] == pytest.approx(500.0), (
        f"Unexpected matched_kilograms for grower-10: {tenth}"
    )


def test_search_unknown_region(service):
    body = _post("/growers/search", {"region_code": "ZZZ"}, expect=200).json()
    assert body["growers"] == [], (
        f"An unknown region must yield an empty growers list: {body}"
    )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"region_code": ""},
        {"region_code": 5},
        {"region_code": "NOR", "min_kilograms": "400"},
        {"region_code": "NOR", "certifications": "organic"},
        {"region_code": "NOR", "certifications": ["organic", ""]},
        {
            "region_code": "NOR",
            "certifications": [
                "c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8", "c9",
            ],
        },
        ["NOR"],
    ],
)
def test_search_validation(service, payload):
    resp = _post("/growers/search", payload, expect=400)
    assert resp.json() == {"error": "invalid_request"}, (
        f"Unexpected error body for {payload!r}: {resp.text[:500]}"
    )


def test_search_rejects_malformed_json(service):
    resp = requests.post(
        f"{BASE_URL}/growers/search",
        data="{not json",
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    assert resp.status_code == 400, (
        f"Malformed JSON must be rejected with 400, got {resp.status_code}: "
        f"{resp.text[:500]}"
    )
    assert resp.json() == {"error": "invalid_request"}, (
        f"Unexpected error body: {resp.text[:500]}"
    )


# --------------------------------------------------------------------------- #
# 14. GET /batches/<code>
# --------------------------------------------------------------------------- #


def test_batch_detail(service):
    body = _get("/batches/BLK-102", expect=200).json()
    assert _keys(body) == [
        "code",
        "kilograms",
        "harvested_on",
        "certifications",
        "grower",
        "inspection_count",
    ], f"Unexpected key order: {_keys(body)}"
    assert body["code"] == "BLK-102", f"Unexpected code: {body}"
    assert body["kilograms"] == pytest.approx(210.0), f"Unexpected kilograms: {body}"
    assert body["harvested_on"] == "2025-02-01", f"Unexpected date: {body}"
    assert body["certifications"] == ["fairtrade", "organic"], (
        f"Unexpected certifications: {body}"
    )
    assert _keys(body["grower"]) == ["slug", "name", "region"], (
        f"Unexpected grower key order: {_keys(body['grower'])}"
    )
    assert _keys(body["grower"]["region"]) == ["code", "name"], (
        f"Unexpected region key order: {_keys(body['grower']['region'])}"
    )
    assert body["grower"] == {
        "slug": "grower-01",
        "name": "Grower 01",
        "region": {"code": "NOR", "name": "Northern Highlands"},
    }, f"Unexpected grower payload: {body['grower']}"
    assert body["inspection_count"] == 0, f"Unexpected inspection_count: {body}"


def test_batch_detail_without_certifications(service):
    body = _get("/batches/BLK-1205", expect=200).json()
    assert body["kilograms"] == pytest.approx(620.0), f"Unexpected kilograms: {body}"
    assert body["harvested_on"] == "2025-05-12", f"Unexpected date: {body}"
    assert body["certifications"] == [], f"Unexpected certifications: {body}"
    assert body["grower"]["slug"] == "grower-12", f"Unexpected grower: {body}"
    assert body["grower"]["region"] == {
        "code": "EAS",
        "name": "Eastern Coast",
    }, f"Unexpected region: {body}"


def test_batch_detail_unknown_code(service):
    resp = _get("/batches/BLK-9999", expect=404)
    assert resp.json() == {"error": "not_found"}, (
        f"Unexpected error body: {resp.text[:500]}"
    )
    resp = _get("/batches/", expect=404)
    assert resp.json() == {"error": "not_found"}, (
        f"Unexpected error body for an unknown path: {resp.text[:500]}"
    )


# --------------------------------------------------------------------------- #
# 15-16. POST /regions/totals
# --------------------------------------------------------------------------- #


EXPECTED_TOTALS = {
    "EAS": 7500.0,
    "NOR": 7100.0,
    "SOU": 7300.0,
}


def _assert_region_row(row, code):
    assert _keys(row) == [
        "code",
        "name",
        "grower_count",
        "batch_count",
        "total_kilograms",
    ], f"Unexpected region key order: {_keys(row)}"
    assert row["code"] == code, f"Unexpected code: {row}"
    assert row["name"] == REGION_NAMES[code], f"Unexpected name: {row}"
    assert row["grower_count"] == 4, f"Unexpected grower_count for {code}: {row}"
    assert row["batch_count"] == 20, f"Unexpected batch_count for {code}: {row}"
    assert row["total_kilograms"] == pytest.approx(EXPECTED_TOTALS[code]), (
        f"Unexpected total_kilograms for {code}: {row}"
    )


def test_region_totals_all_regions(service):
    body = _post("/regions/totals", {}, expect=200).json()
    assert _keys(body) == ["regions"], f"Unexpected key order: {_keys(body)}"
    rows = body["regions"]
    assert [row["code"] for row in rows] == ["EAS", "NOR", "SOU"], (
        f"Regions must be ordered by code: {[r.get('code') for r in rows]}"
    )
    for row in rows:
        _assert_region_row(row, row["code"])

    same = _post("/regions/totals", {"region_codes": []}, expect=200).json()
    assert same == body, (
        "An empty region_codes array must behave like an absent one.\n"
        f"empty: {json.dumps(same)[:1000]}\nabsent: {json.dumps(body)[:1000]}"
    )


def test_region_totals_subset(service):
    body = _post(
        "/regions/totals", {"region_codes": ["SOU", "EAS"]}, expect=200
    ).json()
    rows = body["regions"]
    assert [row["code"] for row in rows] == ["EAS", "SOU"], (
        f"Subset must be ordered by code: {[r.get('code') for r in rows]}"
    )
    for row in rows:
        _assert_region_row(row, row["code"])


def test_region_totals_unknown_codes(service):
    body = _post("/regions/totals", {"region_codes": ["ZZZ"]}, expect=200).json()
    assert body == {"regions": []}, f"Unexpected body: {body}"


@pytest.mark.parametrize(
    "payload",
    [
        {"region_codes": "SOU"},
        {"region_codes": ["SOU", ""]},
    ],
)
def test_region_totals_validation(service, payload):
    resp = _post("/regions/totals", payload, expect=400)
    assert resp.json() == {"error": "invalid_request"}, (
        f"Unexpected error body for {payload!r}: {resp.text[:500]}"
    )


# --------------------------------------------------------------------------- #
# 17-20. POST /inspections (nested mutation)
# --------------------------------------------------------------------------- #


def test_record_inspection_with_defects(service, gel_client):
    body = _post(
        "/inspections",
        {
            "batch_code": "BLK-303",
            "inspector": "Ines Marek",
            "passed": False,
            "defect_codes": ["mould", "mould", "bruise"],
            "severity": 4,
        },
        expect=201,
    ).json()
    assert _keys(body) == [
        "inspection_id",
        "batch_code",
        "inspector",
        "passed",
        "defect_count",
    ], f"Unexpected key order: {_keys(body)}"
    assert body["batch_code"] == "BLK-303", f"Unexpected batch_code: {body}"
    assert body["inspector"] == "Ines Marek", f"Unexpected inspector: {body}"
    assert body["passed"] is False, f"Unexpected passed value: {body}"
    assert body["defect_count"] == 3, f"Unexpected defect_count: {body}"
    assert re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        str(body["inspection_id"]),
    ), f"inspection_id must be a UUID string: {body}"

    stored = json.loads(
        gel_client.query_json(
            """
            select Inspection {
              inspector,
              passed,
              batch: { code },
              defects: { code, severity },
            }
            filter .id = <uuid>$id
            """,
            id=uuid.UUID(str(body["inspection_id"])),
        )
    )
    assert len(stored) == 1, (
        f"Exactly one Inspection with id {body['inspection_id']} must exist in "
        f"the database, found {len(stored)}."
    )
    row = stored[0]
    assert row["inspector"] == "Ines Marek", f"Stored inspector wrong: {row}"
    assert row["passed"] is False, f"Stored passed wrong: {row}"
    assert row["batch"] == {"code": "BLK-303"}, f"Stored batch wrong: {row}"
    assert sorted(d["code"] for d in row["defects"]) == [
        "bruise",
        "mould",
        "mould",
    ], f"The inspection must link three new Defect objects: {row}"
    assert all(d["severity"] == 4 for d in row["defects"]), (
        f"Every created Defect must have severity 4: {row}"
    )

    counts = _counts(gel_client)
    assert counts["inspections"] == 1, f"Unexpected Inspection count: {counts}"
    assert counts["defects"] == 3, f"Unexpected Defect count: {counts}"

    detail = _get("/batches/BLK-303", expect=200).json()
    assert detail["inspection_count"] == 1, (
        f"BLK-303 should now report one inspection: {detail}"
    )


def test_record_inspection_without_defects(service, gel_client):
    body = _post(
        "/inspections",
        {
            "batch_code": "BLK-303",
            "inspector": "Otto Vale",
            "passed": True,
            "defect_codes": [],
            "severity": 1,
        },
        expect=201,
    ).json()
    assert body["defect_count"] == 0, f"Unexpected defect_count: {body}"
    assert body["passed"] is True, f"Unexpected passed value: {body}"

    counts = _counts(gel_client)
    assert counts["inspections"] == 2, f"Unexpected Inspection count: {counts}"
    assert counts["defects"] == 3, (
        f"No new Defect object may be created for an empty list: {counts}"
    )

    stored = json.loads(
        gel_client.query_json(
            "select Inspection { defects: { code } } filter .id = <uuid>$id",
            id=uuid.UUID(str(body["inspection_id"])),
        )
    )
    assert stored and stored[0]["defects"] == [], (
        f"The second inspection must link no defects: {stored}"
    )

    detail = _get("/batches/BLK-303", expect=200).json()
    assert detail["inspection_count"] == 2, (
        f"BLK-303 should now report two inspections: {detail}"
    )


def test_record_inspection_unknown_batch_writes_nothing(service, gel_client):
    resp = _post(
        "/inspections",
        {
            "batch_code": "BLK-9999",
            "inspector": "Nobody",
            "passed": True,
            "defect_codes": ["x"],
            "severity": 2,
        },
        expect=404,
    )
    assert resp.json() == {"error": "batch_not_found"}, (
        f"Unexpected error body: {resp.text[:500]}"
    )
    counts = _counts(gel_client)
    assert counts["inspections"] == 2, (
        f"No Inspection may be created for an unknown batch: {counts}"
    )
    assert counts["defects"] == 3, (
        f"No Defect may be created for an unknown batch: {counts}"
    )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "batch_code": "BLK-303",
            "passed": True,
            "defect_codes": [],
            "severity": 1,
        },
        {
            "batch_code": "BLK-303",
            "inspector": "",
            "passed": True,
            "defect_codes": [],
            "severity": 1,
        },
        {
            "batch_code": "BLK-303",
            "inspector": "Ines Marek",
            "passed": "true",
            "defect_codes": [],
            "severity": 1,
        },
        {
            "batch_code": "BLK-303",
            "inspector": "Ines Marek",
            "passed": True,
            "defect_codes": [],
            "severity": 0,
        },
        {
            "batch_code": "BLK-303",
            "inspector": "Ines Marek",
            "passed": True,
            "defect_codes": [],
            "severity": 6,
        },
        {
            "batch_code": "BLK-303",
            "inspector": "Ines Marek",
            "passed": True,
            "defect_codes": [],
            "severity": 2.5,
        },
        {
            "batch_code": "BLK-303",
            "inspector": "Ines Marek",
            "passed": True,
            "defect_codes": "mould",
            "severity": 2,
        },
        {
            "batch_code": "BLK-303",
            "inspector": "Ines Marek",
            "passed": True,
            "defect_codes": ["mould", 3],
            "severity": 2,
        },
        {
            "batch_code": "BLK-303",
            "inspector": "Ines Marek",
            "passed": True,
            "defect_codes": [
                "d1", "d2", "d3", "d4", "d5", "d6", "d7", "d8", "d9",
            ],
            "severity": 2,
        },
        ["BLK-303"],
    ],
)
def test_record_inspection_validation_writes_nothing(service, gel_client, payload):
    resp = _post("/inspections", payload, expect=400)
    assert resp.json() == {"error": "invalid_request"}, (
        f"Unexpected error body for {payload!r}: {resp.text[:500]}"
    )
    counts = _counts(gel_client)
    assert counts["inspections"] == 2, (
        f"A rejected request must not write anything: {counts} ({payload!r})"
    )
    assert counts["defects"] == 3, (
        f"A rejected request must not write anything: {counts} ({payload!r})"
    )


# --------------------------------------------------------------------------- #
# 21-23. Routing, concurrency, regression
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/nope"),
        ("GET", "/inspections"),
        ("POST", "/healthz"),
    ],
)
def test_unknown_routes(service, method, path):
    if method == "GET":
        resp = _get(path, expect=404)
    else:
        resp = _post(path, {}, expect=404)
    assert resp.json() == {"error": "not_found"}, (
        f"Unexpected body for {method} {path}: {resp.text[:500]}"
    )


def test_concurrent_requests(service):
    started = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(requests.get, f"{BASE_URL}/batches/BLK-101", timeout=60)
            for _ in range(8)
        ]
        responses = [f.result() for f in futures]
    elapsed = time.time() - started
    assert elapsed < 60, (
        f"8 concurrent requests took {elapsed:.1f}s, which is too slow."
    )
    payloads = []
    for resp in responses:
        assert resp.status_code == 200, (
            f"Concurrent request failed with {resp.status_code}: {resp.text[:500]}"
        )
        payloads.append(resp.json())
    expected = {
        "kilograms": pytest.approx(110.0),
        "harvested_on": "2025-01-01",
        "certifications": ["organic"],
    }
    for payload in payloads:
        assert payload["kilograms"] == expected["kilograms"], (
            f"Unexpected kilograms under concurrency: {payload}"
        )
        assert payload["harvested_on"] == expected["harvested_on"], (
            f"Unexpected harvested_on under concurrency: {payload}"
        )
        assert payload["certifications"] == expected["certifications"], (
            f"Unexpected certifications under concurrency: {payload}"
        )
    assert all(payload == payloads[0] for payload in payloads), (
        "Concurrent responses for the same batch differ from each other."
    )


def test_seeded_data_unchanged(service, gel_client):
    counts = _counts(gel_client)
    assert counts["regions"] == 3, f"Region objects were modified: {counts}"
    assert counts["growers"] == 12, f"Grower objects were modified: {counts}"
    assert counts["batches"] == 60, f"Batch objects were modified: {counts}"

    batch = json.loads(
        gel_client.query_single_json(
            """
            select Batch { kilograms, harvested_on, certifications }
            filter .code = 'BLK-505'
            """
        )
    )
    assert batch["kilograms"] == pytest.approx(550.0), (
        f"BLK-505 was modified: {batch}"
    )
    assert batch["harvested_on"] == "2025-05-05", f"BLK-505 was modified: {batch}"
    assert batch["certifications"] == [], f"BLK-505 was modified: {batch}"

    other = json.loads(
        gel_client.query_single_json(
            "select Batch { kilograms, certifications } filter .code = 'BLK-1201'"
        )
    )
    assert other["kilograms"] == pytest.approx(220.0), (
        f"BLK-1201 was modified: {other}"
    )
    assert sorted(other["certifications"]) == ["organic"], (
        f"BLK-1201 was modified: {other}"
    )


# --------------------------------------------------------------------------- #
# 24. Regeneration is reproducible byte-for-byte
# --------------------------------------------------------------------------- #


def test_regeneration_is_byte_identical(service, gel_client):
    assert os.path.isfile(REGENERATE_SCRIPT), (
        f"{REGENERATE_SCRIPT} does not exist."
    )
    snapshot = {}
    for path in _generated_py_files():
        with open(path, "rb") as handle:
            snapshot[path] = handle.read()
    assert snapshot, (
        f"No generated module found in {QUERIES_DIR} to regenerate."
    )

    for path in snapshot:
        os.remove(path)
    for cache in glob.glob(os.path.join(QUERIES_DIR, "__pycache__", "*")):
        os.remove(cache)

    try:
        proc = subprocess.run(
            ["bash", "regenerate.sh"],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=300,
            env=os.environ.copy(),
        )
        assert proc.returncode == 0, (
            "'bash regenerate.sh' failed after the generated modules were "
            f"deleted.\nstdout: {proc.stdout[-4000:]}\n"
            f"stderr: {proc.stderr[-4000:]}"
        )
        regenerated = {}
        for path in _generated_py_files():
            with open(path, "rb") as handle:
                regenerated[path] = handle.read()
        assert sorted(regenerated) == sorted(snapshot), (
            "regenerate.sh produced a different set of files.\n"
            f"before: {sorted(os.path.basename(p) for p in snapshot)}\n"
            f"after:  {sorted(os.path.basename(p) for p in regenerated)}"
        )
        differing = [
            os.path.basename(path)
            for path in snapshot
            if regenerated.get(path) != snapshot[path]
        ]
        assert not differing, (
            "The regenerated modules are not byte-for-byte identical to the "
            f"committed ones: {differing}"
        )
    finally:
        for path, data in snapshot.items():
            current = None
            if os.path.isfile(path):
                with open(path, "rb") as handle:
                    current = handle.read()
            if current != data:
                with open(path, "wb") as handle:
                    handle.write(data)


# --------------------------------------------------------------------------- #
# 25. Restart determinism
# --------------------------------------------------------------------------- #


def test_service_restart_is_deterministic(service, gel_client):
    before_batch = _get("/batches/BLK-1205", expect=200).json()
    before_totals = _post("/regions/totals", {}, expect=200).json()

    service.restart()

    after_batch = _get("/batches/BLK-1205", expect=200).json()
    after_totals = _post("/regions/totals", {}, expect=200).json()
    assert after_batch == before_batch, (
        "GET /batches/BLK-1205 changed across a restart.\n"
        f"before: {before_batch}\nafter: {after_batch}"
    )
    assert after_totals == before_totals, (
        "POST /regions/totals changed across a restart.\n"
        f"before: {before_totals}\nafter: {after_totals}"
    )
    detail = _get("/batches/BLK-303", expect=200).json()
    assert detail["inspection_count"] == 2, (
        f"BLK-303 must still report two inspections after a restart: {detail}"
    )
