"""Final-state verification for the gel_graphql_http_endpoint_integration_py task.

Every check drives the real, running Gel 7 server: the native binary protocol
(via the `gel` Python client), the `gel` CLI, raw HTTP against the built-in
GraphQL endpoint, and the executor's own `gateway` module / `report.py` command.
"""

import glob
import importlib
import json
import os
import re
import shutil
import subprocess
import sys

import pytest
import requests

PROJECT_DIR = "/home/user/gateway"
DBSCHEMA_DIR = os.path.join(PROJECT_DIR, "dbschema")
MIGRATIONS_DIR = os.path.join(DBSCHEMA_DIR, "migrations")
REPORT_PATH = os.path.join(PROJECT_DIR, "out", "report.json")

HOST = "127.0.0.1"
PORT = 5656
GRAPHQL_URL = f"http://{HOST}:{PORT}/branch/main/graphql"

SEED_ROWS = [
    ("auth-api", 1, True, "atlas", "eu-west"),
    ("billing-api", 2, True, "borealis", "us-east"),
    ("cache-proxy", 3, False, "atlas", "eu-west"),
    ("dns-relay", 2, True, "cygnus", "eu-west"),
    ("edge-router", 1, True, "borealis", "us-east"),
    ("feed-worker", 3, True, "atlas", "eu-west"),
    ("graph-sync", 2, False, "cygnus", "eu-west"),
    ("heartbeat", 1, True, "borealis", "us-east"),
    ("index-builder", 3, True, "cygnus", "eu-west"),
]
SEED_NAMES = [row[0] for row in SEED_ROWS]
ACTIVE_NAMES = [row[0] for row in SEED_ROWS if row[2]]

EXPECTED_TEAMS = [
    {
        "team": "atlas",
        "region": "eu-west",
        "service_count": 3,
        "active_service_count": 2,
        "services": ["auth-api", "cache-proxy", "feed-worker"],
    },
    {
        "team": "borealis",
        "region": "us-east",
        "service_count": 3,
        "active_service_count": 3,
        "services": ["billing-api", "edge-router", "heartbeat"],
    },
    {
        "team": "cygnus",
        "region": "eu-west",
        "service_count": 3,
        "active_service_count": 2,
        "services": ["dns-relay", "graph-sync", "index-builder"],
    },
]

EXPECTED_PAGES = {
    "page_1": ["auth-api", "billing-api"],
    "page_2": ["cache-proxy", "dns-relay"],
    "page_3": ["edge-router", "feed-worker"],
}

SERVICE_KEYS = {"name", "tier", "active", "team", "region"}

LISTING_QUERY = """
select Service {
    name,
    tier,
    active,
    team := .owner.name,
    region := .owner.region,
}
order by .name
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def gel_server():
    """Start the local Gel server (idempotent) and block until it answers queries.

    Every test that touches the database, the CLI or HTTP MUST depend on this
    fixture so it can never race the server startup.
    """
    starter = shutil.which("start-gel-server.sh")
    assert starter is not None, "start-gel-server.sh was not found in PATH."
    proc = subprocess.run([starter], capture_output=True, text=True, timeout=600)
    print("--- start-gel-server.sh stdout ---")
    print(proc.stdout)
    print("--- start-gel-server.sh stderr ---")
    print(proc.stderr)
    assert proc.returncode == 0, (
        "start-gel-server.sh failed to start the local Gel server.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    return True


@pytest.fixture(scope="session")
def client(gel_server):
    """A native binary-protocol client used as the independent oracle."""
    import gel

    conn = gel.create_client()
    conn.ensure_connected()
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def gw(gel_server):
    """Import the executor's gateway module from the project root."""
    assert os.path.isfile(
        os.path.join(PROJECT_DIR, "gateway.py")
    ), f"{PROJECT_DIR}/gateway.py does not exist."
    if PROJECT_DIR not in sys.path:
        sys.path.insert(0, PROJECT_DIR)
    module = importlib.import_module("gateway")
    return module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _binary_rows(client):
    rows = client.query(LISTING_QUERY)
    return [(r.name, r.tier, r.active, r.team, r.region) for r in rows]


def _names(records):
    return [record["name"] for record in records]


