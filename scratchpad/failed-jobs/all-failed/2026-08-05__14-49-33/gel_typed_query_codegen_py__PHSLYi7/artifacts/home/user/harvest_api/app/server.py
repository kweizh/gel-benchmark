import json
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
import gel

# Import the generated functions
from app.queries.list_region_growers_async_edgeql import list_region_growers
from app.queries.get_batch_detail_async_edgeql import get_batch_detail
from app.queries.record_inspection_async_edgeql import record_inspection
from app.queries.region_totals_async_edgeql import region_totals

# Create the client
client = gel.create_async_client()

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await client.aclose()

app = FastAPI(lifespan=lifespan)

def ordered_json_response(data, status_code=200):
    content = json.dumps(data)
    return Response(content=content, media_type="application/json", status_code=status_code)

def invalid_request_response():
    return ordered_json_response({"error": "invalid_request"}, status_code=400)

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return ordered_json_response({"error": "not_found"}, status_code=404)
    # For any other HTTP exception (including 405 Method Not Allowed), return 404 not_found
    return ordered_json_response({"error": "not_found"}, status_code=404)

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    # Any other unhandled exception in routing/middleware -> return 404 not_found to satisfy "Any other path or method -> 404"
    return ordered_json_response({"error": "not_found"}, status_code=404)

@app.get("/healthz")
async def healthz():
    return ordered_json_response({"status": "ok"}, status_code=200)

@app.post("/growers/search")
async def search_growers(request: Request):
    try:
        body = await request.json()
    except Exception:
        return invalid_request_response()

    if not isinstance(body, dict):
        return invalid_request_response()

    if "region_code" not in body:
        return invalid_request_response()

    region_code = body["region_code"]
    if not isinstance(region_code, str) or not region_code:
        return invalid_request_response()

    min_kilograms = body.get("min_kilograms")
    if min_kilograms is not None:
        if isinstance(min_kilograms, bool) or not isinstance(min_kilograms, (int, float)):
            return invalid_request_response()
        min_kilograms = float(min_kilograms)

    certifications = body.get("certifications")
    if certifications is not None:
        if not isinstance(certifications, list):
            return invalid_request_response()
        if len(certifications) > 8:
            return invalid_request_response()
        for cert in certifications:
            if not isinstance(cert, str) or not cert:
                return invalid_request_response()
    else:
        certifications = []

    # Call the database query
    try:
        result = await list_region_growers(
            client,
            region_code=region_code,
            min_kilograms=min_kilograms,
            certifications=certifications,
        )
    except Exception:
        return invalid_request_response()

    growers_list = []
    for g in result:
        batches_list = []
        matched_kilograms = 0.0
        for b in g.batches:
            sorted_certs = sorted(list(b.certifications))
            batches_list.append({
                "code": b.code,
                "kilograms": b.kilograms,
                "harvested_on": b.harvested_on.isoformat(),
                "certifications": sorted_certs,
            })
            matched_kilograms += b.kilograms

        growers_list.append({
            "slug": g.slug,
            "name": g.name,
            "region": {
                "code": g.region.code,
                "name": g.region.name,
            },
            "batches": batches_list,
            "matched_batches": len(batches_list),
            "matched_kilograms": matched_kilograms,
        })

    response_data = {
        "region_code": region_code,
        "growers": growers_list,
    }
    return ordered_json_response(response_data, status_code=200)

@app.get("/batches/{code}")
async def get_batch(code: str):
    # Path parameter is already a string. If it is empty (which FastAPI won't route to this endpoint anyway), handled by 404.
    try:
        batch = await get_batch_detail(client, code=code)
    except Exception:
        return ordered_json_response({"error": "not_found"}, status_code=404)

    if not batch:
        return ordered_json_response({"error": "not_found"}, status_code=404)

    sorted_certs = sorted(list(batch.certifications))
    response_data = {
        "code": batch.code,
        "kilograms": batch.kilograms,
        "harvested_on": batch.harvested_on.isoformat(),
        "certifications": sorted_certs,
        "grower": {
            "slug": batch.grower.slug,
            "name": batch.grower.name,
            "region": {
                "code": batch.grower.region.code,
                "name": batch.grower.region.name,
            },
        },
        "inspection_count": batch.inspection_count,
    }
    return ordered_json_response(response_data, status_code=200)

@app.post("/inspections")
async def create_inspection(request: Request):
    try:
        body = await request.json()
    except Exception:
        return invalid_request_response()

    if not isinstance(body, dict):
        return invalid_request_response()

    required_keys = {"batch_code", "inspector", "passed", "defect_codes", "severity"}
    if not required_keys.issubset(body.keys()):
        return invalid_request_response()

    batch_code = body["batch_code"]
    if not isinstance(batch_code, str) or not batch_code:
        return invalid_request_response()

    inspector = body["inspector"]
    if not isinstance(inspector, str) or not inspector:
        return invalid_request_response()

    passed = body["passed"]
    if not isinstance(passed, bool):
        return invalid_request_response()

    defect_codes = body["defect_codes"]
    if not isinstance(defect_codes, list):
        return invalid_request_response()
    if len(defect_codes) > 8:
        return invalid_request_response()
    for code in defect_codes:
        if not isinstance(code, str) or not code:
            return invalid_request_response()

    severity = body["severity"]
    if isinstance(severity, bool) or not isinstance(severity, int):
        return invalid_request_response()
    if not (1 <= severity <= 5):
        return invalid_request_response()

    # First, verify if the batch exists in the database
    try:
        batch_exists = await client.query_single(
            "select exists (select Batch filter .code = <str>$code)",
            code=batch_code,
        )
    except Exception:
        return invalid_request_response()

    if not batch_exists:
        return ordered_json_response({"error": "batch_not_found"}, status_code=404)

    # Insert the inspection
    try:
        inspection = await record_inspection(
            client,
            batch_code=batch_code,
            inspector=inspector,
            passed=passed,
            defect_codes=defect_codes,
            severity=severity,
        )
    except Exception:
        return invalid_request_response()

    response_data = {
        "inspection_id": str(inspection.id),
        "batch_code": inspection.batch_code,
        "inspector": inspection.inspector,
        "passed": inspection.passed,
        "defect_count": inspection.defect_count,
    }
    return ordered_json_response(response_data, status_code=201)

@app.post("/regions/totals")
async def get_region_totals(request: Request):
    try:
        body = await request.json()
    except Exception:
        return invalid_request_response()

    if not isinstance(body, dict):
        return invalid_request_response()

    region_codes = body.get("region_codes")
    if region_codes is not None:
        if not isinstance(region_codes, list):
            return invalid_request_response()
        if len(region_codes) > 8:
            return invalid_request_response()
        for code in region_codes:
            if not isinstance(code, str) or not code:
                return invalid_request_response()
    else:
        region_codes = []

    try:
        result = await region_totals(client, region_codes=region_codes)
    except Exception:
        return invalid_request_response()

    regions_list = []
    for r in result:
        regions_list.append({
            "code": r.code,
            "name": r.name,
            "grower_count": r.grower_count,
            "batch_count": r.batch_count,
            "total_kilograms": r.total_kilograms,
        })

    response_data = {
        "regions": regions_list,
    }
    return ordered_json_response(response_data, status_code=200)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.server:app", host="127.0.0.1", port=8099, log_level="info")
