import glob
import json
import os
import subprocess

import pytest

PROJECT_DIR = "/home/user/functions-lab"
MIGRATIONS_DIR = os.path.join(PROJECT_DIR, "dbschema", "migrations")
QUOTE_SCRIPT = os.path.join(PROJECT_DIR, "quote.sh")
START_HELPER = "/usr/local/bin/gel-start"

VERIFICATION_SKUS = ("SKU-8001", "SKU-8002", "SKU-8003")

EXPECTED_FUNCTIONS = [
    {
        "name": "logistics::batch_total_cents",
        "volatility": "Immutable",
        "return_type": "std::int64",
        "return_typemod": "SingletonType",
        "params": [
            {
                "name": "quotes",
                "kind": "VariadicParam",
                "typemod": "SingletonType",
                "type": "array<std::int64>",
            }
        ],
    },
    {
        "name": "logistics::billable_grams",
        "volatility": "Immutable",
        "return_type": "std::int64",
        "return_typemod": "SingletonType",
        "params": [
            {
                "name": "grams",
                "kind": "PositionalParam",
                "typemod": "SingletonType",
                "type": "std::int64",
            }
        ],
    },
    {
        "name": "logistics::billable_grams",
        "volatility": "Immutable",
        "return_type": "std::int64",
        "return_typemod": "SingletonType",
        "params": [
            {
                "name": "grams",
                "kind": "PositionalParam",
                "typemod": "SingletonType",
                "type": "std::int64",
            },
            {
                "name": "tier",
                "kind": "PositionalParam",
                "typemod": "SingletonType",
                "type": "logistics::shipping_tier",
            },
        ],
    },
    {
        "name": "logistics::delivery_note",
        "volatility": "Immutable",
        "return_type": "std::str",
        "return_typemod": "SingletonType",
        "params": [
            {
                "name": "tier",
                "kind": "PositionalParam",
                "typemod": "SingletonType",
                "type": "logistics::shipping_tier",
            },
            {
                "name": "hint",
                "kind": "PositionalParam",
                "typemod": "OptionalType",
                "type": "std::str",
            },
        ],
    },
    {
        "name": "logistics::heaviest_grams",
        "volatility": "Immutable",
        "return_type": "std::int64",
        "return_typemod": "OptionalType",
        "params": [
            {
                "name": "parcels",
                "kind": "PositionalParam",
                "typemod": "SingletonType",
                "type": "array<std::int64>",
            }
        ],
    },
    {
        "name": "logistics::quote_cents",
        "volatility": "Immutable",
        "return_type": "std::int64",
        "return_typemod": "SingletonType",
        "params": [
            {
                "name": "grams",
                "kind": "PositionalParam",
                "typemod": "SingletonType",
                "type": "std::int64",
            },
            {
                "name": "tier",
                "kind": "PositionalParam",
                "typemod": "SingletonType",
                "type": "logistics::shipping_tier",
            },
            {
                "name": "insured",
                "kind": "NamedOnlyParam",
                "typemod": "SingletonType",
                "type": "std::bool",
            },
        ],
    },
    {
        "name": "logistics::tariff_version",
        "volatility": "Stable",
        "return_type": "std::str",
        "return_typemod": "SingletonType",
        "params": [],
    },
]

EXPECTED_SCALARS = [
    {
        "name": "logistics::packable_grams",
        "base": "std::int64",
        "enum_values": None,
        "constraint_errmessages": {
            "std::min_value": "weight must be at least 50 grams",
            "logistics::multiple_of": "weight must be a multiple of {step} grams",
        },
    },
    {
        "name": "logistics::shipping_tier",
        "base": "std::anyenum",
        "enum_values": ["Ground", "Express", "Overnight"],
        "constraint_errmessages": {},
    },
    {
        "name": "logistics::sku_code",
        "base": "std::str",
        "enum_values": None,
        "constraint_errmessages": {"std::regexp": "sku must match SKU-0000"},
    },
]

EXPECTED_ABSTRACT_CONSTRAINT = {
    "name": "logistics::multiple_of",
    "errmessage": "weight must be a multiple of {step} grams",
    "param_names": ["step"],
}

