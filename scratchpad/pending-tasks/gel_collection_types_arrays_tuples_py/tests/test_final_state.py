import glob
import json
import os
import subprocess

import gel
import pytest

PROJECT_DIR = "/home/user/collections"
CATALOG_PY = os.path.join(PROJECT_DIR, "catalog.py")
DATA_FILE = os.path.join(PROJECT_DIR, "data", "instruments.json")
CATALOG_JSON = os.path.join(PROJECT_DIR, "catalog.json")
LIVE_CATALOG_JSON = "/tmp/catalog_check.json"
ENSURE_SCRIPT = "/usr/local/bin/gel-ensure.sh"

EXTRA_CODE = "zeta-\u65b0"


def rng(lower, upper):
    return {
        "lower": lower,
        "upper": upper,
        "inc_lower": True,
        "inc_upper": False,
    }


EMPTY_RANGE = {"empty": True}

EXPECTED_SEED = [
    {
        "code": "alpha",
        "labels": ["north", "south", "north"],
        "tags": ["core", "beta", "core"],
        "span": {"lower": 0, "upper": 5, "inc_lower": False, "inc_upper": True},
        "coverage": [{"lower": 1, "upper": 5}, {"lower": 8, "upper": 10}],
        "origin": {"region": "eu-west", "priority": 3},
    },
    {
        "code": "beta-\u03a9",
        "labels": [],
        "tags": [],
        "span": {"empty": True},
        "coverage": [],
        "origin": {"region": "\u65e5\u672c", "priority": 0},
    },
    {
        "code": "gamma",
        "labels": ["caf\u00e9", "\u65e5\u672c\u8a9e", "emoji-\U0001f680"],
        "tags": ["core", "unicode-\u03a9"],
        "span": {"lower": -3, "upper": 3},
        "coverage": [{"lower": 2, "upper": 12}, {"lower": 0, "upper": 3}],
        "origin": {"region": "apac", "priority": 7},
    },
    {
        "code": "delta",
        "labels": ["north"],
        "tags": ["core", "edge"],
        "span": {"lower": 4, "upper": 9, "inc_upper": True},
        "coverage": [{"lower": 3, "upper": 6}, {"lower": 6, "upper": 9}],
        "origin": {"region": "us-east", "priority": 1},
    },
    {
        "code": "epsilon",
        "labels": ["", "north"],
        "tags": ["edge"],
        "span": {"lower": 7, "upper": 7},
        "coverage": [{"lower": -5, "upper": -1}, {"lower": 4, "upper": 5}],
        "origin": {"region": "eu-west", "priority": 2},
    },
]

EXPECTED_CODES = ["alpha", "beta-\u03a9", "delta", "epsilon", "gamma"]