def _graphql(payload):
    return requests.post(
        GRAPHQL_URL,
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=60,
    )


def _has_argument(document, argument):
    return re.search(rf"\b{argument}\s*:", document) is not None


# ---------------------------------------------------------------------------
# 1. Migration is applied and in sync
# ---------------------------------------------------------------------------


def test_schema_file_exists():
    path = os.path.join(DBSCHEMA_DIR, "default.gel")
    assert os.path.isfile(path), f"Expected the schema file at {path}."


def test_migration_file_created():
    matches = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql")))
    assert matches, (
        f"No migration file matching {MIGRATIONS_DIR}/*.edgeql was found; the schema "
        "must be applied through a real migration."
    )


def test_migration_status_is_in_sync(gel_server):
    proc = subprocess.run(
        ["gel", "migration", "status"],
        capture_output=True,
        text=True,
        cwd=PROJECT_DIR,
        timeout=180,
    )
    assert proc.returncode == 0, (
        "`gel migration status` did not exit successfully.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    combined = (proc.stdout + proc.stderr).lower()
    assert "up to date" in combined, (
        "`gel migration status` does not report the database as up to date.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


# ---------------------------------------------------------------------------
# 2. Extension, types, computeds and alias exist
# ---------------------------------------------------------------------------


def test_graphql_extension_enabled(client):
    names = {row.name for row in client.query("select schema::Extension { name }")}
    assert "graphql" in names, (
        f"The 'graphql' extension is not enabled; enabled extensions: {sorted(names)}."
    )


def test_active_service_alias_exists(client):
    names = {row.name for row in client.query("select schema::Alias { name }")}
    assert "default::ActiveService" in names, (
        "The expression alias default::ActiveService was not found; "
        f"aliases present: {sorted(n for n in names if n.startswith('default::'))}."
    )


def test_team_type_shape(client):
    rows = client.query(
        """
        select schema::ObjectType {
            pointers: {
                name,
                required,
                is_computed := exists .expr,
                target_name := .target.name,
                constraint_names := (select .constraints.name),
            }
        }
        filter .name = 'default::Team'
        """
    )
    assert rows, "Object type default::Team was not found."
    pointers = {p.name: p for p in rows[0].pointers}
    for expected in ("name", "region", "services", "service_count"):
        assert expected in pointers, (
            f"default::Team is missing the pointer '{expected}'; "
            f"found {sorted(pointers)}."
        )
    assert pointers["name"].required, "default::Team.name must be a required property."
    assert pointers["name"].target_name == "std::str", (
        f"default::Team.name must be of type str, got {pointers['name'].target_name}."
    )
    assert "std::exclusive" in set(pointers["name"].constraint_names), (
        "default::Team.name must carry an exclusive constraint; found "
        f"{sorted(set(pointers['name'].constraint_names))}."
    )
    assert pointers["region"].required, "default::Team.region must be required."
    assert pointers["region"].target_name == "std::str", (
        f"default::Team.region must be of type str, got {pointers['region'].target_name}."
    )
    assert pointers["services"].is_computed, (
        "default::Team.services must be a computed pointer."
    )
    assert pointers["services"].target_name == "default::Service", (
        "default::Team.services must target default::Service, got "
        f"{pointers['services'].target_name}."
    )
    assert pointers["service_count"].is_computed, (
        "default::Team.service_count must be a computed property."
    )
    assert pointers["service_count"].target_name == "std::int64", (
        "default::Team.service_count must be of type int64, got "
        f"{pointers['service_count'].target_name}."
    )


def test_service_type_shape(client):
    rows = client.query(
        """
        select schema::ObjectType {
            pointers: {
                name,
                required,
                is_computed := exists .expr,
                target_name := .target.name,
                constraint_names := (select .constraints.name),
            },
            link_names := (select .pointers[is schema::Link].name),
        }
        filter .name = 'default::Service'
        """
    )
    assert rows, "Object type default::Service was not found."
    pointers = {p.name: p for p in rows[0].pointers}
    for expected in ("name", "tier", "active", "owner", "team_name"):
        assert expected in pointers, (
            f"default::Service is missing the pointer '{expected}'; "
            f"found {sorted(pointers)}."
        )
    assert pointers["name"].required, "default::Service.name must be required."
    assert "std::exclusive" in set(pointers["name"].constraint_names), (
        "default::Service.name must carry an exclusive constraint; found "
        f"{sorted(set(pointers['name'].constraint_names))}."
    )
    assert pointers["tier"].required, "default::Service.tier must be required."
    assert pointers["tier"].target_name == "std::int64", (
        f"default::Service.tier must be of type int64, got {pointers['tier'].target_name}."
    )
    assert pointers["active"].required, "default::Service.active must be required."
    assert pointers["active"].target_name == "std::bool", (
        f"default::Service.active must be of type bool, got {pointers['active'].target_name}."
    )
    assert pointers["owner"].required, "default::Service.owner must be a required link."
    assert pointers["owner"].target_name == "default::Team", (
        f"default::Service.owner must target default::Team, got {pointers['owner'].target_name}."
    )
    assert "owner" in set(rows[0].link_names), (
        "default::Service.owner must be a link, not a property; links found: "
        f"{sorted(set(rows[0].link_names))}."
    )
    assert pointers["team_name"].is_computed, (
        "default::Service.team_name must be a computed property."
    )
    assert pointers["team_name"].target_name == "std::str", (
        "default::Service.team_name must be of type str, got "
        f"{pointers['team_name'].target_name}."
    )


# ---------------------------------------------------------------------------
# 3. Seed data loaded exactly
# ---------------------------------------------------------------------------


def test_seed_counts(client):
    assert client.query_single("select count(Team)") == 3, (
        "The database must contain exactly 3 Team objects."
    )
    assert client.query_single("select count(Service)") == 9, (
        "The database must contain exactly 9 Service objects."
    )


def test_seed_rows_exact(client):
    rows = _binary_rows(client)
    assert rows == SEED_ROWS, (
        "The seeded services do not match data/seed.json.\n"
        f"expected: {SEED_ROWS}\nactual:   {rows}"
    )


# ---------------------------------------------------------------------------
# 4. Raw HTTP endpoint works (no gateway involved)
# ---------------------------------------------------------------------------


def test_raw_http_alias_query(gel_server):
    response = _graphql(
        {"query": "{ ActiveService(order: {name: {dir: ASC}}) { name team_name } }"}
    )
    assert response.status_code == 200, (
        f"POST {GRAPHQL_URL} returned {response.status_code}: {response.text}"
    )
    body = response.json()
    assert "errors" not in body, (
        f"The GraphQL endpoint returned errors for the ActiveService query: {body}"
    )
    rows = body["data"]["ActiveService"]
    assert [row["name"] for row in rows] == ACTIVE_NAMES, (
        f"ActiveService must expose exactly {ACTIVE_NAMES}, got {rows}."
    )
    expected_teams = [row[3] for row in SEED_ROWS if row[2]]
    assert [row["team_name"] for row in rows] == expected_teams, (
        f"ActiveService.team_name must be {expected_teams}, got {rows}."
    )


def test_raw_http_team_computeds(gel_server):
    response = _graphql(
        {
            "query": (
                "{ Team(order: {name: {dir: ASC}}) { name service_count "
                "services(order: {name: {dir: ASC}}) { name } } }"
            )
        }
    )
    assert response.status_code == 200, (
        f"POST {GRAPHQL_URL} returned {response.status_code}: {response.text}"
    )
    body = response.json()
    assert "errors" not in body, f"The Team GraphQL query returned errors: {body}"
    rows = body["data"]["Team"]
    assert [row["name"] for row in rows] == ["atlas", "borealis", "cygnus"], (
        f"Expected three teams atlas/borealis/cygnus, got {rows}."
    )
    for row, expected in zip(rows, EXPECTED_TEAMS):
        assert row["service_count"] == 3, (
            f"Team {row['name']} must report service_count 3, got {row['service_count']}."
        )
        assert [s["name"] for s in row["services"]] == expected["services"], (
            f"Team {row['name']} services must be {expected['services']}, got {row['services']}."
        )


# ---------------------------------------------------------------------------
# 5-9. gateway.endpoint_url / gateway.execute
# ---------------------------------------------------------------------------


def test_endpoint_url(gw):
    assert gw.endpoint_url() == GRAPHQL_URL, (
        f"gateway.endpoint_url() must return {GRAPHQL_URL!r}, got {gw.endpoint_url()!r}."
    )
    other = gw.endpoint_url("other")
    assert other.endswith("/branch/other/graphql"), (
        f"gateway.endpoint_url('other') must end with '/branch/other/graphql', got {other!r}."
    )


def test_execute_happy_path(gw):
    result = gw.execute("{ Service(order: {name: {dir: ASC}}, first: 2) { name } }")
    assert set(result) == {"data", "errors", "status"}, (
        f"execute() must return exactly the keys data/errors/status, got {sorted(result)}."
    )
    assert result["status"] == 200, f"Expected HTTP 200, got {result['status']}."
    assert result["errors"] == [], f"Expected no errors, got {result['errors']}."
    assert [row["name"] for row in result["data"]["Service"]] == [
        "auth-api",
        "billing-api",
    ], f"Unexpected data payload: {result['data']}."


def test_execute_with_variables(gw):
    result = gw.execute(
        "query Q($t: Int64!) { Service(filter: {tier: {eq: $t}}, "
        "order: {name: {dir: ASC}}) { name } }",
        {"t": 1},
    )
    assert result["status"] == 200, f"Expected HTTP 200, got {result}."
    assert result["errors"] == [], f"Expected no errors, got {result['errors']}."
    assert [row["name"] for row in result["data"]["Service"]] == [
        "auth-api",
        "edge-router",
        "heartbeat",
    ], f"Unexpected data payload: {result['data']}."
    recorded = gw.last_request()
    assert recorded["variables"] == {"t": 1}, (
        f"last_request()['variables'] must be {{'t': 1}}, got {recorded['variables']!r}."
    )


def test_execute_graphql_error_payload(gw):
    result = gw.execute("{ Service { no_such_field } }")
    assert result["status"] == 200, (
        f"A GraphQL-level error must still surface as HTTP 200, got {result['status']}."
    )
    assert result["data"] is None, (
        f"data must be None when the response carries no data object, got {result['data']!r}."
    )
    assert isinstance(result["errors"], list) and result["errors"], (
        f"errors must be a non-empty list, got {result['errors']!r}."
    )
    assert isinstance(result["errors"][0], str), (
        f"errors entries must be strings, got {type(result['errors'][0])}."
    )
    assert "no_such_field" in result["errors"][0], (
        f"The error message should mention the bad field, got {result['errors'][0]!r}."
    )


def test_execute_non_json_transport_error(gw):
    result = gw.execute("{ Service { name } }", branch="no_such_branch")
    assert result["status"] == 404, (
        f"Querying an unknown branch must yield HTTP 404, got {result['status']}."
    )
    assert result["data"] is None, f"data must be None, got {result['data']!r}."
    assert isinstance(result["errors"], list) and len(result["errors"]) == 1, (
        f"errors must hold exactly one entry for a non-JSON body, got {result['errors']!r}."
    )
    assert (
        isinstance(result["errors"][0], str) and result["errors"][0].strip()
    ), f"The single error entry must be a non-empty string, got {result['errors']!r}."


# ---------------------------------------------------------------------------
# 10-13. list_services
# ---------------------------------------------------------------------------


def test_list_services_unfiltered(gw):
    records = gw.list_services()
    assert len(records) == 9, f"Expected 9 services, got {len(records)}."
    assert _names(records) == SEED_NAMES, (
        f"Services must be ordered by name ascending: expected {SEED_NAMES}, got {_names(records)}."
    )
    for record in records:
        assert set(record) == SERVICE_KEYS, (
            f"Each record must have exactly the keys {sorted(SERVICE_KEYS)}, got {sorted(record)}."
        )
        assert isinstance(record["tier"], int) and not isinstance(record["tier"], bool), (
            f"tier must be an int, got {record['tier']!r} for {record['name']}."
        )
        assert isinstance(record["active"], bool), (
            f"active must be a bool, got {record['active']!r} for {record['name']}."
        )
    by_name = {record["name"]: record for record in records}
    assert by_name["auth-api"]["team"] == "atlas", (
        f"auth-api must be owned by atlas, got {by_name['auth-api']}."
    )
    assert by_name["auth-api"]["region"] == "eu-west", (
        f"auth-api must report region eu-west, got {by_name['auth-api']}."
    )


def test_list_services_region_filter(gw):
    got = _names(gw.list_services(region="eu-west"))
    expected = ["auth-api", "cache-proxy", "dns-relay", "feed-worker", "graph-sync", "index-builder"]
    assert got == expected, f"region='eu-west' must yield {expected}, got {got}."


def test_list_services_min_tier_filter(gw):
    got = _names(gw.list_services(min_tier=2))
    expected = [
        "billing-api",
        "cache-proxy",
        "dns-relay",
        "feed-worker",
        "graph-sync",
        "index-builder",
    ]
    assert got == expected, f"min_tier=2 must yield {expected}, got {got}."


def test_list_services_active_only(gw):
    got = _names(gw.list_services(active_only=True))
    assert got == ACTIVE_NAMES, f"active_only=True must yield {ACTIVE_NAMES}, got {got}."
    assert "cache-proxy" not in got and "graph-sync" not in got, (
        f"Inactive services must be excluded, got {got}."
    )


def test_list_services_combined_filters(gw):
    got = _names(gw.list_services(region="eu-west", min_tier=3, active_only=True))
    assert got == ["feed-worker", "index-builder"], (
        f"Combined filters must yield ['feed-worker', 'index-builder'], got {got}."
    )


def test_list_services_empty_result(gw):
    got = gw.list_services(region="antarctica")
    assert got == [], f"An unmatched region filter must yield an empty list, got {got}."


def test_list_services_pagination_boundaries(gw):
    assert _names(gw.list_services(limit=2)) == ["auth-api", "billing-api"], (
        "limit=2 must return the first two services by name."
    )
    assert _names(gw.list_services(limit=2, offset=2)) == ["cache-proxy", "dns-relay"], (
        "limit=2, offset=2 must return the third and fourth services by name."
    )
    assert _names(gw.list_services(offset=8, limit=2)) == ["index-builder"], (
        "offset=8, limit=2 must return only the final service."
    )
    assert gw.list_services(offset=9) == [], (
        "offset=9 is past the end of the result set and must return an empty list."
    )
    assert _names(gw.list_services(offset=8)) == ["index-builder"], (
        "offset=8 without a limit must return only the final service."
    )
    assert _names(gw.list_services(active_only=True, limit=3, offset=6)) == [
        "index-builder"
    ], "active_only=True, limit=3, offset=6 must return only ['index-builder']."


@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": 0},
        {"limit": -1},
        {"limit": 1.5},
        {"offset": -1},
    ],
)
def test_list_services_invalid_window_raises(gw, kwargs):
    before = gw.last_request()
    with pytest.raises(ValueError):
        gw.list_services(**kwargs)
    assert gw.last_request() == before, (
        f"list_services({kwargs}) must reject the arguments without sending a request."
    )


