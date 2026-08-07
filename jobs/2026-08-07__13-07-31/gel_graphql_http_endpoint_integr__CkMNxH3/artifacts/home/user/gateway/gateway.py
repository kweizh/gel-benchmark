import json
import requests
import gel

class GatewayError(Exception):
    def __init__(self, errors=None):
        self.errors = errors if errors is not None else []
        super().__init__(", ".join(self.errors) if self.errors else "GatewayError")

class ServiceNotFound(GatewayError):
    pass

_last_request = None

def last_request():
    return _last_request

def endpoint_url(branch="main"):
    return f"http://127.0.0.1:5656/branch/{branch}/graphql"

def execute(query, variables=None, operation_name=None, branch="main"):
    global _last_request
    url = endpoint_url(branch)
    
    _last_request = {
        "url": url,
        "query": query,
        "variables": variables if variables is not None else {},
        "operation_name": operation_name
    }
    
    payload = {
        "query": query
    }
    if variables is not None:
        payload["variables"] = variables
    if operation_name is not None:
        payload["operation_name"] = operation_name
        
    try:
        response = requests.post(url, json=payload)
        status = response.status_code
    except Exception as e:
        return {
            "data": None,
            "errors": [str(e)],
            "status": 500
        }
        
    try:
        res_json = response.json()
    except ValueError:
        return {
            "data": None,
            "errors": [response.text.strip()],
            "status": status
        }
        
    data = res_json.get("data")
    errors_raw = res_json.get("errors")
    errors = []
    if errors_raw:
        for err in errors_raw:
            if isinstance(err, dict) and "message" in err:
                errors.append(err["message"])
            else:
                errors.append(str(err))
                
    return {
        "data": data,
        "errors": errors,
        "status": status
    }

def list_services(region=None, min_tier=None, active_only=False, limit=None, offset=0):
    if limit is not None:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise ValueError("limit must be a positive integer or None")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError("offset must be a non-negative integer")
        
    args = []
    args.append("order: { name: { dir: ASC } }")
    
    if region is not None or min_tier is not None or active_only:
        filter_parts = []
        if region is not None:
            filter_parts.append(f'owner: {{ region: {{ eq: "{region}" }} }}')
        if min_tier is not None:
            filter_parts.append(f'tier: {{ gte: {min_tier} }}')
        if active_only:
            filter_parts.append('active: { eq: true }')
        
        args.append(f"filter: {{ {', '.join(filter_parts)} }}")
        
    if limit is not None:
        args.append(f"first: {limit}")
        
    if offset > 0:
        args.append(f'after: "{offset}"')
        
    args_str = f"({', '.join(args)})" if args else ""
    
    query = f"""
    query ListServices {{
        Service{args_str} {{
            name
            tier
            active
            team: team_name
            region: owner {{
                region
            }}
        }}
    }}
    """
    
    res = execute(query)
    if res["errors"]:
        raise GatewayError(res["errors"])
        
    data = res.get("data")
    if not data or "Service" not in data:
        return []
        
    services = []
    for item in data["Service"]:
        services.append({
            "name": item["name"],
            "tier": item["tier"],
            "active": item["active"],
            "team": item["team"],
            "region": item["region"]["region"] if item.get("region") else None
        })
        
    return services

def fetch_teams():
    query = """
    query FetchTeams {
        Team(order: { name: { dir: ASC } }) {
            team: name
            region
            service_count
            services(order: { name: { dir: ASC } }) {
                name
                active
            }
        }
    }
    """
    res = execute(query)
    if res["errors"]:
        raise GatewayError(res["errors"])
        
    data = res.get("data")
    if not data or "Team" not in data:
        return []
        
    teams = []
    for t in data["Team"]:
        services_list = t.get("services", [])
        service_names = [s["name"] for s in services_list]
        active_count = sum(1 for s in services_list if s.get("active"))
        teams.append({
            "team": t["team"],
            "region": t["region"],
            "service_count": t["service_count"],
            "active_service_count": active_count,
            "services": service_names
        })
        
    return teams