EXPECTED_SHP_9001 = {
    "shipment": "SHP-9001",
    "tariff_version": "2026.02",
    "parcel_count": 3,
    "parcels": [
        {
            "sku": "SKU-1001",
            "tier": "Express",
            "weight_grams": 1200,
            "chargeable_grams": 1500,
            "price_cents": 135,
            "note": "express/fragile",
        },
        {
            "sku": "SKU-1002",
            "tier": "Ground",
            "weight_grams": 400,
            "chargeable_grams": 500,
            "price_cents": 270,
            "note": "ground/default",
        },
        {
            "sku": "SKU-1003",
            "tier": "Overnight",
            "weight_grams": 2000,
            "chargeable_grams": 3000,
            "price_cents": 450,
            "note": "overnight/default",
        },
    ],
    "heaviest_parcel_grams": 2000,
    "total_quote_cents": 855,
    "batch_total_cents": 855,
}

EXPECTED_SHP_9002 = {
    "shipment": "SHP-9002",
    "tariff_version": "2026.02",
    "parcel_count": 0,
    "parcels": [],
    "heaviest_parcel_grams": None,
    "total_quote_cents": 0,
    "batch_total_cents": 0,
}

CARRIER_SUBQUERY = "assert_single((select Carrier filter .name = 'Northwind'))"


def _run(args, cwd=None, timeout=300):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def _gel(query):
    return _run(["gel", "query", "-F", "json", query])