def test_list_services_records_server_side_request(gw):
    gw.list_services()
    recorded = gw.last_request()
    assert set(recorded) == {"url", "query", "variables", "operation_name"}, (
        "last_request() must have exactly the keys url/query/variables/operation_name, "
        f"got {sorted(recorded)}."
    )
    assert recorded["url"] == gw.endpoint_url(), (
        f"last_request()['url'] must be {gw.endpoint_url()!r}, got {recorded['url']!r}."
    )
    assert _has_argument(recorded["query"], "order"), (
        f"The recorded document must order server-side; got: {recorded['query']}"
    )

    gw.list_services(region="eu-west", min_tier=2, active_only=True, limit=2, offset=1)
    recorded = gw.last_request()
    for argument in ("filter", "order", "first", "after"):
        assert _has_argument(recorded["query"], argument), (
            f"The recorded document must carry a '{argument}' argument so the database "
            f"does the work; got: {recorded['query']}"
        )


# ---------------------------------------------------------------------------
# 14. Not a hardcoded list
# ---------------------------------------------------------------------------


def test_list_services_reflects_live_data(gw, client):
    client.execute(
        """
        insert Service {
            name := 'probe-svc',
            tier := 4,
            active := true,
            owner := (select Team filter .name = 'atlas'),
        }
        """
    )
    try:
        records = gw.list_services()
        assert len(records) == 10, (
            f"After inserting probe-svc the gateway must see 10 services, got {len(records)}."
        )
        by_name = {record["name"]: record for record in records}
        assert "probe-svc" in by_name, (
            f"probe-svc must appear in list_services(), got {sorted(by_name)}."
        )
        assert by_name["probe-svc"]["team"] == "atlas", (
            f"probe-svc must be owned by atlas, got {by_name['probe-svc']}."
        )
        assert by_name["probe-svc"]["region"] == "eu-west", (
            f"probe-svc must report region eu-west, got {by_name['probe-svc']}."
        )
        assert _names(gw.list_services(min_tier=4)) == ["probe-svc"], (
            "min_tier=4 must match only the freshly inserted probe-svc."
        )
    finally:
        client.execute("delete Service filter .name = 'probe-svc'")
    assert _names(gw.list_services()) == SEED_NAMES, (
        "After deleting probe-svc the gateway must be back to the 9 seeded services."
    )


