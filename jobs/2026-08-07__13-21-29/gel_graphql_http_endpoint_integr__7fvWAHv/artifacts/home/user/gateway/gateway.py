"""Gateway module for Gel GraphQL-over-HTTP with dual-protocol verification."""

import json
import urllib.request
import urllib.error

import gel


class GatewayError(Exception):
    """Exception carrying GraphQL-level errors."""

    def __init__(self, errors=None):
        super().__init__(str(errors))
        self.errors = errors if errors is not None else []


class ServiceNotFound(GatewayError):
    """Raised when a service cannot be found."""


_last_request = None


def endpoint_url(branch="main"):
    """Return the GraphQL URL for the given branch."""
    return f"http://127.0.0.1:5656/branch/{branch}/graphql"


def execute(query, variables=None, operation_name=None, branch="main"):
    """Send a GraphQL request and return a dict with data, errors, status."""
    global _last_request

    url = endpoint_url(branch)
    payload = {"query": query}
    if variables is not None:
        payload["variables"] = variables
    if operation_name is not None:
        payload["operationName"] = operation_name

    # Record the request
    _last_request = {
        "url": url,
        "query": query,
        "variables": variables if variables is not None else {},
        "operation_name": operation_name,
    }

    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data_bytes,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req) as resp:
            status = resp.status
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        status = e.code
        body = e.read().decode("utf-8")

    # Try to parse JSON
    try:
        result = json.loads(body)
    except json.JSONDecodeError:
        return {
            "data": None,
            "errors": [body.strip()],
            "status": status,
        }

    data = result.get("data")
    raw_errors = result.get("errors", [])
    errors = [e.get("message", str(e)) for e in raw_errors] if raw_errors else []

    return {
        "data": data,
        "errors": errors,
        "status": status,
    }


def last_request():
    """Return the most recent request sent by this module."""
    return _last_request


def _graphql_value(val):
    """Convert a Python value to a GraphQL literal string."""
    if isinstance(val, bool):
        return "true" if val else "false"
    elif isinstance(val, str):
        escaped = val.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    elif isinstance(val, int):
        return str(val)
    elif isinstance(val, dict):
        pairs = []
        for k, v in val.items():
            pairs.append(f"{k}: {_graphql_value(v)}")
        return "{ " + ", ".join(pairs) + " }"
    elif isinstance(val, list):
        items = [_graphql_value(item) for item in val]
        return "[ " + ", ".join(items) + " ]"
    else:
        return str(val)


def list_services(region=None, min_tier=None, active_only=False, limit=None, offset=0):
    """List services with optional filtering, ordering, and pagination."""
    if limit is not None:
        if not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be None or a positive integer")
    if not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a non-negative integer")

    # Build filter conditions
    filter_parts = []

    if active_only:
        filter_parts.append({"active": {"eq": True}})

    if min_tier is not None:
        filter_parts.append({"tier": {"gte": min_tier}})

    if region is not None:
        filter_parts.append({"owner": {"region": {"eq": region}}})

    # Build the query arguments
    args_parts = ["order: {name: {dir: ASC}}"]

    if len(filter_parts) == 1:
        args_parts.append(f"filter: {_graphql_value(filter_parts[0])}")
    elif len(filter_parts) > 1:
        args_parts.append(f"filter: {{ and: {_graphql_value(filter_parts)} }}")

    if limit is not None:
        args_parts.append(f"first: {limit}")

    if offset > 0:
        args_parts.append(f'after: "{offset - 1}"')

    args_str = ", ".join(args_parts)

    query = (
        "query ListServices {"
        f"  Service({args_str}) {{"
        "    name"
        "    tier"
        "    active"
        "    owner { name region }"
        "  }"
        "}"
    )

    result = execute(query)

    if result["errors"]:
        raise GatewayError(result["errors"])

    data = result.get("data", {})
    services = data.get("Service", []) if data else []

    return [
        {
            "name": s["name"],
            "tier": s["tier"],
            "active": s["active"],
            "team": s["owner"]["name"],
            "region": s["owner"]["region"],
        }
        for s in services
    ]


def fetch_teams():
    """Fetch all teams with their services and counts."""
    query = (
        "query FetchTeams {"
        "  Team(order: {name: {dir: ASC}}) {"
        "    name"
        "    region"
        "    service_count"
        "    services(order: {name: {dir: ASC}}) {"
        "      name"
        "      active"
        "    }"
        "  }"
        "}"
    )

    result = execute(query)

    if result["errors"]:
        raise GatewayError(result["errors"])

    data = result.get("data", {})
    teams = data.get("Team", []) if data else []

    return [
        {
            "team": t["name"],
            "region": t["region"],
            "service_count": t["service_count"],
            "active_service_count": sum(1 for s in t["services"] if s["active"]),
            "services": [s["name"] for s in t["services"]],
        }
        for t in teams
    ]


