"""
Postman Collection generator.
Transforms AI-generated test cases into a valid Postman Collection JSON.
"""

import json
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from exports.assertion_translator import to_postman


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


def _build_test_script(expected: Dict[str, Any]) -> List[str]:
    """Generate Postman test-script lines from expected assertions."""
    lines: List[str] = []
    status_code = expected.get("status_code")
    if status_code is not None:
        lines.append(f'pm.test("Status code is {status_code}", function () {{')
        lines.append(f"    pm.response.to.have.status({status_code});")
        lines.append("});")
        lines.append("")

    validation_rules = expected.get("validation_rules")
    if validation_rules:
        for rule in validation_rules:
            lines.append(f'pm.test({json.dumps(rule)}, function () {{')
            lines.extend(to_postman(rule))
            lines.append("});")
            lines.append("")

    return lines


def _build_case_item(case: Dict[str, Any], default_endpoint: str, default_method: str) -> Dict[str, Any]:
    """Build a Postman item from a single test case."""
    title = case.get("title", "Untitled")
    case_id = case.get("id", "")
    description = case.get("description", "")

    request_info = case.get("request", {}) or {}
    method = (request_info.get("method") or default_method).upper()
    endpoint = default_endpoint
    request_body = request_info.get("body")

    expected = case.get("expected", {}) or {}
    script_lines = _build_test_script(expected)

    item: Dict[str, Any] = {
        "name": f"{case_id} - {title}" if case_id else title,
        "request": {
            "method": method,
            "header": [],
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


def _build_assertion_item(assertion: Dict[str, Any], endpoint: str, method: str) -> Dict[str, Any]:
    """Build a Postman item from an assertion rule."""
    rule = assertion.get("rule", "Assertion")
    category = assertion.get("category", "general")
    severity = assertion.get("severity", "medium")

    script_lines = [
        f'pm.test({json.dumps(f"[{severity}] {category}: {rule}")}, function () {{',
    ]
    script_lines.extend(to_postman(rule))
    script_lines.append("});")

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

    items: List[Dict[str, Any]] = []

    for case in positive_cases:
        items.append(_build_case_item(case, endpoint, method))
    for case in negative_cases:
        items.append(_build_case_item(case, endpoint, method))
    for case in edge_cases:
        items.append(_build_case_item(case, endpoint, method))
    for assertion in assertions:
        items.append(_build_assertion_item(assertion, endpoint, method))

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