EXPECTED_ENTRIES = {
    "alpha": {
        "code": "alpha",
        "labels": ["north", "south", "north"],
        "labels_joined": "north|south|north",
        "tags": ["beta", "core"],
        "tag_count": 2,
        "indexed_labels": [[0, "north"], [1, "south"], [2, "north"]],
        "span": rng(1, 6),
        "span_values": [1, 2, 3, 4, 5],
        "coverage": [rng(1, 5), rng(8, 10)],
        "coverage_size": 6,
        "origin": {"region": "eu-west", "priority": 3},
        "profile": {"code": "alpha", "label_count": 3, "tag_count": 2},
    },
    "beta-\u03a9": {
        "code": "beta-\u03a9",
        "labels": [],
        "labels_joined": "",
        "tags": [],
        "tag_count": 0,
        "indexed_labels": [],
        "span": EMPTY_RANGE,
        "span_values": [],
        "coverage": [],
        "coverage_size": 0,
        "origin": {"region": "\u65e5\u672c", "priority": 0},
        "profile": {"code": "beta-\u03a9", "label_count": 0, "tag_count": 0},
    },
    "delta": {
        "code": "delta",
        "labels": ["north"],
        "labels_joined": "north",
        "tags": ["core", "edge"],
        "tag_count": 2,
        "indexed_labels": [[0, "north"]],
        "span": rng(4, 10),
        "span_values": [4, 5, 6, 7, 8, 9],
        "coverage": [rng(3, 9)],
        "coverage_size": 6,
        "origin": {"region": "us-east", "priority": 1},
        "profile": {"code": "delta", "label_count": 1, "tag_count": 2},
    },
    "epsilon": {
        "code": "epsilon",
        "labels": ["", "north"],
        "labels_joined": "|north",
        "tags": ["edge"],
        "tag_count": 1,
        "indexed_labels": [[0, ""], [1, "north"]],
        "span": EMPTY_RANGE,
        "span_values": [],
        "coverage": [rng(-5, -1), rng(4, 5)],
        "coverage_size": 5,
        "origin": {"region": "eu-west", "priority": 2},
        "profile": {"code": "epsilon", "label_count": 2, "tag_count": 1},
    },
    "gamma": {
        "code": "gamma",
        "labels": ["caf\u00e9", "\u65e5\u672c\u8a9e", "emoji-\U0001f680"],
        "labels_joined": "caf\u00e9|\u65e5\u672c\u8a9e|emoji-\U0001f680",
        "tags": ["core", "unicode-\u03a9"],
        "tag_count": 2,
        "indexed_labels": [
            [0, "caf\u00e9"],
            [1, "\u65e5\u672c\u8a9e"],
            [2, "emoji-\U0001f680"],
        ],
        "span": rng(-3, 3),
        "span_values": [-3, -2, -1, 0, 1, 2],
        "coverage": [rng(0, 12)],
        "coverage_size": 12,
        "origin": {"region": "apac", "priority": 7},
        "profile": {"code": "gamma", "label_count": 3, "tag_count": 2},
    },
}

EXPECTED_TOTALS = {
    "instrument_count": 5,
    "all_tags": ["beta", "core", "edge", "unicode-\u03a9"],
    "label_histogram": {
        "": 1,
        "caf\u00e9": 1,
        "emoji-\U0001f680": 1,
        "north": 3,
        "south": 1,
        "\u65e5\u672c\u8a9e": 1,
    },
    "union_coverage": [rng(-5, -1), rng(0, 12)],
    "union_size": 16,
    "core_intersection": [rng(3, 5), rng(8, 9)],
}

