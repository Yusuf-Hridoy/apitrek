"""
Live test execution engine.

Executes generated test cases against the real API, mutating requests based on
the test category (positive / negative / edge / assertion) and validating
responses against expected status codes and validation rules.
"""
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from core.url_guard import safe_request, BlockedURLError

REQUEST_TIMEOUT_SECONDS = 15
RESPONSE_PREVIEW_LENGTH = 500

_TYPE_WORDS = {
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _get_expected_status(test_case: Dict[str, Any]) -> Optional[int]:
    """Pull the expected status code from either supported shape."""
    if test_case.get("expected_status") is not None:
        return test_case["expected_status"]
    expected = test_case.get("expected") or {}
    return expected.get("status_code")


def _type_matches(value: Any, word: str) -> bool:
    py_type = _TYPE_WORDS[word]
    # bool is a subclass of int — keep boolean/integer checks distinct
    if word in ("integer", "number") and isinstance(value, bool):
        return False
    return isinstance(value, py_type)


def _first_json_object(data: Any) -> Optional[Dict[str, Any]]:
    """Return a dict to inspect: the response itself, or the first item of a list."""
    if isinstance(data, dict):
        return data
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return None


def _run_assertion_rule(
    rule: str, response_data: Any, status_code: int
) -> Tuple[bool, str]:
    """
    Evaluate one free-text validation rule as best as mechanically possible.

    Understands status-code rules ("status code is 200") and quoted field
    checks ("field 'price' is present", "'id' must be an integer"). Rules that
    cannot be checked mechanically pass with a note for manual review.
    """
    lowered = rule.lower()

    # Status-code rules: "status code is 200", "returns 404 on missing resource"
    if "status" in lowered:
        match = re.search(r"\b(\d{3})\b", rule)
        if match:
            code = int(match.group(1))
            return status_code == code, f"Expected HTTP {code}, got {status_code}"

    # Quoted field names: "id", 'price'
    fields = re.findall(r"['\"]([A-Za-z_][\w.\-]*)['\"]", rule)
    target = _first_json_object(response_data)
    if fields and target is not None:
        details = []
        all_ok = True
        for field in fields:
            if field not in target:
                all_ok = False
                details.append(f"field '{field}' MISSING")
                continue
            detail = f"field '{field}' present"
            for word in _TYPE_WORDS:
                if word in lowered:
                    if _type_matches(target[field], word):
                        detail += f", type {word} OK"
                    else:
                        all_ok = False
                        detail += f", expected type {word} (got {type(target[field]).__name__})"
                    break
            details.append(detail)
        return all_ok, "; ".join(details)

    return True, "Rule not mechanically verifiable — manual review recommended"


def _build_request(
    test_case: Dict[str, Any],
    endpoint: str,
    method: str,
    headers: Optional[Dict[str, Any]],
    body: Optional[Dict[str, Any]],
) -> Tuple[str, str, Dict[str, Any], Any, List[str]]:
    """
    Build the HTTP request for a test case, mutating it based on category:
      - positive / assertion: send as-is with valid data
      - negative: remove a body field, break auth, wrong Content-Type
      - edge: boundary values (empty string, max length, null, special chars)
    Returns (url, method, headers, body, mutations_applied).
    """
    tc_request = test_case.get("request") or {}
    url = tc_request.get("endpoint") or endpoint
    req_method = (tc_request.get("method") or method or "GET").upper()
    req_headers = dict(headers or {})
    req_headers.update(tc_request.get("headers") or {})
    req_body = body if body is not None else tc_request.get("body")

    category = test_case.get("category", "positive")
    mutations: List[str] = []

    if category == "negative":
        mutated = False
        for key in list(req_headers):
            if "authorization" in key.lower():
                req_headers[key] = "Bearer invalid-token-000"
                mutations.append(f"Replaced {key} with an invalid token")
                mutated = True
        if isinstance(req_body, dict) and req_body:
            removed = next(iter(req_body))
            req_body = {k: v for k, v in req_body.items() if k != removed}
            mutations.append(f"Removed required field '{removed}' from body")
            mutated = True
        if not mutated:
            req_headers["Content-Type"] = "text/plain"
            mutations.append("Sent wrong Content-Type: text/plain")

    elif category == "edge":
        if isinstance(req_body, dict) and req_body:
            boundary_values = ["", "A" * 10000, None, "special !@#$%^&*<>'\"` chars"]
            labels = ["emptied", "set to max length (10000 chars)", "set to null",
                      "set to special characters"]
            for i, key in enumerate(list(req_body)):
                choice = i % len(boundary_values)
                req_body[key] = boundary_values[choice]
                mutations.append(f"Field '{key}' {labels[choice]}")
        else:
            mutations.append("No request body to mutate; sent as-is")

    return url, req_method, req_headers, req_body, mutations


def execute_test_case(
    test_case: Dict[str, Any],
    endpoint: str,
    method: str,
    headers: Optional[Dict[str, Any]] = None,
    body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Execute a single test case against the live API.

    Returns a structured result dict; never raises — request errors are
    captured in error_message with passed=False.
    """
    category = test_case.get("category", "positive")
    expected_status = _get_expected_status(test_case)
    assertion_results: List[Dict[str, Any]] = []
    error_message: Optional[str] = None
    actual_status = 0
    preview = ""

    start = time.perf_counter()
    try:
        url, req_method, req_headers, req_body, mutations = _build_request(
            test_case, endpoint, method, headers, body
        )
        response = safe_request(
            req_method,
            url,
            headers=req_headers,
            json=req_body if req_body is not None and req_method != "GET" else None,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        actual_status = response.status_code
        preview = response.text[:RESPONSE_PREVIEW_LENGTH]

        try:
            response_data = response.json()
        except ValueError:
            response_data = None

        if expected_status is not None:
            assertion_results.append({
                "assertion": f"Status code is {expected_status}",
                "passed": actual_status == expected_status,
                "detail": f"Expected HTTP {expected_status}, got {actual_status}",
            })

        expected = test_case.get("expected") or {}
        for rule in expected.get("validation_rules") or []:
            ok, detail = _run_assertion_rule(rule, response_data, actual_status)
            assertion_results.append({"assertion": rule, "passed": ok, "detail": detail})

        # Cases from the assertions list carry their own free-text rule
        if test_case.get("rule"):
            ok, detail = _run_assertion_rule(test_case["rule"], response_data, actual_status)
            assertion_results.append({
                "assertion": test_case["rule"], "passed": ok, "detail": detail,
            })

        for mutation in mutations:
            assertion_results.append({
                "assertion": f"Request mutation: {mutation}",
                "passed": True,
                "detail": "Applied",
            })

    except BlockedURLError as e:
        error_message = f"Request blocked: {e}"
    except requests.exceptions.Timeout:
        error_message = f"Request timed out after {REQUEST_TIMEOUT_SECONDS}s"
    except requests.exceptions.RequestException as e:
        error_message = f"Request failed: {e}"
    except Exception as e:  # never crash on malformed test case data
        error_message = f"Execution error: {e}"

    duration_ms = int((time.perf_counter() - start) * 1000)

    if error_message is not None:
        passed = False
    elif assertion_results:
        passed = all(a["passed"] for a in assertion_results)
    else:
        # Nothing to validate: a successful response counts as a pass
        passed = 200 <= actual_status < 400

    return {
        "test_case_id": str(test_case.get("id", "")),
        "title": test_case.get("title") or test_case.get("rule") or "Untitled",
        "passed": passed,
        "expected_status": expected_status,
        "actual_status": actual_status,
        "actual_response_preview": preview,
        "assertion_results": assertion_results,
        "error_message": error_message,
        "duration_ms": duration_ms,
        "category": category,
    }


def execute_test_suite(
    test_cases: List[Dict[str, Any]],
    endpoint: str,
    method: str,
    headers: Optional[Dict[str, Any]] = None,
    body: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Execute a list of test cases sequentially and return all results."""
    return [
        execute_test_case(tc, endpoint, method, headers=headers, body=body)
        for tc in test_cases
    ]
