"""GraphQL-over-HTTP gateway for the Gel service registry, with a
native-binary-protocol parity checker.

All GraphQL traffic goes over plain HTTP to
``http://127.0.0.1:5656/branch/<branch>/graphql``.  Every request/response
is recorded so that :func:`last_request` can describe exactly what was
sent for the most recent call.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

import gel

__all__ = [
    "GatewayError",
    "ServiceNotFound",
    "endpoint_url",
    "execute",
    "last_request",
    "list_services",
    "fetch_teams",
    "create_service",
    "retire_service",
    "delete_service",
    "verify_parity",
    "build_report",
]

_HOST = "127.0.0.1"
_PORT = 5656

# Records the most recent GraphQL request that was actually sent.
_last_request: Optional[Dict[str, Any]] = None


class GatewayError(Exception):
    """Raised when a GraphQL request fails at the GraphQL level."""

    def __init__(self, message: str, errors: Optional[List[str]] = None):
        super().__init__(message)
        self.errors: List[str] = list(errors) if errors else []


class ServiceNotFound(GatewayError):
    """Raised when an operation targets a service that does not exist."""


def endpoint_url(branch: str = "main") -> str:
    """Return the GraphQL-over-HTTP URL for the given branch."""
    return f"http://{_HOST}:{_PORT}/branch/{branch}/graphql"


def execute(
    query: str,
    variables: Optional[Dict[str, Any]] = None,
    operation_name: Optional[str] = None,
    branch: str = "main",
) -> Dict[str, Any]:
    """Send a single GraphQL request over plain HTTP.

    Never raises: GraphQL-level errors and non-2xx HTTP statuses are both
    reported through the returned dict.
    """
    global _last_request

    url = endpoint_url(branch)
    sent_variables = dict(variables) if variables else {}

    payload: Dict[str, Any] = {"query": query, "variables": sent_variables}
    if operation_name is not None:
        payload["operationName"] = operation_name

    _last_request = {
        "url": url,
        "query": query,
        "variables": sent_variables,
        "operation_name": operation_name,
    }

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req) as resp:
            status = resp.getcode()
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read()
    except urllib.error.URLError as exc:
        return {"data": None, "errors": [str(exc)], "status": 0}

    text = raw.decode("utf-8", errors="replace")

    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return {"data": None, "errors": [text.strip()], "status": status}

    data = parsed.get("data")
    raw_errors = parsed.get("errors") or []
    errors = [e.get("message", str(e)) if isinstance(e, dict) else str(e) for e in raw_errors]

    return {"data": data, "errors": errors, "status": status}


def last_request() -> Optional[Dict[str, Any]]:
    """Describe the most recent GraphQL request that was actually sent."""
    if _last_request is None:
        return None
    return dict(_last_request)


def _run(query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Execute a query/mutation and raise GatewayError on GraphQL errors."""
    result = execute(query, variables=variables)
    if result["errors"]:
        raise GatewayError("GraphQL request failed", errors=result["errors"])
    return result["data"]