# ---------------------------------------------------------------------------
# 15-16. fetch_teams / verify_parity
# ---------------------------------------------------------------------------


def test_fetch_teams(gw):
    teams = gw.fetch_teams()
    assert len(teams) == 3, f"fetch_teams() must return 3 teams, got {len(teams)}."
    for team in teams:
        assert set(team) == {
            "team",
            "region",
            "service_count",
            "active_service_count",
            "services",
        }, f"Unexpected key set in a team record: {sorted(team)}."
    assert teams == EXPECTED_TEAMS, (
        f"fetch_teams() must return {EXPECTED_TEAMS}, got {teams}."
    )


def test_verify_parity(gw):
    parity = gw.verify_parity()
    assert set(parity) == {"http_count", "binary_count", "match", "differences"}, (
        f"verify_parity() must return exactly those four keys, got {sorted(parity)}."
    )
    assert parity["http_count"] == 9, (
        f"http_count must be 9, got {parity['http_count']}."
    )
    assert parity["binary_count"] == 9, (
        f"binary_count must be 9, got {parity['binary_count']}."
    )
    assert parity["match"] is True, (
        f"The two protocols must agree; differences reported: {parity['differences']}."
    )
    assert parity["differences"] == [], (
        f"differences must be empty when match is true, got {parity['differences']}."
    )


