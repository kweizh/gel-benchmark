import json
import os
import re
import subprocess
from decimal import Decimal

import gel
import gel.errors
import pytest

PROJECT_DIR = "/home/user/pricing"
DBSCHEMA_DIR = os.path.join(PROJECT_DIR, "dbschema")
MIGRATIONS_DIR = os.path.join(DBSCHEMA_DIR, "migrations")

D = Decimal

SEED_SCRIPT = """
insert billing::Customer { name := 'Acme', discount_pct := <decimal>'10' };
insert billing::Customer { name := 'Globex' };
insert billing::Invoice {
    code := 'INV-1',
    customer := assert_single((select billing::Customer filter .name = 'Acme')),
    installment_count := 3,
};
insert billing::Invoice {
    code := 'INV-2',
    customer := assert_single((select billing::Customer filter .name = 'Acme')),
    minimum_charge := <decimal>'100.00',
};
insert billing::Invoice {
    code := 'INV-3',
    customer := assert_single((select billing::Customer filter .name = 'Globex')),
    paid := true,
};
insert billing::Invoice {
    code := 'INV-4',
    customer := assert_single((select billing::Customer filter .name = 'Globex')),
};
for x in {
    ('INV-1', 'Widget', 2, <decimal>'19.99'),
    ('INV-1', 'Setup', 1, <decimal>'5.00'),
    ('INV-2', 'Hosting', 3, <decimal>'10.00'),
    ('INV-3', 'Support', 1, <decimal>'7.77'),
}
union (
    insert billing::LineItem {
        invoice := assert_single((select billing::Invoice filter .code = x.0)),
        description := x.1,
        qty := x.2,
        unit_price := x.3,
    }
);
"""