def list_services(
    region: Optional[str] = None,
    min_tier: Optional[int] = None,
    active_only: bool = False,
    limit: Optional[int] = None,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """List services matching the given filters, ordered by name."""
    if limit is not None:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise ValueError("limit must be None or a positive integer")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError("offset must be a non-negative integer")

    filter_clauses: List[str] = []
    var_defs: List[str] = []
    variables: Dict[str, Any] = {}

    if region is not None:
        filter_clauses.append("owner: {region: {eq: $region}}")
        var_defs.append("$region: String")
        variables["region"] = region
    if min_tier is not None:
        filter_clauses.append("tier: {gte: $minTier}")
        var_defs.append("$minTier: Int64")
        variables["minTier"] = min_tier
    if active_only:
        filter_clauses.append("active: {eq: true}")

    args = ["order: {name: {dir: ASC}}"]
    if filter_clauses:
        args.append("filter: {" + ", ".join(filter_clauses) + "}")
    if limit is not None:
        var_defs.append("$first: Int")
        variables["first"] = limit
        args.append("first: $first")
    if offset > 0:
        var_defs.append("$after: String")
        variables["after"] = str(offset - 1)
        args.append("after: $after")

    header = "query" + ("(" + ", ".join(var_defs) + ")" if var_defs else "")
    query = (
        f"{header} {{ Service({', '.join(args)}) "
        "{ name tier active owner { name region } } }"
    )

    data = _run(query, variables=variables)
    out = []
    for svc in data["Service"]:
        out.append(
            {
                "name": svc["name"],
                "tier": svc["tier"],
                "active": svc["active"],
                "team": svc["owner"]["name"],
                "region": svc["owner"]["region"],
            }
        )
    return out


def fetch_teams() -> List[Dict[str, Any]]:
    """List every team with its services, ordered by team name."""
    query = (
        "query { Team(order: {name: {dir: ASC}}) { name region service_count "
        "services(order: {name: {dir: ASC}}) { name active } } }"
    )
    data = _run(query)
    out = []
    for team in data["Team"]:
        services = team["services"]
        out.append(
            {
                "team": team["name"],
                "region": team["region"],
                "service_count": team["service_count"],
                "active_service_count": sum(1 for s in services if s["active"]),
                "services": [s["name"] for s in services],
            }
        )
    out.sort(key=lambda t: t["team"])
    return out


def create_service(name: str, tier: int, active: bool, team: str) -> Dict[str, Any]:
    """Insert a new service owned by the named (existing) team."""
    query = (
        "mutation($name: String!, $tier: Int64!, $active: Boolean!, $team: String!) "
        "{ insert_Service(data: [{name: $name, tier: $tier, active: $active, "
        "owner: {filter: {name: {eq: $team}}}}]) "
        "{ name tier active owner { name region } } }"
    )
    variables = {"name": name, "tier": tier, "active": active, "team": team}
    data = _run(query, variables=variables)
    svc = data["insert_Service"][0]
    return {
        "name": svc["name"],
        "tier": svc["tier"],
        "active": svc["active"],
        "team": svc["owner"]["name"],
        "region": svc["owner"]["region"],
    }


def retire_service(name: str) -> Dict[str, Any]:
    """Set a service's active flag to false."""
    query = (
        "mutation($name: String!) { update_Service(filter: {name: {eq: $name}}, "
        "data: {active: {set: false}}) { name tier active owner { name region } } }"
    )
    data = _run(query, variables={"name": name})
    results = data["update_Service"]
    if not results:
        raise ServiceNotFound(f"no service named {name!r}")
    svc = results[0]
    return {
        "name": svc["name"],
        "tier": svc["tier"],
        "active": svc["active"],
        "team": svc["owner"]["name"],
        "region": svc["owner"]["region"],
    }


def delete_service(name: str) -> Dict[str, Any]:
    """Delete a service by name."""
    query = (
        "mutation($name: String!) { delete_Service(filter: {name: {eq: $name}}) "
        "{ name tier active owner { name region } } }"
    )
    data = _run(query, variables={"name": name})
    results = data["delete_Service"]
    if not results:
        raise ServiceNotFound(f"no service named {name!r}")
    svc = results[0]
    return {
        "name": svc["name"],
        "tier": svc["tier"],
        "active": svc["active"],
        "team": svc["owner"]["name"],
        "region": svc["owner"]["region"],
    }


def _fetch_all_services_http() -> List[Dict[str, Any]]:
    query = (
        "query { Service(order: {name: {dir: ASC}}) "
        "{ name tier active owner { name region } } }"
    )
    data = _run(query)
    return [
        {
            "name": svc["name"],
            "tier": svc["tier"],
            "active": svc["active"],
            "team": svc["owner"]["name"],
            "region": svc["owner"]["region"],
        }
        for svc in data["Service"]
    ]


def _fetch_all_services_binary() -> List[Dict[str, Any]]:
    client = gel.create_client()
    try:
        rows = client.query(
            "select Service { name, tier, active, owner: { name, region } } "
            "order by .name;"
        )
        return [
            {
                "name": row.name,
                "tier": row.tier,
                "active": row.active,
                "team": row.owner.name,
                "region": row.owner.region,
            }
            for row in rows
        ]
    finally:
        client.close()


def verify_parity() -> Dict[str, Any]:
    """Cross-check the HTTP GraphQL view of services against the native
    binary protocol view."""
    http_services = _fetch_all_services_http()
    binary_services = _fetch_all_services_binary()

    def key(svc):
        return svc["name"]

    http_by_name = {s["name"]: s for s in http_services}
    binary_by_name = {s["name"]: s for s in binary_services}

    differences: List[str] = []

    only_http = set(http_by_name) - set(binary_by_name)
    only_binary = set(binary_by_name) - set(http_by_name)
    for n in only_http:
        differences.append(f"{n}: present over http only")
    for n in only_binary:
        differences.append(f"{n}: present over binary only")

    for n in set(http_by_name) & set(binary_by_name):
        h = http_by_name[n]
        b = binary_by_name[n]
        if h != b:
            differences.append(f"{n}: http={h!r} binary={b!r}")

    differences.sort()

    return {
        "http_count": len(http_services),
        "binary_count": len(binary_services),
        "match": len(differences) == 0,
        "differences": differences,
    }


def build_report() -> Dict[str, Any]:
    """Assemble the full status report."""
    all_names = sorted(s["name"] for s in _fetch_all_services_http())
    pages = {
        "page_1": all_names[0:2],
        "page_2": all_names[2:4],
        "page_3": all_names[4:6],
    }
    return {
        "endpoint": endpoint_url(),
        "teams": fetch_teams(),
        "pages": pages,
        "parity": verify_parity(),
    }
