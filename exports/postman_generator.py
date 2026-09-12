"""
Postman Collection generator.
Transforms AI-generated test cases into a valid Postman Collection JSON.
"""

import json
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from exports.assertion_translator import postman_assertions


def _parse_url(url: str) -> Dict[str, Any]:
    """Parse a URL into Postman URL object format."""
    parsed = urlparse(url)
    protocol = parsed.scheme or "https"
    host = parsed.hostname.split(".") if parsed.hostname else ["example", "com"]
    path = [p for p in parsed.path.split("/") if p]

    query = []
    if parsed.query:
        for param in parsed.query.split("&"):
            if "=" in param:
                key, value = param.split("=", 1)
                query.append({"key": key, "value": value})
            else:
                query.append({"key": param, "value": ""})

    url_obj: Dict[str, Any] = {
        "raw": url,
        "protocol": protocol,
        "host": host,
        "path": path,
    }
    if query:
        url_obj["query"] = query
    return url_obj


def _build_headers(headers: Optional[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Convert a dict of headers into Postman header objects."""
    if not headers:
        return []
    return [{"key": str(k), "value": str(v), "type": "text"} for k, v in headers.items()]


def _build_body(body: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Build a Postman request body object for raw JSON."""
    if body is None:
        return None
    return {
        "mode": "raw",
        "raw": json.dumps(body, indent=2),
        "options": {"raw": {"language": "json"}},
    }


def _allowed_statuses(status_code: Any) -> Optional[List[int]]:
    """Normalize an expected status_code (int / digit-str / list of those) to a
    list of ints, or None when it isn't a concrete status (so callers emit a
    generic check instead of invalid JS like pm.response.to.have.status([200]))."""
    if isinstance(status_code, bool):
        return None
    if isinstance(status_code, int):
        return [status_code]
    if isinstance(status_code, str) and status_code.strip().isdigit():
        return [int(status_code)]
    if isinstance(status_code, (list, tuple)):
        allowed = [
            int(x) for x in status_code
            if (isinstance(x, int) and not isinstance(x, bool))
            or (isinstance(x, str) and x.strip().isdigit())
        ]
        return allowed or None
    return None


def _build_test_script(expected: Dict[str, Any], sample: Any) -> List[str]:
    """Generate Postman test-script lines from expected assertions."""
    lines: List[str] = []
    status_code = expected.get("status_code")
    if status_code is not None:
        allowed = _allowed_statuses(status_code)
        if allowed and len(allowed) == 1:
            lines.append(f'pm.test("Status code is {allowed[0]}", function () {{')
            lines.append(f"    pm.response.to.have.status({allowed[0]});")
            lines.append("});")
            lines.append("")
        elif allowed:
            lines.append(f'pm.test("Status code is one of {allowed}", function () {{')
            lines.append(f"    pm.expect(pm.response.code).to.be.oneOf({json.dumps(allowed)});")
            lines.append("});")
            lines.append("")
        else:
            lines.append('pm.test("Status code is not a server error", function () {')
            lines.append("    pm.expect(pm.response.code).to.be.below(500);")
            lines.append("});")
            lines.append("")

    validation_rules = expected.get("validation_rules")
    if validation_rules:
        assertion_lines = postman_assertions(sample, validation_rules)
        if assertion_lines:
            lines.append(f'pm.test("Response schema is valid", function () {{')
            lines.extend(assertion_lines)
            lines.append("});")
            lines.append("")

    return lines


def _build_case_item(case: Dict[str, Any], default_endpoint: str, default_method: str, sample: Any) -> Dict[str, Any]:
    """Build a Postman item from a single test case."""
    title = case.get("title", "Untitled")
    case_id = case.get("id", "")
    description = case.get("description", "")

    request_info = case.get("request", {}) or {}
    method = (request_info.get("method") or default_method).upper()
    # Each case sends the request its scenario describes: per-case endpoint,
    # headers, and body from the LLM's request object; baseline only as fallback.
    endpoint = request_info.get("endpoint") or default_endpoint
    request_headers = request_info.get("headers")
    request_body = request_info.get("body")

    expected = case.get("expected", {}) or {}
    script_lines = _build_test_script(expected, sample)

    item: Dict[str, Any] = {
        "name": f"{case_id} - {title}" if case_id else title,
        "request": {
            "method": method,
            "header": _build_headers(request_headers) if isinstance(request_headers, dict) else [],
            "url": _parse_url(endpoint),
            "description": description,
        },
        "response": [],
    }

    if request_body is not None:
        item["request"]["body"] = _build_body(request_body)

    if script_lines:
        item["event"] = [
            {
                "listen": "test",
                "script": {
                    "exec": script_lines,
                    "type": "text/javascript",
                },
            }
        ]

    return item


def _build_assertion_item(assertion: Dict[str, Any], endpoint: str, method: str, sample: Any) -> Dict[str, Any]:
    """Build a Postman item from an assertion rule."""
    rule = assertion.get("rule", "Assertion")
    category = assertion.get("category", "general")
    severity = assertion.get("severity", "medium")

    assertion_lines = postman_assertions(sample, [rule])
    # The translator's generic <500 fallback (emitted when the sample response
    # has no schema to check against) is the same fake-pass pattern as a
    # fabricated oneOf/200 — only real field checks count as groundable.
    schema_sample = isinstance(sample, (dict, list))
    if assertion.get("grounded", True) and schema_sample and assertion_lines:
        script_lines = [
            f'pm.test({json.dumps(f"[{severity}] {category}: {rule}")}, function () {{',
        ]
        script_lines.extend(assertion_lines)
        script_lines.append("});")
    else:
        # Not mechanically verifiable against the sample response — marked
        # manual-review instead of fabricating a pass on the baseline request.
        script_lines = [
            f'pm.test.skip({json.dumps(f"[MANUAL] [{severity}] {category}: {rule}")}, function () {{',
            "    // Not mechanically verifiable from the sample response —",
            "    // verify manually or supply a scenario request.",
            "    // (Marked unverified in Apitrek.)",
            "});",
        ]

    return {
        "name": f"[{severity}] {rule}",
        "request": {
            "method": method.upper(),
            "header": [],
            "url": _parse_url(endpoint),
            "description": f"Category: {category}, Severity: {severity}",
        },
        "response": [],
        "event": [
            {
                "listen": "test",
                "script": {
                    "exec": script_lines,
                    "type": "text/javascript",
                },
            }
        ],
    }


def generate_postman_collection(
    endpoint: str,
    method: str,
    test_data: Dict[str, Any],
    headers: Optional[Dict[str, Any]] = None,
    request_body: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Generate a Postman Collection v2.1 JSON string from structured test data.

    Args:
        endpoint: The API endpoint URL.
        method: Default HTTP method.
        test_data: Dict with keys positive_test_cases, negative_test_cases,
                   edge_cases, assertions.
        headers: Optional global headers to apply to requests.
        request_body: Optional global request body to apply to requests.

    Returns:
        A JSON string representing a valid Postman Collection.
    """
    if not endpoint:
        endpoint = "https://example.com/api"
    if not method:
        method = "GET"

    positive_cases = test_data.get("positive_test_cases") or []
    negative_cases = test_data.get("negative_test_cases") or []
    edge_cases = test_data.get("edge_cases") or []
    assertions = test_data.get("assertions") or []
    sample = test_data.get("sample_response")

    items: List[Dict[str, Any]] = []

    for case in positive_cases:
        items.append(_build_case_item(case, endpoint, method, sample))
    for case in negative_cases:
        items.append(_build_case_item(case, endpoint, method, sample))
    for case in edge_cases:
        items.append(_build_case_item(case, endpoint, method, sample))
    for assertion in assertions:
        items.append(_build_assertion_item(assertion, endpoint, method, sample))

    if not items:
        # Minimal fallback collection with a smoke-test request
        items.append(
            {
                "name": "Smoke Test",
                "request": {
                    "method": method.upper(),
                    "header": _build_headers(headers),
                    "url": _parse_url(endpoint),
                    "description": "Minimal smoke test — no AI-generated cases available.",
                },
                "response": [],
                "event": [
                    {
                        "listen": "test",
                        "script": {
                            "exec": [
                                'pm.test("Response status is valid", function () {',
                                "    pm.expect(pm.response.code).to.be.oneOf([200, 201, 204]);",
                                "});",
                            ],
                            "type": "text/javascript",
                        },
                    }
                ],
            }
        )

    # Apply global headers / body to items that don't already define them
    if headers:
        for item in items:
            if not item["request"].get("header"):
                item["request"]["header"] = _build_headers(headers)

    if request_body:
        for item in items:
            if not item["request"].get("body"):
                item["request"]["body"] = _build_body(request_body)

    collection = {
        "info": {
            "name": f"API Tests - {endpoint}",
            "description": f"Auto-generated Postman collection for {endpoint}",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "item": items,
    }

    return json.dumps(collection, indent=2)