# ---------------------------------------------------------------------------
# 17-20. Mutations over HTTP
# ---------------------------------------------------------------------------


def test_create_and_delete_service(gw, client):
    created = gw.create_service("telemetry-hub", 2, True, "borealis")
    try:
        assert created == {
            "name": "telemetry-hub",
            "tier": 2,
            "active": True,
            "team": "borealis",
            "region": "us-east",
        }, f"create_service returned an unexpected record: {created}."
        recorded = gw.last_request()
        assert "telemetry-hub" in json.dumps(recorded["variables"]), (
            "The inserted values must travel as GraphQL variables; recorded variables: "
            f"{recorded['variables']!r}."
        )
        assert "telemetry-hub" not in recorded["query"], (
            "The GraphQL document must not embed the literal service name; got: "
            f"{recorded['query']}"
        )
        rows = _binary_rows(client)
        assert len(rows) == 10, (
            f"The binary protocol must see 10 services after the insert, got {len(rows)}."
        )
        assert (
            "telemetry-hub",
            2,
            True,
            "borealis",
            "us-east",
        ) in rows, f"telemetry-hub was not persisted correctly: {rows}."
    except Exception:
        client.execute("delete Service filter .name = 'telemetry-hub'")
        raise

    deleted = gw.delete_service("telemetry-hub")
    assert deleted == created, (
        f"delete_service must return the deleted record {created}, got {deleted}."
    )
    rows = _binary_rows(client)
    assert rows == SEED_ROWS, (
        f"After deletion the database must be back to the seeded 9 services, got {rows}."
    )