@pytest.fixture(scope="session")
def server():
    """Bring up the local Gel server. Every DB/CLI test must depend on this."""
    proc = subprocess.run(
        ["gel-start"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=300,
    )
    print("gel-start stdout:\n" + proc.stdout)
    print("gel-start stderr:\n" + proc.stderr)
    assert proc.returncode == 0, (
        "Failed to start the local Gel server with 'gel-start'.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    return True


@pytest.fixture(scope="session")
def client(server):
    c = gel.create_client()
    c.ensure_connected()
    yield c
    c.close()


def _wipe(client):
    for stmt in (
        "delete billing::LineItem;",
        "delete billing::Invoice;",
        "delete billing::Customer;",
    ):
        client.execute(stmt)


@pytest.fixture()
def seeded(client):
    _wipe(client)
    client.execute(SEED_SCRIPT)
    yield client
    _wipe(client)


def _jq(client, query):
    return json.loads(client.query_json(query))


# ---------------------------------------------------------------- layout / modules


def test_three_modules_exist(client):
    names = _jq(client, "select schema::Module.name")
    for expected in ("util", "billing", "reports"):
        assert expected in names, (
            f"Schema module {expected!r} does not exist in the instance. Found: {sorted(names)}"
        )


def test_each_module_is_declared_in_its_own_schema_file():
    for module, filename in (
        ("util", "util.gel"),
        ("billing", "billing.gel"),
        ("reports", "reports.gel"),
    ):
        path = os.path.join(DBSCHEMA_DIR, filename)
        assert os.path.isfile(path), f"Expected schema file {path} to exist."
        content = open(path).read()
        assert re.search(r"\bmodule\s+" + module + r"\b", content), (
            f"{path} does not declare 'module {module}'."
        )


def test_migration_files_exist():
    assert os.path.isdir(MIGRATIONS_DIR), (
        f"Migration directory {MIGRATIONS_DIR} does not exist; the schema was never migrated."
    )
    migrations = sorted(f for f in os.listdir(MIGRATIONS_DIR) if f.endswith(".edgeql"))
    assert migrations, f"No .edgeql migration files found in {MIGRATIONS_DIR}."


def test_nothing_was_declared_in_the_default_module(client):
    object_types = _jq(
        client,
        "select (schema::ObjectType.name) "
        "filter schema::ObjectType.name like 'default::%' and not schema::ObjectType.builtin",
    )
    assert object_types == [], (
        f"Object types must not live in the 'default' module, found: {object_types}"
    )
    functions = _jq(
        client, "select (schema::Function.name) filter schema::Function.name like 'default::%'"
    )
    assert functions == [], (
        f"Functions must not live in the 'default' module, found: {functions}"
    )
    aliases = _jq(
        client, "select (schema::Alias.name) filter schema::Alias.name like 'default::%'"
    )
    assert aliases == [], f"Aliases must not live in the 'default' module, found: {aliases}"


def test_migration_status_is_in_sync(server):
    proc = subprocess.run(
        ["gel", "migration", "status", "--quiet"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        "'gel migration status --quiet' reports that the instance and dbschema/ are not in sync.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


# ---------------------------------------------------------------- signatures


def _functions(client):
    rows = _jq(
        client,
        """
        select schema::Function {
            name,
            volatility,
            return_typemod,
            return_type: { name },
            params: { name, num, kind, typemod, type: { name }, default } order by .num
        }
        filter .name like 'util::%' or .name = 'billing::customer_outstanding'
        """,
    )
    out = {}
    for row in rows:
        key = (row["name"], tuple(p["name"] for p in row["params"]))
        out[key] = row
    return out


def test_money_round_overloads_are_declared(client):
    funcs = _functions(client)
    one = funcs.get(("util::money_round", ("amount",)))
    two = funcs.get(("util::money_round", ("amount", "places")))
    assert one is not None, (
        "util::money_round(amount: decimal) is missing. Found: " + str(sorted(funcs))
    )
    assert two is not None, (
        "util::money_round(amount: decimal, places: int64) overload is missing. "
        "Found: " + str(sorted(funcs))
    )
    for fn in (one, two):
        assert fn["return_type"]["name"] == "std::decimal", (
            f"util::money_round must return std::decimal, got {fn['return_type']['name']}"
        )
        assert fn["return_typemod"] == "SingletonType", (
            f"util::money_round must return a single value, got typemod {fn['return_typemod']}"
        )
        for param in fn["params"]:
            assert param["kind"] == "PositionalParam", (
                f"util::money_round parameter {param['name']!r} must be positional, "
                f"got {param['kind']}"
            )
            assert param["typemod"] == "SingletonType", (
                f"util::money_round parameter {param['name']!r} must not be optional or set-of, "
                f"got {param['typemod']}"
            )
    assert one["params"][0]["type"]["name"] == "std::decimal", (
        "util::money_round 'amount' must be std::decimal, got "
        + one["params"][0]["type"]["name"]
    )
    assert two["params"][1]["type"]["name"] == "std::int64", (
        "util::money_round 'places' must be std::int64, got " + two["params"][1]["type"]["name"]
    )


def test_total_of_is_variadic(client):
    funcs = _functions(client)
    fn = funcs.get(("util::total_of", ("amounts",)))
    assert fn is not None, (
        "util::total_of(variadic amounts: decimal) is missing. Found: " + str(sorted(funcs))
    )
    param = fn["params"][0]
    assert param["kind"] == "VariadicParam", (
        f"util::total_of parameter 'amounts' must be variadic, got {param['kind']}"
    )
    assert param["type"]["name"] == "array<std::decimal>", (
        "A variadic decimal parameter is introspected as array<std::decimal>, got "
        + param["type"]["name"]
    )
    assert fn["return_type"]["name"] == "std::decimal", (
        f"util::total_of must return std::decimal, got {fn['return_type']['name']}"
    )


def test_apply_discount_has_single_optional_parameter(client):
    funcs = _functions(client)
    fn = funcs.get(("util::apply_discount", ("amount", "pct", "floor_amount")))
    assert fn is not None, (
        "util::apply_discount(amount, pct, floor_amount) is missing. Found: "
        + str(sorted(funcs))
    )
    typemods = {p["name"]: p["typemod"] for p in fn["params"]}
    assert typemods["floor_amount"] == "OptionalType", (
        f"'floor_amount' must be declared optional, got {typemods['floor_amount']}"
    )
    assert typemods["amount"] == "SingletonType" and typemods["pct"] == "SingletonType", (
        f"'amount' and 'pct' must not be optional, got {typemods}"
    )
    for name, param in ((p["name"], p) for p in fn["params"]):
        assert param["type"]["name"] == "std::decimal", (
            f"util::apply_discount parameter {name!r} must be std::decimal, "
            f"got {param['type']['name']}"
        )


def test_gross_with_tax_has_named_only_parameter_with_default(client):
    funcs = _functions(client)
    fn = funcs.get(("util::gross_with_tax", ("net", "tax_pct")))
    assert fn is not None, (
        "util::gross_with_tax(net, named only tax_pct) is missing. Found: " + str(sorted(funcs))
    )
    kinds = {p["name"]: p["kind"] for p in fn["params"]}
    assert kinds["net"] == "PositionalParam", (
        f"'net' must be a positional parameter, got {kinds['net']}"
    )
    assert kinds["tax_pct"] == "NamedOnlyParam", (
        f"'tax_pct' must be declared 'named only', got {kinds['tax_pct']}"
    )
    default = {p["name"]: p["default"] for p in fn["params"]}["tax_pct"]
    assert default, "'tax_pct' must declare a default value so it can be omitted."


def test_installments_returns_a_set(client):
    funcs = _functions(client)
    fn = funcs.get(("util::installments", ("total", "count")))
    assert fn is not None, (
        "util::installments(total: decimal, count: int64) is missing. Found: "
        + str(sorted(funcs))
    )
    assert fn["return_typemod"] == "SetOfType", (
        "util::installments must be declared with a 'set of' return type, got typemod "
        + fn["return_typemod"]
    )
    assert fn["return_type"]["name"] == "std::decimal", (
        f"util::installments must return decimals, got {fn['return_type']['name']}"
    )
    types = {p["name"]: p["type"]["name"] for p in fn["params"]}
    assert types == {"total": "std::decimal", "count": "std::int64"}, (
        f"Unexpected util::installments parameter types: {types}"
    )


def test_function_volatilities(client):
    funcs = _functions(client)
    money_round = funcs.get(("util::money_round", ("amount",)))
    assert money_round is not None, "util::money_round(amount: decimal) is missing."
    assert money_round["volatility"] == "Immutable", (
        "util::money_round must be Immutable so it can be used in a constraint, got "
        + money_round["volatility"]
    )
    outstanding = funcs.get(("billing::customer_outstanding", ("customer_name",)))
    assert outstanding is not None, (
        "billing::customer_outstanding(customer_name: str) is missing. Found: "
        + str(sorted(funcs))
    )
    assert outstanding["volatility"] == "Stable", (
        "billing::customer_outstanding must have Stable volatility, got "
        + outstanding["volatility"]
    )
    assert outstanding["params"][0]["type"]["name"] == "std::str", (
        "billing::customer_outstanding 'customer_name' must be std::str, got "
        + outstanding["params"][0]["type"]["name"]
    )


# ---------------------------------------------------------------- util behaviour


def test_money_round_values(client):
    cases = [
        ("select util::money_round(<decimal>'2.345')", D("2.35")),
        ("select util::money_round(<decimal>'-2.345')", D("-2.35")),
        ("select util::money_round(<decimal>'2.5')", D("2.5")),
        ("select util::money_round(<decimal>'2.345', 1)", D("2.3")),
        ("select util::money_round(<decimal>'163.278', -1)", D("160")),
        ("select util::money_round(<decimal>'163.278', 0)", D("163")),
    ]
    for query, expected in cases:
        got = client.query_single(query)
        assert got == expected, f"{query} returned {got!r}, expected {expected!r}"


def test_empty_set_into_non_optional_parameter_yields_empty_set(client):
    rows = client.query("select util::money_round(<decimal>{})")
    assert list(rows) == [], (
        "util::money_round(<decimal>{}) must return an empty set because 'amount' is not "
        f"optional, got {list(rows)!r}"
    )


def test_calls_that_match_no_overload_are_rejected(client):
    with pytest.raises(gel.errors.EdgeDBError):
        client.query("select util::money_round(<decimal>'2.345', 1, 2)")
    with pytest.raises(gel.errors.EdgeDBError):
        client.query("select util::installments(<decimal>'10')")


def test_total_of_variadic_behaviour(client):
    cases = [
        ("select util::total_of()", D("0")),
        ("select util::total_of(<decimal>'1.005', <decimal>'2.5')", D("3.51")),
        ("select util::total_of(<decimal>'1.111')", D("1.11")),
        ("select util::total_of(<decimal>'-1.005', <decimal>'1')", D("-0.01")),
    ]
    for query, expected in cases:
        got = client.query_single(query)
        assert got == expected, f"{query} returned {got!r}, expected {expected!r}"


def test_apply_discount_optional_argument_behaviour(client):
    cases = [
        ("select util::apply_discount(<decimal>'100', <decimal>'10', <decimal>{})", D("90")),
        ("select util::apply_discount(<decimal>'100', <decimal>'10', <decimal>'95')", D("95")),
        ("select util::apply_discount(<decimal>'100', <decimal>'0', <decimal>{})", D("100")),
        ("select util::apply_discount(<decimal>'19.99', <decimal>'33', <decimal>{})", D("13.39")),
    ]
    for query, expected in cases:
        got = client.query_single(query)
        assert got == expected, f"{query} returned {got!r}, expected {expected!r}"


def test_gross_with_tax_named_only_behaviour(client):
    cases = [
        ("select util::gross_with_tax(<decimal>'100', tax_pct := <decimal>'8.25')", D("108.25")),
        ("select util::gross_with_tax(<decimal>'19.99', tax_pct := <decimal>'7.5')", D("21.49")),
        ("select util::gross_with_tax(<decimal>'100')", D("100")),
    ]
    for query, expected in cases:
        got = client.query_single(query)
        assert got == expected, f"{query} returned {got!r}, expected {expected!r}"
    with pytest.raises(gel.errors.EdgeDBError):
        client.query("select util::gross_with_tax(<decimal>'100', <decimal>'8.25')")


def test_installments_set_semantics(client):
    got = sorted(client.query("select util::installments(<decimal>'100.00', 3)"))
    assert got == [D("33.33"), D("33.33"), D("33.34")], (
        f"installments(100.00, 3) returned {got!r}"
    )
    assert sum(got) == D("100.00"), f"installments(100.00, 3) must sum to 100.00, got {sum(got)}"

    got = sorted(client.query("select util::installments(<decimal>'0.05', 3)"))
    assert got == [D("0.01"), D("0.02"), D("0.02")], f"installments(0.05, 3) returned {got!r}"
    assert sum(got) == D("0.05"), f"installments(0.05, 3) must sum to 0.05, got {sum(got)}"

    got = list(client.query("select util::installments(<decimal>'10', 1)"))
    assert len(got) == 1 and got[0] == D("10"), f"installments(10, 1) returned {got!r}"

    got = list(client.query("select util::installments(<decimal>'1', 7)"))
    assert len(got) == 7, f"installments(1, 7) must return 7 values, got {got!r}"
    assert len([v for v in got if v == D("0.14")]) == 6, (
        f"installments(1, 7) must contain six values of 0.14, got {got!r}"
    )
    assert len([v for v in got if v == D("0.16")]) == 1, (
        f"installments(1, 7) must contain one value of 0.16, got {got!r}"
    )
    assert sum(got) == D("1"), f"installments(1, 7) must sum to 1, got {sum(got)}"

    for count in (0, -2):
        rows = list(client.query(f"select util::installments(<decimal>'10', {count})"))
        assert rows == [], f"installments(10, {count}) must return an empty set, got {rows!r}"


# ---------------------------------------------------------------- domain behaviour


def test_invoice_computeds_over_seeded_data(seeded):
    rows = seeded.query("select billing::Invoice { code, subtotal, total_due } order by .code")
    got = {r.code: (r.subtotal, r.total_due) for r in rows}
    expected = {
        "INV-1": (D("44.98"), D("40.48")),
        "INV-2": (D("30.00"), D("100.00")),
        "INV-3": (D("7.77"), D("7.77")),
        "INV-4": (D("0.00"), D("0.00")),
    }
    assert set(got) == set(expected), f"Unexpected invoice codes: {sorted(got)}"
    for code, (subtotal, total_due) in expected.items():
        assert got[code][0] == subtotal, (
            f"{code}: subtotal is {got[code][0]!r}, expected {subtotal!r}"
        )
        assert got[code][1] == total_due, (
            f"{code}: total_due is {got[code][1]!r}, expected {total_due!r}"
        )


def test_invoice_lines_and_line_totals(seeded):
    row = seeded.query_single(
        "select billing::Invoice { code, line_totals := .lines.line_total } "
        "filter .code = 'INV-1'"
    )
    got = sorted(row.line_totals)
    assert got == [D("5.00"), D("39.98")], f"INV-1 line totals are {got!r}"
    empty = seeded.query_single(
        "select billing::Invoice { code, line_totals := .lines.line_total } "
        "filter .code = 'INV-4'"
    )
    assert list(empty.line_totals) == [], (
        f"INV-4 has no line items, so it must expose no line totals, got {list(empty.line_totals)!r}"
    )


def test_computed_pointers_reference_the_util_module(client):
    rows = _jq(
        client,
        """
        select schema::ObjectType {
            name,
            pointers: { name, expr, cardinality }
        }
        filter .name in {'billing::LineItem', 'billing::Invoice'}
        """,
    )
    pointers = {}
    for row in rows:
        for pointer in row["pointers"]:
            pointers[(row["name"], pointer["name"])] = pointer

    for type_name, pointer_name in (
        ("billing::LineItem", "line_total"),
        ("billing::Invoice", "subtotal"),
        ("billing::Invoice", "total_due"),
    ):
        pointer = pointers.get((type_name, pointer_name))
        assert pointer is not None, f"{type_name} has no pointer named {pointer_name!r}."
        expr = pointer["expr"] or ""
        assert "util::" in expr, (
            f"{type_name}.{pointer_name} must be computed by calling the util module functions; "
            f"its expression is {expr!r}"
        )
        assert pointer["cardinality"] == "One", (
            f"{type_name}.{pointer_name} must be single, got cardinality {pointer['cardinality']}"
        )

    lines = pointers.get(("billing::Invoice", "lines"))
    assert lines is not None, "billing::Invoice has no 'lines' pointer."
    assert lines["cardinality"] == "Many", (
        f"billing::Invoice.lines must be a multi pointer, got {lines['cardinality']}"
    )


def test_unit_price_precision_constraint(seeded):
    bad = (
        "insert billing::LineItem { "
        "invoice := assert_single((select billing::Invoice filter .code = 'INV-4')), "
        "description := 'too precise', qty := 1, unit_price := <decimal>'1.234' }"
    )
    with pytest.raises(gel.errors.ConstraintViolationError):
        seeded.execute(bad)

    good = (
        "insert billing::LineItem { "
        "invoice := assert_single((select billing::Invoice filter .code = 'INV-4')), "
        "description := 'fine', qty := 1, unit_price := <decimal>'1.23' }"
    )
    seeded.execute(good)
    count = seeded.query_single(
        "select count(billing::LineItem filter .description = 'fine')"
    )
    assert count == 1, (
        f"A line item with a 2-decimal unit_price must be accepted, found {count} rows."
    )
    rejected = seeded.query_single(
        "select count(billing::LineItem filter .description = 'too precise')"
    )
    assert rejected == 0, (
        f"The rejected line item must not have been stored, found {rejected} rows."
    )
    seeded.execute("delete billing::LineItem filter .description = 'fine'")


def test_customer_outstanding_function(seeded):
    cases = [("Acme", D("140.48")), ("Globex", D("0")), ("Nobody", D("0"))]
    for name, expected in cases:
        got = seeded.query_single(
            "select billing::customer_outstanding(<str>$name)", name=name
        )
        assert got == expected, (
            f"billing::customer_outstanding({name!r}) returned {got!r}, expected {expected!r}"
        )


# ---------------------------------------------------------------- aliases


def test_report_aliases_are_declared_as_aliases(client):
    names = _jq(client, "select (schema::Alias.name) filter schema::Alias.name like 'reports::%'")
    for expected in (
        "reports::UnpaidInvoice",
        "reports::CustomerBalance",
        "reports::InvoicePlan",
    ):
        assert expected in names, (
            f"{expected} is not declared as a schema alias. Found: {sorted(names)}"
        )


def test_unpaid_invoice_alias_contents(seeded):
    rows = seeded.query("select reports::UnpaidInvoice { code, subtotal, total_due } order by .code")
    codes = [r.code for r in rows]
    assert codes == ["INV-1", "INV-2", "INV-4"], (
        f"reports::UnpaidInvoice must contain exactly the unpaid invoices, got {codes}"
    )
    by_code = {r.code: r for r in rows}
    assert by_code["INV-1"].subtotal == D("44.98"), (
        f"INV-1 subtotal via the alias is {by_code['INV-1'].subtotal!r}, expected 44.98"
    )
    assert by_code["INV-2"].total_due == D("100.00"), (
        f"INV-2 total_due via the alias is {by_code['INV-2'].total_due!r}, expected 100.00"
    )


def test_customer_balance_alias_contents(seeded):
    rows = seeded.query("select reports::CustomerBalance { name, outstanding } order by .name")
    got = {r.name: r.outstanding for r in rows}
    assert set(got) == {"Acme", "Globex"}, f"Unexpected customers in the alias: {sorted(got)}"
    assert got["Acme"] == D("140.48"), f"Acme outstanding is {got['Acme']!r}, expected 140.48"
    assert got["Globex"] == D("0.00"), f"Globex outstanding is {got['Globex']!r}, expected 0.00"


def test_invoice_plan_alias_contents(seeded):
    row = seeded.query_single("select reports::InvoicePlan { code, plan } filter .code = 'INV-1'")
    plan = sorted(row.plan)
    assert plan == [D("13.49"), D("13.49"), D("13.50")], (
        f"INV-1 installment plan is {plan!r}, expected 13.49 + 13.49 + 13.50"
    )
    assert sum(plan) == D("40.48"), (
        f"INV-1 installment plan must sum to its total_due 40.48, got {sum(plan)}"
    )
    single = seeded.query_single(
        "select reports::InvoicePlan { code, plan } filter .code = 'INV-4'"
    )
    plan4 = list(single.plan)
    assert len(plan4) == 1 and plan4[0] == D("0"), (
        f"INV-4 has installment_count 1 and total_due 0, so its plan must be a single 0, got {plan4!r}"
    )


def test_aliases_and_function_react_to_data_changes(seeded):
    seeded.execute("update billing::Invoice filter .code = 'INV-2' set { paid := true }")

    rows = seeded.query("select reports::CustomerBalance { name, outstanding } order by .name")
    got = {r.name: r.outstanding for r in rows}
    assert got["Acme"] == D("40.48"), (
        "After INV-2 is paid, Acme outstanding must be 40.48, got " + repr(got["Acme"])
    )

    codes = [r.code for r in seeded.query("select reports::UnpaidInvoice { code } order by .code")]
    assert codes == ["INV-1", "INV-4"], (
        f"After INV-2 is paid, reports::UnpaidInvoice must yield INV-1 and INV-4, got {codes}"
    )

    outstanding = seeded.query_single("select billing::customer_outstanding('Acme')")
    assert outstanding == D("40.48"), (
        f"After INV-2 is paid, billing::customer_outstanding('Acme') must be 40.48, got {outstanding!r}"
    )
