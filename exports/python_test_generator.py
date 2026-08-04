"""
Pytest automation script generator.
Transforms AI-generated test cases into runnable pytest Python scripts.
"""

import json
import re
from typing import Any, Dict, List


def _sanitize_name(name: str) -> str:
    """Convert a title into a valid Python function name."""
    name = re.sub(r"[^\w\s-]", "", name.lower())
    name = re.sub(r"[\s-]+", "_", name).strip("_")
    return name or "test_case"


def _build_request_helper() -> str:
    return '''def _make_request(method, url, json=None, headers=None):
    """Send an HTTP request using the requests library."""
    if headers is None:
        headers = {}
    method = method.upper()
    if method == "GET":
        return requests.get(url, headers=headers)
    elif method == "POST":
        return requests.post(url, json=json, headers=headers)
    elif method == "PUT":
        return requests.put(url, json=json, headers=headers)
    elif method == "PATCH":
        return requests.patch(url, json=json, headers=headers)
    elif method == "DELETE":
        return requests.delete(url, headers=headers)
    elif method == "HEAD":
        return requests.head(url, headers=headers)
    elif method == "OPTIONS":
        return requests.options(url, headers=headers)
    else:
        raise ValueError(f"Unsupported HTTP method: {method}")
'''


def _build_assertions_for_case(case: Dict[str, Any]) -> List[str]:
    """Extract structured assertions from a test case."""
    lines = []
    expected = case.get("expected", {}) or {}
    status_code = expected.get("status_code")
    if status_code is not None:
        lines.append(f"    assert response.status_code == {status_code}")

    validation_rules = expected.get("validation_rules")
    if validation_rules:
        lines.append("    data = response.json()")
        for rule in validation_rules:
            lines.append(f"    # Validation: {rule}")
            words = rule.split()
            field = words[0] if words else ""
            lower_rule = rule.lower()
            if field.isidentifier():
                if "should be present" in lower_rule or "should exist" in lower_rule:
                    lines.append(f'    assert "{field}" in data')
                elif "should be integer" in lower_rule:
                    lines.append(f'    assert isinstance(data["{field}"], int)')
                elif "should be string" in lower_rule:
                    lines.append(f'    assert isinstance(data["{field}"], str)')
                elif "should be boolean" in lower_rule:
                    lines.append(f'    assert isinstance(data["{field}"], bool)')
                elif "should be float" in lower_rule or "should be number" in lower_rule:
                    lines.append(f'    assert isinstance(data["{field}"], (float, int))')
    return lines


def _generate_case_tests(cases: List[Dict[str, Any]], prefix: str) -> List[str]:
    """Generate test function strings from a list of test cases."""
    tests = []
    seen_names = set()

    for case in cases:
        case_id = case.get("id", "")
        title = case.get("title", "Untitled")
        description = case.get("description", "")
        request_info = case.get("request", {}) or {}
        method = request_info.get("method")

        base_name = _sanitize_name(title)
        name = f"test_{prefix}_{base_name}"
        if name in seen_names:
            idx = 1
            while f"{name}_{idx}" in seen_names:
                idx += 1
            name = f"{name}_{idx}"
        seen_names.add(name)

        doc_parts = [p for p in [case_id, title, description] if p]
        docstring = " - ".join(doc_parts)

        test_method = method if method else "HTTP_METHOD"
        json_arg = ""
        request_body = request_info.get("body")
        if request_body is not None:
            json_arg = f", json={json.dumps(request_body)}"

        test_lines = [
            "",
            f"def {name}():",
            f'    """{docstring}"""',
            f'    response = _make_request("{test_method}", ENDPOINT{json_arg})',
        ]

        assertion_lines = _build_assertions_for_case(case)
        if not assertion_lines:
            test_lines.append("    assert response is not None")
        else:
            test_lines.extend(assertion_lines)

        tests.append("\n".join(test_lines))

    return tests


def _generate_assertion_tests(assertions: List[Dict[str, Any]]) -> List[str]:
    """Generate test functions from AI assertion rules."""
    tests = []
    seen_names = set()

    for idx, assertion in enumerate(assertions):
        rule = assertion.get("rule", f"assertion_{idx}")
        category = assertion.get("category", "general")
        severity = assertion.get("severity", "medium")

        base_name = _sanitize_name(rule)
        name = f"test_assertion_{base_name}"
        if name in seen_names:
            counter = 1
            while f"{name}_{counter}" in seen_names:
                counter += 1
            name = f"{name}_{counter}"
        seen_names.add(name)

        test_lines = [
            "",
            f"def {name}():",
            f'    """[{severity}] {category}: {rule}"""',
            "    response = _make_request(HTTP_METHOD, ENDPOINT)",
            "    assert response.status_code == 200  # Adjust if expected behavior differs",
        ]
        tests.append("\n".join(test_lines))

    return tests


def generate_pytest_script(endpoint: str, method: str, test_data: Dict[str, Any]) -> str:
    """
    Generate a pytest-compatible Python script from structured test data.

    Args:
        endpoint: The API endpoint URL.
        method: HTTP method used.
        test_data: Dict with keys positive_test_cases, negative_test_cases,
                   edge_cases, assertions.

    Returns:
        A string containing the full Python script.
    """
    if not endpoint:
        endpoint = "https://example.com/api"
    if not method:
        method = "GET"

    positive_cases = test_data.get("positive_test_cases") or []
    negative_cases = test_data.get("negative_test_cases") or []
    edge_cases = test_data.get("edge_cases") or []
    assertions = test_data.get("assertions") or []

    parts = [
        '"""',
        "Auto-generated pytest API tests",
        f"Endpoint: {endpoint}",
        f"Method: {method}",
        '"""',
        "",
        "import requests",
        "",
        f'ENDPOINT = "{endpoint}"',
        f'HTTP_METHOD = "{method.upper()}"',
        "",
        _build_request_helper(),
    ]

    if positive_cases:
        parts.append("\n# --- Positive Test Cases ---")
        parts.extend(_generate_case_tests(positive_cases, "positive"))

    if negative_cases:
        parts.append("\n# --- Negative Test Cases ---")
        parts.extend(_generate_case_tests(negative_cases, "negative"))

    if edge_cases:
        parts.append("\n# --- Edge Cases ---")
        parts.extend(_generate_case_tests(edge_cases, "edge"))

    if assertions:
        parts.append("\n# --- Assertions ---")
        parts.extend(_generate_assertion_tests(assertions))

    if not any([positive_cases, negative_cases, edge_cases, assertions]):
        parts.append("\ndef test_endpoint_is_reachable():")
        parts.append('    """Minimal smoke test — no AI-generated cases available."""')
        parts.append("    response = _make_request(HTTP_METHOD, ENDPOINT)")
        parts.append("    assert response.status_code in (200, 201, 204)")

    return "\n".join(parts)