def test_create_service_failure_is_clean(gw, client):
    with pytest.raises(gw.GatewayError) as excinfo:
        gw.create_service("ghost-svc", 1, True, "no-such-team")
    errors = excinfo.value.errors
    assert isinstance(errors, list) and errors, (
        f"GatewayError.errors must be a non-empty list, got {errors!r}."
    )
    assert all(isinstance(item, str) for item in errors), (
        f"GatewayError.errors must contain only strings, got {errors!r}."
    )
    rows = _binary_rows(client)
    assert len(rows) == 9, (
        f"A failed insert must leave exactly 9 services, got {len(rows)}."
    )
    assert all(row[0] != "ghost-svc" for row in rows), (
        f"ghost-svc must not have been persisted, got {rows}."
    )


def test_retire_service(gw, client):
    try:
        updated = gw.retire_service("heartbeat")
        assert updated == {
            "name": "heartbeat",
            "tier": 1,
            "active": False,
            "team": "borealis",
            "region": "us-east",
        }, f"retire_service returned an unexpected record: {updated}."
        remaining = _names(gw.list_services(active_only=True))
        assert len(remaining) == 6, (
            f"After retiring heartbeat, 6 services must remain active, got {remaining}."
        )
        assert "heartbeat" not in remaining, (
            f"heartbeat must no longer be active, got {remaining}."
        )
    finally:
        client.execute("update Service filter .name = 'heartbeat' set { active := true }")
    assert _names(gw.list_services(active_only=True)) == ACTIVE_NAMES, (
        "Restoring heartbeat must bring the active listing back to 7 services."
    )


