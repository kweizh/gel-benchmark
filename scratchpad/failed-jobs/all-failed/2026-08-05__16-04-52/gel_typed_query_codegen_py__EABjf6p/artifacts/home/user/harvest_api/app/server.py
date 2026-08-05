"""HTTP JSON service for the harvest-tracking API."""

from __future__ import annotations

import json
import re
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any

import gel

from app.queries.list_region_growers_edgeql import list_region_growers
from app.queries.get_batch_detail_edgeql import get_batch_detail
from app.queries.record_inspection_edgeql import record_inspection
from app.queries.region_totals_edgeql import region_totals

HOST = "127.0.0.1"
PORT = 8099

_NON_EMPTY_STR_RE = re.compile(r".+")


def _json_response(handler: BaseHTTPRequestHandler, status: int, body: dict[str, Any]) -> None:
    data = json.dumps(body).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _read_json_body(handler: BaseHTTPRequestHandler) -> Any | None:
    content_length = int(handler.headers.get("Content-Length", 0))
    if content_length == 0:
        return None
    raw = handler.rfile.read(content_length)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _is_non_empty_str(val: Any) -> bool:
    return isinstance(val, str) and bool(_NON_EMPTY_STR_RE.match(val))


def _is_optional_number(val: Any) -> bool:
    return val is None or isinstance(val, (int, float)) or (isinstance(val, bool) and False)


def _is_number(val: Any) -> bool:
    return isinstance(val, (int, float)) and not isinstance(val, bool)


def _is_bool(val: Any) -> bool:
    return isinstance(val, bool)


def _is_str_array(val: Any, max_len: int = 8) -> bool:
    if not isinstance(val, list):
        return False
    if len(val) > max_len:
        return False
    return all(_is_non_empty_str(item) for item in val)


def _is_int(val: Any) -> bool:
    return isinstance(val, int) and not isinstance(val, bool)


class Handler(BaseHTTPRequestHandler):
    """HTTP request handler for the harvest API."""

    # Silence default logging
    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_404(self) -> None:
        _json_response(self, 404, {"error": "not_found"})

    def _send_400(self) -> None:
        _json_response(self, 400, {"error": "invalid_request"})

    def do_GET(self) -> None:
        if self.path == "/healthz":
            _json_response(self, 200, {"status": "ok"})
            return

        # GET /batches/<code>
        if self.path.startswith("/batches/"):
            code = self.path[len("/batches/"):]
            if not _is_non_empty_str(code):
                self._send_404()
                return
            try:
                client = gel.create_client()
                result = get_batch_detail(client, code=code)
                client.close()
            except Exception:
                self._send_400()
                return

            if result is None:
                _json_response(self, 404, {"error": "not_found"})
                return

            body = {
                "code": result.code,
                "kilograms": result.kilograms,
                "harvested_on": result.harvested_on.isoformat(),
                "certifications": sorted(result.certifications),
                "grower": {
                    "slug": result.grower.slug,
                    "name": result.grower.name,
                    "region": {
                        "code": result.grower.region.code,
                        "name": result.grower.region.name,
                    },
                },
                "inspection_count": result.inspection_count,
            }
            _json_response(self, 200, body)
            return

        self._send_404()

    def do_POST(self) -> None:
        if self.path == "/growers/search":
            self._handle_growers_search()
        elif self.path == "/inspections":
            self._handle_inspections()
        elif self.path == "/regions/totals":
            self._handle_region_totals()
        else:
            self._send_404()

    def _handle_growers_search(self) -> None:
        body = _read_json_body(self)
        if not isinstance(body, dict):
            self._send_400()
            return

        region_code = body.get("region_code")
        if not _is_non_empty_str(region_code):
            self._send_400()
            return

        min_kilograms = body.get("min_kilograms")
        if min_kilograms is not None and not _is_optional_number(min_kilograms):
            self._send_400()
            return

        certifications = body.get("certifications")
        if certifications is None:
            certifications = []
        if not _is_str_array(certifications):
            self._send_400()
            return

        try:
            client = gel.create_client()
            results = list_region_growers(
                client,
                region_code=region_code,
                min_kilograms=min_kilograms,
                certifications=certifications,
            )
            client.close()
        except Exception:
            self._send_400()
            return

        growers = []
        for g in results:
            batches = []
            for b in g.batches:
                batches.append({
                    "code": b.code,
                    "kilograms": b.kilograms,
                    "harvested_on": b.harvested_on.isoformat(),
                    "certifications": sorted(b.certifications),
                })
            matched_kg = sum(b.kilograms for b in g.batches)
            growers.append({
                "slug": g.slug,
                "name": g.name,
                "region": {
                    "code": g.region.code,
                    "name": g.region.name,
                },
                "batches": batches,
                "matched_batches": len(batches),
                "matched_kilograms": matched_kg,
            })

        _json_response(self, 200, {
            "region_code": region_code,
            "growers": growers,
        })

    def _handle_inspections(self) -> None:
        body = _read_json_body(self)
        if not isinstance(body, dict):
            self._send_400()
            return

        # Validate required fields
        batch_code = body.get("batch_code")
        if not _is_non_empty_str(batch_code):
            self._send_400()
            return

        inspector = body.get("inspector")
        if not _is_non_empty_str(inspector):
            self._send_400()
            return

        passed = body.get("passed")
        if not _is_bool(passed):
            self._send_400()
            return

        defect_codes = body.get("defect_codes")
        if not isinstance(defect_codes, list):
            self._send_400()
            return
        if len(defect_codes) > 8:
            self._send_400()
            return
        if not all(_is_non_empty_str(c) for c in defect_codes):
            self._send_400()
            return

        severity = body.get("severity")
        if not _is_int(severity):
            self._send_400()
            return
        if severity < 1 or severity > 5:
            self._send_400()
            return

        # Check all required keys are present (no extras allowed by spec? spec says "all keys required")
        required_keys = {"batch_code", "inspector", "passed", "defect_codes", "severity"}
        if set(body.keys()) != required_keys:
            self._send_400()
            return

        try:
            client = gel.create_client()
            # Check batch exists first
            existing = get_batch_detail(client, code=batch_code)
            if existing is None:
                client.close()
                _json_response(self, 404, {"error": "batch_not_found"})
                return

            result = record_inspection(
                client,
                batch_code=batch_code,
                inspector=inspector,
                passed=passed,
                defect_codes=defect_codes,
                severity=severity,
            )
            client.close()
        except Exception:
            self._send_400()
            return

        _json_response(self, 201, {
            "inspection_id": str(result.id),
            "batch_code": result.batch_code,
            "inspector": result.inspector,
            "passed": result.passed,
            "defect_count": result.defect_count,
        })

    def _handle_region_totals(self) -> None:
        body = _read_json_body(self)
        if not isinstance(body, dict):
            self._send_400()
            return

        region_codes = body.get("region_codes")
        if region_codes is None:
            region_codes = []
        if not isinstance(region_codes, list):
            self._send_400()
            return
        if not all(_is_non_empty_str(c) for c in region_codes):
            self._send_400()
            return

        try:
            client = gel.create_client()
            results = region_totals(client, region_codes=region_codes)
            client.close()
        except Exception:
            self._send_400()
            return

        regions = []
        for r in results:
            regions.append({
                "code": r.code,
                "name": r.name,
                "grower_count": r.grower_count,
                "batch_count": r.batch_count,
                "total_kilograms": r.total_kilograms,
            })

        _json_response(self, 200, {"regions": regions})


def main() -> None:
    server = HTTPServer((HOST, PORT), Handler)
    print(f"Listening on {HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