def create_service(name, tier, active, team):
    """Create a new service linked to an existing team."""
    query = (
        "mutation CreateService($name: String!, $tier: Int64!, $active: Boolean!, $team: String!) {"
        "  insert_Service(data: {"
        "    name: $name,"
        "    tier: $tier,"
        "    active: $active,"
        "    owner: {filter: {name: {eq: $team}}}"
        "  }) {"
        "    name"
        "    tier"
        "    active"
        "    owner { name region }"
        "  }"
        "}"
    )

    variables = {
        "name": name,
        "tier": tier,
        "active": active,
        "team": team,
    }

    result = execute(query, variables=variables)

    if result["errors"]:
        raise GatewayError(result["errors"])

    data = result.get("data", {})
    inserted = data.get("insert_Service", []) if data else []

    if not inserted:
        raise GatewayError(["No service was created"])

    s = inserted[0]
    return {
        "name": s["name"],
        "tier": s["tier"],
        "active": s["active"],
        "team": s["owner"]["name"],
        "region": s["owner"]["region"],
    }


def retire_service(name):
    """Mark a service as inactive."""
    query = (
        "mutation RetireService($name: String!) {"
        "  update_Service("
        "    filter: {name: {eq: $name}},"
        "    data: {active: {set: false}}"
        "  ) {"
        "    name"
        "    tier"
        "    active"
        "    owner { name region }"
        "  }"
        "}"
    )

    variables = {"name": name}
    result = execute(query, variables=variables)

    if result["errors"]:
        raise GatewayError(result["errors"])

    data = result.get("data", {})
    updated = data.get("update_Service", []) if data else []

    if not updated:
        raise ServiceNotFound([f"Service '{name}' not found"])

    s = updated[0]
    return {
        "name": s["name"],
        "tier": s["tier"],
        "active": s["active"],
        "team": s["owner"]["name"],
        "region": s["owner"]["region"],
    }


def delete_service(name):
    """Delete a service by name."""
    query = (
        "mutation DeleteService($name: String!) {"
        "  delete_Service(filter: {name: {eq: $name}}) {"
        "    name"
        "    tier"
        "    active"
        "    owner { name region }"
        "  }"
        "}"
    )

    variables = {"name": name}
    result = execute(query, variables=variables)

    if result["errors"]:
        raise GatewayError(result["errors"])

    data = result.get("data", {})
    deleted = data.get("delete_Service", []) if data else []

    if not deleted:
        raise ServiceNotFound([f"Service '{name}' not found"])

    s = deleted[0]
    return {
        "name": s["name"],
        "tier": s["tier"],
        "active": s["active"],
        "team": s["owner"]["name"],
        "region": s["owner"]["region"],
    }


def _fetch_services_http():
    """Fetch all services over HTTP GraphQL."""
    query = (
        "query AllServices {"
        "  Service(order: {name: {dir: ASC}}) {"
        "    name"
        "    tier"
        "    active"
        "    owner { name region }"
        "  }"
        "}"
    )

    result = execute(query)

    if result["errors"]:
        raise GatewayError(result["errors"])

    data = result.get("data", {})
    services = data.get("Service", []) if data else []

    return [
        {
            "name": s["name"],
            "tier": s["tier"],
            "active": s["active"],
            "team": s["owner"]["name"],
            "region": s["owner"]["region"],
        }
        for s in services
    ]


def _fetch_services_binary():
    """Fetch all services over Gel's native binary protocol."""
    client = gel.create_client()
    try:
        result = client.query(
            """
            SELECT Service {
                name,
                tier,
                active,
                owner: { name, region }
            }
            ORDER BY .name
            """
        )
        return [
            {
                "name": r.name,
                "tier": r.tier,
                "active": r.active,
                "team": r.owner.name,
                "region": r.owner.region,
            }
            for r in result
        ]
    finally:
        client.close()


def verify_parity():
    """Compare HTTP GraphQL results with binary protocol results."""
    http_services = _fetch_services_http()
    binary_services = _fetch_services_binary()

    http_count = len(http_services)
    binary_count = len(binary_services)

    # Build sets of tuples for comparison
    def to_key(s):
        return (s["name"], s["tier"], s["active"], s["team"], s["region"])

    http_set = {to_key(s) for s in http_services}
    binary_set = {to_key(s) for s in binary_services}

    match = http_set == binary_set

    differences = []
    if not match:
        only_http = http_set - binary_set
        only_binary = binary_set - http_set

        for s in sorted(only_http):
            differences.append(f"only in HTTP: {s}")
        for s in sorted(only_binary):
            differences.append(f"only in binary: {s}")

    return {
        "http_count": http_count,
        "binary_count": binary_count,
        "match": match,
        "differences": differences,
    }


def build_report():
    """Build a comprehensive report."""
    all_services = list_services()

    # Split into pages of size 2
    pages = {}
    for page_num, start in enumerate([0, 2, 4], 1):
        page_services = all_services[start:start + 2]
        pages[f"page_{page_num}"] = [s["name"] for s in page_services]

    return {
        "endpoint": endpoint_url(),
        "teams": fetch_teams(),
        "pages": pages,
        "parity": verify_parity(),
    }