def test_missing_service_errors(gw, client):
    with pytest.raises(gw.ServiceNotFound):
        gw.retire_service("nope-svc")
    with pytest.raises(gw.ServiceNotFound):
        gw.delete_service("nope-svc")
    assert issubclass(gw.ServiceNotFound, gw.GatewayError), (
        "ServiceNotFound must be a subclass of GatewayError."
    )
    assert client.query_single("select count(Service)") == 9, (
        "Failed lookups must not change the number of services."
    )


# ---------------------------------------------------------------------------
# 21-22. report.py / build_report and final state
# ---------------------------------------------------------------------------


def test_report_command_regenerates_report(gw):
    if os.path.exists(REPORT_PATH):
        os.remove(REPORT_PATH)
    proc = subprocess.run(
        ["python3", "report.py"],
        capture_output=True,
        text=True,
        cwd=PROJECT_DIR,
        timeout=300,
    )
    assert proc.returncode == 0, (
        "`python3 report.py` must exit 0.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert os.path.isfile(REPORT_PATH), f"{REPORT_PATH} was not created by report.py."
    with open(REPORT_PATH, encoding="utf-8") as handle:
        report = json.load(handle)

    assert set(report) == {"endpoint", "teams", "pages", "parity"}, (
        f"The report must have exactly the keys endpoint/teams/pages/parity, got {sorted(report)}."
    )
    assert report["endpoint"] == GRAPHQL_URL, (
        f"report['endpoint'] must be {GRAPHQL_URL!r}, got {report['endpoint']!r}."
    )
    assert report["teams"] == EXPECTED_TEAMS, (
        f"report['teams'] must be {EXPECTED_TEAMS}, got {report['teams']}."
    )
    assert set(report["pages"]) == {"page_1", "page_2", "page_3"}, (
        f"report['pages'] must have exactly page_1/page_2/page_3, got {sorted(report['pages'])}."
    )
    assert report["pages"] == EXPECTED_PAGES, (
        f"report['pages'] must be {EXPECTED_PAGES}, got {report['pages']}."
    )
    assert report["parity"] == {
        "http_count": 9,
        "binary_count": 9,
        "match": True,
        "differences": [],
    }, f"report['parity'] is wrong: {report['parity']}."

    in_process = gw.build_report()
    assert in_process == report, (
        "gateway.build_report() must return the same object report.py writes.\n"
        f"in-process: {in_process}\nfile: {report}"
    )


def test_no_schema_drift_at_end(client):
    assert client.query_single("select count(Team)") == 3, (
        "The run must end with exactly 3 Team objects."
    )
    assert client.query_single("select count(Service)") == 9, (
        "The run must end with exactly 9 Service objects."
    )