def _gel_json(query):
    proc = _gel(query)
    assert proc.returncode == 0, (
        f"'gel query' failed for {query!r}:\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    return json.loads(proc.stdout)


def _quote(*args, cwd="/tmp"):
    return _run(["bash", QUOTE_SCRIPT, *args], cwd=cwd)


def _shape_functions(rows):
    shaped = []
    for row in rows:
        params = sorted(row["params"], key=lambda p: p["num"])
        shaped.append(
            {
                "name": row["name"],
                "volatility": row["volatility"],
                "return_type": row["return_type"]["name"],
                "return_typemod": row["return_typemod"],
                "params": [
                    {
                        "name": p["name"],
                        "kind": p["kind"],
                        "typemod": p["typemod"],
                        "type": p["type"]["name"],
                    }
                    for p in params
                ],
            }
        )
    shaped.sort(key=lambda f: (f["name"], len(f["params"])))
    return shaped


def _shape_scalars(rows):
    shaped = []
    for row in rows:
        shaped.append(
            {
                "name": row["name"],
                "base": row["bases"][0]["name"],
                "enum_values": row["enum_values"],
                "constraint_errmessages": {
                    c["name"]: c["errmessage"] for c in row["constraints"]
                },
            }
        )
    shaped.sort(key=lambda s: s["name"])
    return shaped


def _live_functions():
    rows = _gel_json(
        "select schema::Function { name, volatility, return_typemod, "
        "return_type: { name }, params: { name, kind, num, typemod, default, "
        "type: { name } } } filter .name like 'logistics::%'"
    )
    return _shape_functions(rows)


def _live_scalars():
    rows = _gel_json(
        "select schema::ScalarType { name, enum_values, bases: { name }, "
        "constraints: { name, errmessage } } filter .name like 'logistics::%'"
    )
    return _shape_scalars(rows)


@pytest.fixture(scope="session")
def client():
    """Make sure the local Gel instance is reachable and clean verification leftovers."""
    probe = _gel("select 1")
    if probe.returncode != 0:
        assert os.path.isfile(START_HELPER), (
            f"Gel is not reachable and the start helper {START_HELPER} is missing."
        )
        started = _run(["bash", START_HELPER])
        assert started.returncode == 0, (
            f"Failed to start the local Gel instance:\n{started.stdout}\n{started.stderr}"
        )
        probe = _gel("select 1")
    assert probe.returncode == 0, (
        f"Local Gel instance is not reachable:\nstdout={probe.stdout}\nstderr={probe.stderr}"
    )
    skus = ", ".join(f"'{sku}'" for sku in VERIFICATION_SKUS)
    _gel(f"delete Parcel filter .sku in {{{skus}}}")
    return True


def test_migration_history_in_sync(client):
    proc = _run(["gel", "migration", "status"], cwd=PROJECT_DIR)
    combined = (proc.stdout + proc.stderr).lower()
    assert proc.returncode == 0 and "up to date" in combined, (
        "Expected 'gel migration status' to report the database is up to date, got:\n"
        f"returncode={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )


def test_new_migration_files_recorded():
    migrations = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql")))
    assert len(migrations) >= 2, (
        f"Expected at least two migration files in {MIGRATIONS_DIR}, found {migrations}."
    )


def test_function_catalog_overloads_and_volatility(client):
    rows = _gel_json(
        "select schema::Function { name, volatility, params: { name } } "
        "filter .name like 'logistics::%'"
    )
    by_key = {(r["name"], len(r["params"])): r for r in rows}
    billable = [r for r in rows if r["name"] == "logistics::billable_grams"]
    assert len(billable) == 2, (
        f"Expected exactly two functions named logistics::billable_grams, found {billable}."
    )
    assert sorted(len(r["params"]) for r in billable) == [1, 2], (
        "Expected one single-parameter and one two-parameter logistics::billable_grams overload, "
        f"got {[len(r['params']) for r in billable]}."
    )
    expected_volatility = {
        ("logistics::billable_grams", 1): "Immutable",
        ("logistics::billable_grams", 2): "Immutable",
        ("logistics::quote_cents", 3): "Immutable",
        ("logistics::batch_total_cents", 1): "Immutable",
        ("logistics::heaviest_grams", 1): "Immutable",
        ("logistics::delivery_note", 2): "Immutable",
        ("logistics::tariff_version", 0): "Stable",
    }
    for key, volatility in expected_volatility.items():
        assert key in by_key, (
            f"Function {key[0]} with {key[1]} parameter(s) was not found in the schema catalog; "
            f"available: {sorted(by_key)}."
        )
        assert by_key[key]["volatility"] == volatility, (
            f"Expected volatility {volatility!r} for {key[0]} with {key[1]} parameter(s), "
            f"got {by_key[key]['volatility']!r}."
        )


def test_parameter_catalog_kinds_and_typemods(client):
    rows = _gel_json(
        "select schema::Function { name, return_typemod, params: { name, kind, num, "
        "typemod, default, type: { name } } } filter .name like 'logistics::%'"
    )
    by_key = {(r["name"], len(r["params"])): r for r in rows}

    quote = by_key.get(("logistics::quote_cents", 3))
    assert quote is not None, "logistics::quote_cents with three parameters is missing."
    params = sorted(quote["params"], key=lambda p: p["num"])
    signature = [(p["name"], p["kind"], p["typemod"], p["type"]["name"]) for p in params]
    assert signature == [
        ("grams", "PositionalParam", "SingletonType", "std::int64"),
        ("tier", "PositionalParam", "SingletonType", "logistics::shipping_tier"),
        ("insured", "NamedOnlyParam", "SingletonType", "std::bool"),
    ], f"Unexpected logistics::quote_cents signature: {signature}."
    assert params[2]["default"] == "false", (
        f"Expected the named-only 'insured' parameter to default to false, got {params[2]['default']!r}."
    )

    batch = by_key.get(("logistics::batch_total_cents", 1))
    assert batch is not None, "logistics::batch_total_cents is missing."
    quotes_param = batch["params"][0]
    assert quotes_param["name"] == "quotes", (
        f"Expected the variadic parameter to be named 'quotes', got {quotes_param['name']!r}."
    )
    assert quotes_param["kind"] == "VariadicParam", (
        f"Expected 'quotes' to be a VariadicParam, got {quotes_param['kind']!r}."
    )
    assert quotes_param["type"]["name"] == "array<std::int64>", (
        "Expected the catalog to record the variadic parameter type as array<std::int64>, "
        f"got {quotes_param['type']['name']!r}."
    )

    note = by_key.get(("logistics::delivery_note", 2))
    assert note is not None, "logistics::delivery_note with two parameters is missing."
    hint = [p for p in note["params"] if p["name"] == "hint"]
    assert hint and hint[0]["typemod"] == "OptionalType", (
        f"Expected logistics::delivery_note's 'hint' parameter to be optional, got {note['params']}."
    )

    heaviest = by_key.get(("logistics::heaviest_grams", 1))
    assert heaviest is not None, "logistics::heaviest_grams is missing."
    assert heaviest["return_typemod"] == "OptionalType", (
        "Expected logistics::heaviest_grams to have an optional return type modifier, "
        f"got {heaviest['return_typemod']!r}."
    )

    assert ("logistics::tariff_version", 0) in by_key, (
        "Expected logistics::tariff_version to take no parameters."
    )


def test_billable_grams_behaviour(client):
    cases = [
        ("select logistics::billable_grams(1200, logistics::shipping_tier.Express)", 1500),
        ("select logistics::billable_grams(2000, logistics::shipping_tier.Overnight)", 3000),
        ("select logistics::billable_grams(400, logistics::shipping_tier.Ground)", 500),
        ("select logistics::billable_grams(120)", 500),
        ("select logistics::billable_grams(900)", 900),
    ]
    for query, expected in cases:
        result = _gel_json(query)
        assert result == [expected], f"Expected {query} to return {expected}, got {result}."


def test_quote_cents_behaviour(client):
    cases = [
        ("select logistics::quote_cents(1200, logistics::shipping_tier.Express)", 135),
        (
            "select logistics::quote_cents(400, logistics::shipping_tier.Ground, insured := true)",
            270,
        ),
        ("select logistics::quote_cents(2000, logistics::shipping_tier.Overnight)", 450),
        ("select logistics::quote_cents(50, logistics::shipping_tier.Ground)", 20),
    ]
    for query, expected in cases:
        result = _gel_json(query)
        assert result == [expected], f"Expected {query} to return {expected}, got {result}."


def test_variadic_and_array_aggregate_boundaries(client):
    assert _gel_json("select logistics::batch_total_cents(135, 270, 450)") == [855], (
        "logistics::batch_total_cents(135, 270, 450) must return 855."
    )
    assert _gel_json("select logistics::batch_total_cents()") == [0], (
        "logistics::batch_total_cents() with no arguments must return 0."
    )
    assert _gel_json("select logistics::heaviest_grams([300, 1200, 900])") == [1200], (
        "logistics::heaviest_grams([300, 1200, 900]) must return 1200."
    )
    assert _gel_json("select logistics::heaviest_grams(<array<int64>>[])") == [], (
        "logistics::heaviest_grams(<array<int64>>[]) must return an empty set."
    )


def test_optional_parameter_and_tariff_version(client):
    assert _gel_json(
        "select logistics::delivery_note(logistics::shipping_tier.Express, <str>{})"
    ) == ["express/default"], (
        "logistics::delivery_note with an empty hint must return 'express/default'."
    )
    assert _gel_json(
        "select logistics::delivery_note(logistics::shipping_tier.Overnight, 'fragile')"
    ) == ["overnight/fragile"], (
        "logistics::delivery_note(Overnight, 'fragile') must return 'overnight/fragile'."
    )
    assert _gel_json("select logistics::tariff_version()") == ["2026.02"], (
        "logistics::tariff_version() must return '2026.02'."
    )


def test_scalar_types_and_abstract_constraint(client):
    scalars = {row["name"]: row for row in _live_scalars()}
    for expected in EXPECTED_SCALARS:
        actual = scalars.get(expected["name"])
        assert actual is not None, (
            f"Scalar type {expected['name']} was not found; available: {sorted(scalars)}."
        )
        assert actual == expected, (
            f"Unexpected catalog entry for {expected['name']}:\nexpected={expected}\nactual={actual}"
        )

    abstract = _gel_json(
        "select schema::Constraint { name, errmessage, params: { name, num, type: { name } } } "
        "filter .abstract and .name like 'logistics::%'"
    )
    assert len(abstract) == 1, (
        f"Expected exactly one abstract constraint in module logistics, got {abstract}."
    )
    entry = abstract[0]
    assert entry["name"] == "logistics::multiple_of", (
        f"Expected the abstract constraint to be logistics::multiple_of, got {entry['name']!r}."
    )
    assert entry["errmessage"] == "weight must be a multiple of {step} grams", (
        f"Unexpected errmessage template on logistics::multiple_of: {entry['errmessage']!r}."
    )
    declared = [
        (p["name"], p["type"]["name"])
        for p in sorted(entry["params"], key=lambda p: p["num"])
        if p["name"] != "__subject__"
    ]
    assert declared == [("step", "std::int64")], (
        f"Expected logistics::multiple_of to declare one int64 parameter 'step', got {declared}."
    )

    constraint_args = _gel_json(
        "select schema::ScalarType { name, constraints: { name, params: { name, @value } } } "
        "filter .name = 'logistics::packable_grams'"
    )
    assert constraint_args, "Scalar type logistics::packable_grams was not found."
    values = {
        c["name"]: {p["name"]: p["@value"] for p in c["params"]}
        for c in constraint_args[0]["constraints"]
    }
    assert values.get("logistics::multiple_of", {}).get("step") == "50", (
        f"Expected logistics::multiple_of(step := 50) on packable_grams, got {values}."
    )
    assert values.get("std::min_value", {}).get("min") == "50", (
        f"Expected std::min_value(50) on packable_grams, got {values}."
    )


def test_constraint_violations_report_exact_messages(client):
    cases = [
        (
            f"insert Parcel {{ sku := <logistics::sku_code>'SKU-8001', "
            f"weight_grams := <logistics::packable_grams>1230, "
            f"tier := logistics::shipping_tier.Ground, carrier := {CARRIER_SUBQUERY} }}",
            "weight must be a multiple of 50 grams",
        ),
        (
            f"insert Parcel {{ sku := <logistics::sku_code>'SKU-8002', "
            f"weight_grams := <logistics::packable_grams>0, "
            f"tier := logistics::shipping_tier.Ground, carrier := {CARRIER_SUBQUERY} }}",
            "weight must be at least 50 grams",
        ),
        (
            f"insert Parcel {{ sku := <logistics::sku_code>'BAD-1', "
            f"weight_grams := <logistics::packable_grams>500, "
            f"tier := logistics::shipping_tier.Ground, carrier := {CARRIER_SUBQUERY} }}",
            "sku must match SKU-0000",
        ),
    ]
    for query, message in cases:
        proc = _gel(query)
        assert proc.returncode != 0, (
            f"Expected the insert to be rejected, but it succeeded:\n{query}\n{proc.stdout}"
        )
        combined = proc.stdout + proc.stderr
        assert message in combined, (
            f"Expected the rejection message {message!r} for:\n{query}\ngot:\n{combined}"
        )

    leftovers = _gel_json(
        "select Parcel { sku } filter .sku in {'SKU-8001', 'SKU-8002', 'BAD-1'}"
    )
    assert leftovers == [], f"Rejected parcels must not be persisted, found {leftovers}."


def test_seed_parcels_have_expected_computed_values(client):
    rows = _gel_json(
        "select Parcel { sku, weight_grams, chargeable_grams, price_cents, note, "
        "carrier: { name } } order by .sku"
    )
    actual = [
        (
            row["sku"],
            row["weight_grams"],
            row["chargeable_grams"],
            row["price_cents"],
            row["note"],
            row["carrier"]["name"],
        )
        for row in rows
    ]
    expected = [
        ("SKU-1001", 1200, 1500, 135, "express/fragile", "Northwind"),
        ("SKU-1002", 400, 500, 270, "ground/default", "Northwind"),
        ("SKU-1003", 2000, 3000, 450, "overnight/default", "Halcyon"),
        ("SKU-1004", 50, 500, 20, "ground/sample", "Halcyon"),
    ]
    assert actual == expected, (
        f"Unexpected parcels / computed values.\nexpected={expected}\nactual={actual}"
    )


def test_shipment_computed_aggregates(client):
    rows = _gel_json(
        "select Shipment { code, total_quote_cents, heaviest_parcel_grams, "
        "parcel_count := count(.parcels) } order by .code"
    )
    assert rows == [
        {
            "code": "SHP-9001",
            "total_quote_cents": 855,
            "heaviest_parcel_grams": 2000,
            "parcel_count": 3,
        },
        {
            "code": "SHP-9002",
            "total_quote_cents": 0,
            "heaviest_parcel_grams": None,
            "parcel_count": 0,
        },
    ], f"Unexpected shipment computed values: {rows}."


def test_existing_carrier_data_untouched(client):
    rows = _gel_json("select Carrier { name, hub_code } order by .name")
    assert rows == [
        {"name": "Halcyon", "hub_code": "SEA"},
        {"name": "Northwind", "hub_code": "PDX"},
    ], f"The pre-existing Carrier rows must stay unchanged, got {rows}."


def test_report_command_shipment_mode(client):
    proc = _quote("--shipment", "SHP-9001")
    assert proc.returncode == 0, (
        f"'quote.sh --shipment SHP-9001' must exit 0, got {proc.returncode}:\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"stdout must be exactly one JSON object ({exc}): {proc.stdout!r}")
    assert list(payload.keys()) == [
        "shipment",
        "tariff_version",
        "parcel_count",
        "parcels",
        "heaviest_parcel_grams",
        "total_quote_cents",
        "batch_total_cents",
    ], f"Unexpected top-level key order: {list(payload.keys())}."
    for parcel in payload["parcels"]:
        assert list(parcel.keys()) == [
            "sku",
            "tier",
            "weight_grams",
            "chargeable_grams",
            "price_cents",
            "note",
        ], f"Unexpected parcel key order: {list(parcel.keys())}."
    assert payload == EXPECTED_SHP_9001, (
        f"Unexpected report for SHP-9001.\nexpected={EXPECTED_SHP_9001}\nactual={payload}"
    )


def test_report_command_empty_shipment(client):
    proc = _quote("--shipment", "SHP-9002")
    assert proc.returncode == 0, (
        f"'quote.sh --shipment SHP-9002' must exit 0, got {proc.returncode}:\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert payload == EXPECTED_SHP_9002, (
        f"Unexpected report for the empty shipment SHP-9002.\n"
        f"expected={EXPECTED_SHP_9002}\nactual={payload}"
    )


def test_report_command_unknown_shipment_and_bad_usage(client):
    proc = _quote("--shipment", "SHP-0000")
    assert proc.returncode == 3, (
        f"An unknown shipment must exit 3, got {proc.returncode}:\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert payload == {"error": "unknown shipment", "shipment": "SHP-0000"}, (
        f"Unexpected unknown-shipment payload: {payload}."
    )

    proc = _quote()
    assert proc.returncode == 2, (
        f"Running the report command without arguments must exit 2, got {proc.returncode}:\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert proc.stdout.strip() == "", (
        f"Bad usage must not write anything to stdout, got {proc.stdout!r}."
    )


def test_report_command_introspect_mode(client):
    proc = _quote("--introspect")
    assert proc.returncode == 0, (
        f"'quote.sh --introspect' must exit 0, got {proc.returncode}:\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"stdout must be exactly one JSON object ({exc}): {proc.stdout!r}")
    assert list(payload.keys()) == ["functions", "scalar_types", "abstract_constraints"], (
        f"Unexpected top-level key order in --introspect output: {list(payload.keys())}."
    )

    functions = payload["functions"]
    for entry in functions:
        assert list(entry.keys()) == [
            "name",
            "volatility",
            "return_type",
            "return_typemod",
            "params",
        ], f"Unexpected function key order: {list(entry.keys())}."
        for param in entry["params"]:
            assert list(param.keys()) == ["name", "kind", "typemod", "type"], (
                f"Unexpected parameter key order: {list(param.keys())}."
            )
    order_keys = [(entry["name"], len(entry["params"])) for entry in functions]
    assert order_keys == sorted(order_keys), (
        f"--introspect functions must be sorted by name then parameter count, got {order_keys}."
    )
    assert sum(1 for e in functions if e["name"] == "logistics::billable_grams") == 2, (
        "--introspect must report both logistics::billable_grams overloads."
    )
    for expected in EXPECTED_FUNCTIONS:
        assert expected in functions, (
            f"Missing or incorrect --introspect entry for {expected['name']} with "
            f"{len(expected['params'])} parameter(s).\nexpected={expected}\nreported={functions}"
        )

    scalars = payload["scalar_types"]
    for entry in scalars:
        assert list(entry.keys()) == [
            "name",
            "base",
            "enum_values",
            "constraint_errmessages",
        ], f"Unexpected scalar key order: {list(entry.keys())}."
    scalar_names = [entry["name"] for entry in scalars]
    assert scalar_names == sorted(scalar_names), (
        f"--introspect scalar_types must be sorted by name, got {scalar_names}."
    )
    for expected in EXPECTED_SCALARS:
        assert expected in scalars, (
            f"Missing or incorrect --introspect entry for scalar {expected['name']}.\n"
            f"expected={expected}\nreported={scalars}"
        )

    constraints = payload["abstract_constraints"]
    for entry in constraints:
        assert list(entry.keys()) == ["name", "errmessage", "param_names"], (
            f"Unexpected abstract constraint key order: {list(entry.keys())}."
        )
    constraint_names = [entry["name"] for entry in constraints]
    assert constraint_names == sorted(constraint_names), (
        f"--introspect abstract_constraints must be sorted by name, got {constraint_names}."
    )
    assert EXPECTED_ABSTRACT_CONSTRAINT in constraints, (
        f"Missing or incorrect abstract constraint entry.\n"
        f"expected={EXPECTED_ABSTRACT_CONSTRAINT}\nreported={constraints}"
    )


def test_introspect_output_matches_live_catalog(client):
    proc = _quote("--introspect")
    assert proc.returncode == 0, (
        f"'quote.sh --introspect' must exit 0, got {proc.returncode}:\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert payload["functions"] == _live_functions(), (
        "--introspect functions must match what the schema catalog reports live.\n"
        f"reported={payload['functions']}\nlive={_live_functions()}"
    )
    assert payload["scalar_types"] == _live_scalars(), (
        "--introspect scalar_types must match what the schema catalog reports live.\n"
        f"reported={payload['scalar_types']}\nlive={_live_scalars()}"
    )