INSTRUMENT_KEYS = {
    "code",
    "labels",
    "labels_joined",
    "tags",
    "tag_count",
    "indexed_labels",
    "span",
    "span_values",
    "coverage",
    "coverage_size",
    "origin",
    "profile",
}


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def gel_server():
    """Start the local Gel server (idempotent); every DB/CLI test depends on it."""
    proc = subprocess.run(
        [ENSURE_SCRIPT], capture_output=True, text=True, timeout=300
    )
    assert proc.returncode == 0, (
        f"{ENSURE_SCRIPT} failed (exit {proc.returncode}). "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    return True


@pytest.fixture(scope="session")
def client(gel_server):
    db = gel.create_client()
    try:
        db.query("select 1")
        yield db
    finally:
        db.close()


def run_catalog(args, timeout=300):
    return subprocess.run(
        ["python3", "catalog.py", *args],
        capture_output=True,
        text=True,
        cwd=PROJECT_DIR,
        timeout=timeout,
    )


@pytest.fixture(scope="session")
def seeded(client):
    """Run `seed` twice: it has to be idempotent."""
    for attempt in (1, 2):
        proc = run_catalog(["seed"])
        assert proc.returncode == 0, (
            f"`python3 catalog.py seed` run #{attempt} exited with "
            f"{proc.returncode}. stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )
        assert proc.stdout.strip() == "", (
            f"`python3 catalog.py seed` run #{attempt} must not print on stdout, "
            f"got {proc.stdout!r}"
        )
    return True


@pytest.fixture(scope="session")
def catalog(seeded):
    """Produce the catalog document from a clean slate and parse it."""
    if os.path.exists(CATALOG_JSON):
        os.remove(CATALOG_JSON)
    proc = run_catalog(["export", "--out", CATALOG_JSON])
    assert proc.returncode == 0, (
        f"`python3 catalog.py export --out {CATALOG_JSON}` exited with "
        f"{proc.returncode}. stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert proc.stdout.strip() == "", (
        f"`export` must not print on stdout, got {proc.stdout!r}"
    )
    assert os.path.isfile(CATALOG_JSON), f"{CATALOG_JSON} was not created."
    with open(CATALOG_JSON, "rb") as handle:
        raw = handle.read()
    text = raw.decode("utf-8")
    return json.loads(text), text


# ---------------------------------------------------------------------------
# project / migration state
# ---------------------------------------------------------------------------


def test_catalog_entrypoint_exists():
    assert os.path.isfile(CATALOG_PY), f"Expected the CLI entrypoint at {CATALOG_PY}."


def test_input_data_file_untouched():
    with open(DATA_FILE, encoding="utf-8") as handle:
        payload = json.load(handle)
    assert payload == EXPECTED_SEED, (
        f"{DATA_FILE} must stay exactly as provided; its parsed content differs "
        "from the original input file."
    )


def test_migration_files_exist(client):
    migrations = sorted(
        glob.glob(os.path.join(PROJECT_DIR, "dbschema", "migrations", "*.edgeql"))
    )
    assert migrations, (
        "No migration script found under "
        f"{os.path.join(PROJECT_DIR, 'dbschema', 'migrations')}/*.edgeql."
    )
    applied = client.query("select schema::Migration { name }")
    assert len(applied) >= 1, (
        "The database reports no applied migration; the schema must be delivered "
        "through the migration system."
    )


def test_migration_status_is_in_sync(client):
    proc = subprocess.run(
        ["gel", "migration", "status"],
        capture_output=True,
        text=True,
        cwd=PROJECT_DIR,
        timeout=300,
    )
    assert proc.returncode == 0, (
        "`gel migration status` reported a problem "
        f"(exit {proc.returncode}). stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert "up to date" in proc.stdout.lower(), (
        f"`gel migration status` did not report an up-to-date database: {proc.stdout!r}"
    )


def test_instrument_schema_pointers(client):
    rows = client.query(
        """
        select schema::ObjectType {
            properties: {
                name,
                required,
                cardinality,
                computed := exists .expr,
                target: { name },
                constraints: { name },
            }
        }
        filter .name = 'default::Instrument'
        """
    )
    assert len(rows) == 1, (
        "Exactly one object type named 'default::Instrument' must exist, "
        f"found {len(rows)}."
    )
    props = {p.name: p for p in rows[0].properties}

    expected = {
        "code": ("std::str", True, "One", False),
        "labels": ("array<std::str>", True, "One", False),
        "tags": ("std::str", False, "Many", False),
        "span": ("range<std::int64>", True, "One", False),
        "coverage": ("multirange<std::int64>", True, "One", False),
        "origin": ("tuple<region:std::str, priority:std::int64>", True, "One", False),
        "profile": (
            "tuple<code:std::str, label_count:std::int64, tag_count:std::int64>",
            True,
            "One",
            True,
        ),
    }

    for name, (target, required, cardinality, computed) in expected.items():
        assert name in props, (
            f"Property '{name}' is missing on default::Instrument "
            f"(found: {sorted(props)})."
        )
        prop = props[name]
        got_target = "".join(str(prop.target.name).split())
        want_target = "".join(target.split())
        assert got_target == want_target, (
            f"Property '{name}' must be declared as {target!r}, "
            f"but its target type is {prop.target.name!r}."
        )
        assert bool(prop.required) is required, (
            f"Property '{name}' required flag is {prop.required}, expected {required}."
        )
        assert str(prop.cardinality) == cardinality, (
            f"Property '{name}' cardinality is {prop.cardinality!r}, "
            f"expected {cardinality!r}."
        )
        assert bool(prop.computed) is computed, (
            f"Property '{name}' computed flag is {prop.computed}, expected {computed}."
        )

    code_constraints = {c.name for c in props["code"].constraints}
    assert "std::exclusive" in code_constraints, (
        "Property 'code' must be unique across Instrument objects "
        f"(constraints found: {sorted(code_constraints)})."
    )


# ---------------------------------------------------------------------------
# seeding
# ---------------------------------------------------------------------------


def test_seed_is_idempotent(seeded, client):
    total = client.query_single("select count(Instrument)")
    distinct_codes = client.query_single("select count(distinct Instrument.code)")
    assert total == 5, (
        f"After running `seed` twice there must be exactly 5 Instrument objects, "
        f"found {total}."
    )
    assert distinct_codes == 5, (
        f"Expected 5 distinct instrument codes after seeding, found {distinct_codes}."
    )


def fetch_instrument(client, code):
    row = client.query_single(
        """
        select Instrument {
            code,
            labels,
            sorted_tags := array_agg((with t := .tags select t order by t)),
            span,
            coverage,
            origin,
            profile,
        }
        filter .code = <str>$code
        """,
        code=code,
    )
    assert row is not None, f"No Instrument with code {code!r} was stored."
    return row


def test_stored_values_alpha(seeded, client):
    row = fetch_instrument(client, "alpha")
    assert list(row.labels) == ["north", "south", "north"], (
        f"'alpha' labels must keep order and duplicates, got {list(row.labels)!r}."
    )
    assert list(row.sorted_tags) == ["beta", "core"], (
        f"'alpha' tags must collapse duplicates, got {list(row.sorted_tags)!r}."
    )
    assert row.span == gel.Range(1, 6), (
        f"'alpha' span must cover the integers 1..5, got {row.span!r}."
    )
    assert row.coverage == gel.MultiRange([gel.Range(1, 5), gel.Range(8, 10)]), (
        f"'alpha' coverage is wrong: {row.coverage!r}."
    )
    assert tuple(row.origin) == ("eu-west", 3), (
        f"'alpha' origin is wrong: {tuple(row.origin)!r}."
    )
    assert tuple(row.profile) == ("alpha", 3, 2), (
        f"'alpha' computed profile is wrong: {tuple(row.profile)!r}."
    )


def test_stored_values_beta_empty_collections(seeded, client):
    row = fetch_instrument(client, "beta-\u03a9")
    assert list(row.labels) == [], (
        f"'beta-\u03a9' must store an empty labels array, got {list(row.labels)!r}."
    )
    assert list(row.sorted_tags) == [], (
        f"'beta-\u03a9' must have no tags, got {list(row.sorted_tags)!r}."
    )
    assert row.span.is_empty(), f"'beta-\u03a9' span must be empty, got {row.span!r}."
    assert list(row.coverage) == [], (
        f"'beta-\u03a9' coverage must be an empty multirange, got {row.coverage!r}."
    )
    assert tuple(row.origin) == ("\u65e5\u672c", 0), (
        f"'beta-\u03a9' origin is wrong: {tuple(row.origin)!r}."
    )


def test_stored_values_delta_boundaries(seeded, client):
    row = fetch_instrument(client, "delta")
    assert row.span == gel.Range(4, 10), (
        "'delta' span was written with an inclusive upper bound of 9 and must cover "
        f"the integers 4..9, got {row.span!r}."
    )
    assert row.coverage == gel.MultiRange([gel.Range(3, 9)]), (
        "'delta' coverage components are adjacent and must be stored as a single "
        f"component, got {row.coverage!r}."
    )


def test_stored_values_gamma_overlapping_coverage(seeded, client):
    row = fetch_instrument(client, "gamma")
    assert row.coverage == gel.MultiRange([gel.Range(0, 12)]), (
        "'gamma' coverage components overlap and must be stored merged, "
        f"got {row.coverage!r}."
    )
    assert row.span == gel.Range(-3, 3), f"'gamma' span is wrong: {row.span!r}."


def test_stored_values_epsilon(seeded, client):
    row = fetch_instrument(client, "epsilon")
    assert list(row.labels) == ["", "north"], (
        f"'epsilon' labels are wrong: {list(row.labels)!r}."
    )
    assert row.span.is_empty(), (
        "'epsilon' span was written with equal lower and upper bounds and must be "
        f"empty, got {row.span!r}."
    )


# ---------------------------------------------------------------------------
# exported catalog
# ---------------------------------------------------------------------------


def test_catalog_top_level_shape(catalog):
    doc, _ = catalog
    assert isinstance(doc, dict), "The exported catalog must be a JSON object."
    assert set(doc) == {"instruments", "totals"}, (
        f"The catalog must have exactly the keys 'instruments' and 'totals', "
        f"found {sorted(doc)}."
    )


def test_catalog_instrument_order(catalog):
    doc, _ = catalog
    codes = [entry["code"] for entry in doc["instruments"]]
    assert codes == EXPECTED_CODES, (
        f"Instruments must be ordered by code ascending: expected {EXPECTED_CODES}, "
        f"got {codes}."
    )


@pytest.mark.parametrize("code", EXPECTED_CODES)
def test_catalog_instrument_entry(catalog, code):
    doc, _ = catalog
    entries = {entry.get("code"): entry for entry in doc["instruments"]}
    assert code in entries, f"No catalog entry for instrument {code!r}."
    entry = entries[code]
    assert set(entry) == INSTRUMENT_KEYS, (
        f"Entry {code!r} must contain exactly {sorted(INSTRUMENT_KEYS)}, "
        f"found {sorted(entry)}."
    )
    expected = EXPECTED_ENTRIES[code]
    for key in sorted(INSTRUMENT_KEYS):
        assert entry[key] == expected[key], (
            f"Entry {code!r} key {key!r}: expected {expected[key]!r}, "
            f"got {entry[key]!r}."
        )


def test_catalog_totals(catalog):
    doc, _ = catalog
    totals = doc["totals"]
    assert set(totals) == set(EXPECTED_TOTALS), (
        f"'totals' must contain exactly {sorted(EXPECTED_TOTALS)}, "
        f"found {sorted(totals)}."
    )
    for key in sorted(EXPECTED_TOTALS):
        assert totals[key] == EXPECTED_TOTALS[key], (
            f"totals[{key!r}]: expected {EXPECTED_TOTALS[key]!r}, got {totals[key]!r}."
        )


def test_catalog_uses_literal_unicode(catalog):
    _, text = catalog
    for needle in ("\u65e5\u672c\u8a9e", "emoji-\U0001f680", "caf\u00e9", "unicode-\u03a9"):
        assert needle in text, (
            f"The catalog file must contain the literal characters {needle!r}; "
            "escaped sequences are not accepted."
        )
    assert "\\u" not in text, (
        "The catalog file must not contain \\uXXXX escape sequences."
    )


# ---------------------------------------------------------------------------
# lookup subcommand
# ---------------------------------------------------------------------------


QUERY_CASES = [
    (
        "core,edge",
        "3:9",
        {
            "requested_tags": ["core", "edge"],
            "requested_span": rng(3, 9),
            "matched_codes": ["alpha", "delta"],
            "matched_count": 2,
            "unmatched_tags": [],
            "overlap_size": 8,
        },
    ),
    (
        "beta,core",
        "1:6",
        {
            "requested_tags": ["beta", "core"],
            "requested_span": rng(1, 6),
            "matched_codes": ["alpha", "delta", "gamma"],
            "matched_count": 3,
            "unmatched_tags": [],
            "overlap_size": 9,
        },
    ),
    (
        "unicode-\u03a9,zzz",
        "-5:100",
        {
            "requested_tags": ["unicode-\u03a9", "zzz"],
            "requested_span": rng(-5, 100),
            "matched_codes": ["gamma"],
            "matched_count": 1,
            "unmatched_tags": ["zzz"],
            "overlap_size": 6,
        },
    ),
    (
        "core,core",
        "4:4",
        {
            "requested_tags": ["core"],
            "requested_span": EMPTY_RANGE,
            "matched_codes": [],
            "matched_count": 0,
            "unmatched_tags": [],
            "overlap_size": 0,
        },
    ),
    (
        "",
        "0:10",
        {
            "requested_tags": [],
            "requested_span": rng(0, 10),
            "matched_codes": [],
            "matched_count": 0,
            "unmatched_tags": [],
            "overlap_size": 0,
        },
    ),
]


@pytest.mark.parametrize("tags,span,expected", QUERY_CASES)
def test_query_subcommand(seeded, tags, span, expected):
    proc = run_catalog(["query", "--tags", tags, "--span", span])
    assert proc.returncode == 0, (
        f"`query --tags {tags!r} --span {span!r}` exited with {proc.returncode}. "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"`query --tags {tags!r} --span {span!r}` must print a single JSON "
            f"object on stdout, got {proc.stdout!r} ({exc})."
        )
    assert set(payload) == set(expected), (
        f"The query result must contain exactly {sorted(expected)}, "
        f"found {sorted(payload)}."
    )
    for key in sorted(expected):
        assert payload[key] == expected[key], (
            f"`query --tags {tags!r} --span {span!r}` result[{key!r}]: expected "
            f"{expected[key]!r}, got {payload[key]!r}."
        )


@pytest.mark.parametrize("span", ["9:3", "abc"])
def test_query_rejects_invalid_span(seeded, span):
    proc = run_catalog(["query", "--tags", "core", "--span", span])
    assert proc.returncode == 2, (
        f"`query --tags core --span {span!r}` must exit with code 2, "
        f"got {proc.returncode}. stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert proc.stdout.strip() == "", (
        f"`query --tags core --span {span!r}` must print nothing on stdout, "
        f"got {proc.stdout!r}"
    )
    stderr_lines = [line for line in proc.stderr.splitlines() if line.strip()]
    assert stderr_lines, (
        f"`query --tags core --span {span!r}` must report the problem on stderr."
    )
    assert any(line.startswith("ERROR: ") for line in stderr_lines), (
        "stderr must contain a line starting with 'ERROR: ', got "
        f"{proc.stderr!r}"
    )


# ---------------------------------------------------------------------------
# anti-cheat: the export must read the live database
# ---------------------------------------------------------------------------


def test_export_reflects_live_database(seeded, client):
    if os.path.exists(LIVE_CATALOG_JSON):
        os.remove(LIVE_CATALOG_JSON)
    client.execute(
        """
        delete Instrument filter .code = <str>$code
        """,
        code=EXTRA_CODE,
    )
    client.execute(
        """
        insert Instrument {
            code := <str>$code,
            labels := ['z'],
            tags := {'core'},
            span := range(0, 2),
            coverage := multirange([range(100, 105)]),
            origin := (region := 'test', priority := 9),
        }
        """,
        code=EXTRA_CODE,
    )
    try:
        proc = run_catalog(["export", "--out", LIVE_CATALOG_JSON])
        assert proc.returncode == 0, (
            f"`export --out {LIVE_CATALOG_JSON}` exited with {proc.returncode}. "
            f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )
        with open(LIVE_CATALOG_JSON, encoding="utf-8") as handle:
            doc = json.load(handle)

        codes = [entry["code"] for entry in doc["instruments"]]
        assert codes == EXPECTED_CODES + [EXTRA_CODE], (
            "The export must reflect the current database contents; expected "
            f"{EXPECTED_CODES + [EXTRA_CODE]}, got {codes}."
        )

        totals = doc["totals"]
        assert totals["instrument_count"] == 6, (
            f"totals['instrument_count'] must be 6, got {totals['instrument_count']!r}."
        )
        assert totals["union_coverage"] == [rng(-5, -1), rng(0, 12), rng(100, 105)], (
            f"totals['union_coverage'] is wrong: {totals['union_coverage']!r}."
        )
        assert totals["union_size"] == 21, (
            f"totals['union_size'] must be 21, got {totals['union_size']!r}."
        )
        assert totals["core_intersection"] == [], (
            "The new instrument does not share any covered value with the other "
            "'core' instruments, so totals['core_intersection'] must be empty, got "
            f"{totals['core_intersection']!r}."
        )
        assert totals["label_histogram"].get("z") == 1, (
            "totals['label_histogram'] must count the new label 'z' once, got "
            f"{totals['label_histogram']!r}."
        )

        entry = [e for e in doc["instruments"] if e["code"] == EXTRA_CODE][0]
        expected_entry = {
            "code": EXTRA_CODE,
            "labels": ["z"],
            "labels_joined": "z",
            "tags": ["core"],
            "tag_count": 1,
            "indexed_labels": [[0, "z"]],
            "span": rng(0, 2),
            "span_values": [0, 1],
            "coverage": [rng(100, 105)],
            "coverage_size": 5,
            "origin": {"region": "test", "priority": 9},
            "profile": {"code": EXTRA_CODE, "label_count": 1, "tag_count": 1},
        }
        assert entry == expected_entry, (
            f"The entry for the newly inserted instrument is wrong: {entry!r}."
        )
    finally:
        client.execute(
            "delete Instrument filter .code = <str>$code", code=EXTRA_CODE
        )