def create_service(name, tier, active, team):
    query = """
    mutation CreateService($name: String!, $tier: Int64!, $active: Boolean!, $team: String!) {
        insert_Service(data: {
            name: $name,
            tier: $tier,
            active: $active,
            owner: {
                filter: {
                    name: { eq: $team }
                }
            }
        }) {
            name
            tier
            active
            team: team_name
            region: owner {
                region
            }
        }
    }
    """
    variables = {
        "name": name,
        "tier": tier,
        "active": active,
        "team": team
    }
    res = execute(query, variables=variables)
    if res["errors"]:
        raise GatewayError(res["errors"])
        
    data = res.get("data")
    if not data or not data.get("insert_Service"):
        raise GatewayError(["Failed to insert service"])
        
    item = data["insert_Service"][0]
    return {
        "name": item["name"],
        "tier": item["tier"],
        "active": item["active"],
        "team": item["team"],
        "region": item["region"]["region"] if item.get("region") else None
    }

def retire_service(name):
    query = """
    mutation RetireService($name: String!) {
        update_Service(
            filter: { name: { eq: $name } },
            data: { active: { set: false } }
        ) {
            name
            tier
            active
            team: team_name
            region: owner {
                region
            }
        }
    }
    """
    res = execute(query, variables={"name": name})
    if res["errors"]:
        raise GatewayError(res["errors"])
        
    data = res.get("data")
    if not data or not data.get("update_Service"):
        raise ServiceNotFound([f"Service '{name}' not found"])
        
    item = data["update_Service"][0]
    return {
        "name": item["name"],
        "tier": item["tier"],
        "active": item["active"],
        "team": item["team"],
        "region": item["region"]["region"] if item.get("region") else None
    }

def delete_service(name):
    query = """
    mutation DeleteService($name: String!) {
        delete_Service(filter: { name: { eq: $name } }) {
            name
            tier
            active
            team: team_name
            region: owner {
                region
            }
        }
    }
    """
    res = execute(query, variables={"name": name})
    if res["errors"]:
        raise GatewayError(res["errors"])
        
    data = res.get("data")
    if not data or not data.get("delete_Service"):
        raise ServiceNotFound([f"Service '{name}' not found"])
        
    item = data["delete_Service"][0]
    return {
        "name": item["name"],
        "tier": item["tier"],
        "active": item["active"],
        "team": item["team"],
        "region": item["region"]["region"] if item.get("region") else None
    }

def verify_parity():
    http_services = list_services()
    
    client = gel.create_client()
    try:
        binary_raw = client.query("""
            SELECT Service {
                name,
                tier,
                active,
                team := .owner.name,
                region := .owner.region
            } ORDER BY .name ASC;
        """)
    finally:
        client.close()
        
    binary_services = []
    for s in binary_raw:
        binary_services.append({
            "name": s.name,
            "tier": s.tier,
            "active": s.active,
            "team": s.team,
            "region": s.region
        })
        
    http_count = len(http_services)
    binary_count = len(binary_services)
    
    differences = []
    
    http_map = {s["name"]: s for s in http_services}
    binary_map = {s["name"]: s for s in binary_services}
    
    all_names = sorted(list(set(http_map.keys()) | set(binary_map.keys())))
    
    for name in all_names:
        if name not in http_map:
            differences.append(f"Service '{name}' present in binary but missing in HTTP")
        elif name not in binary_map:
            differences.append(f"Service '{name}' present in HTTP but missing in binary")
        else:
            h_val = http_map[name]
            b_val = binary_map[name]
            for field in ["tier", "active", "team", "region"]:
                if h_val[field] != b_val[field]:
                    differences.append(
                        f"Service '{name}' field '{field}' differs: HTTP={h_val[field]}, binary={b_val[field]}"
                    )
                    
    differences.sort()
    match = len(differences) == 0
    
    return {
        "http_count": http_count,
        "binary_count": binary_count,
        "match": match,
        "differences": differences
    }

def build_report():
    endpoint = endpoint_url()
    teams = fetch_teams()
    
    p1 = list_services(limit=2, offset=0)
    p2 = list_services(limit=2, offset=2)
    p3 = list_services(limit=2, offset=4)
    
    pages = {
        "page_1": [s["name"] for s in p1],
        "page_2": [s["name"] for s in p2],
        "page_3": [s["name"] for s in p3]
    }
    
    parity = verify_parity()
    
    return {
        "endpoint": endpoint,
        "teams": teams,
        "pages": pages,
        "parity": parity
    }
