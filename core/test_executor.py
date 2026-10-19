"""
Live test execution engine.

Executes generated test cases against the real API. Each case describes its
own request (endpoint, method, headers, body) — the runner sends exactly that,
the same request the pytest/Postman exports would send. Only a case with no
request at all gets a generic per-category fallback mutation, and the notes
say so. Responses are validated against expected status codes (which may be a
list) and validation rules.

Honesty: notes about what was sent are info, never checks; a rule the runner
cannot verify mechanically is not a pass; and a request the HTTP client or the
SSRF guard refused is "not run", not a failure of the API.
"""
import copy
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from core.auth_check import _normalize_expected_status
from core.database import _SENSITIVE_HEADER_HINTS
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

# The HTTP client refused to send the request at all (bad header, bad URL) —
# the API was never tested, so this is "not run", never a failure.
_CLIENT_REFUSAL_ERRORS = (
    requests.exceptions.InvalidHeader,
    requests.exceptions.InvalidURL,
    requests.exceptions.MissingSchema,
    requests.exceptions.InvalidSchema,
)


def _get_expected_status(test_case: Dict[str, Any]) -> Any:
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
    rule: str, response_data: Any, status_code: int, response_headers: Any = None
) -> Tuple[bool, str, bool]:
    """
    Evaluate one free-text validation rule as best as mechanically possible.

    Understands status-code rules ("status code is 200"), quoted field
    checks ("field 'price' is present", "'id' must be an integer"), and
    header checks ("header 'Content-Encoding' is 'gzip'") — header rules are
    verified against the response headers, never the body, and header VALUES
    are never printed. Rules that cannot be checked mechanically are flagged
    verifiable=False so the UI can distinguish them from real passes — they
    never count as passed checks.
    """
    lowered = rule.lower()

    # Status-code rules: "status code is 200", "returns 404 on missing resource"
    if "status" in lowered:
        match = re.search(r"\b(\d{3})\b", rule)
        if match:
            code = int(match.group(1))
            return status_code == code, f"Expected HTTP {code}, got {status_code}", True

    # Quoted field names: "id", 'price'
    fields = re.findall(r"['\"]([A-Za-z_][\w.\-]*)['\"]", rule)

    # Header-targeted rules: check the response headers, not the body. The
    # first quoted token is the header name; a second quoted token after
    # "equals" is the expected value. Values are secrets-adjacent — compared
    # quietly, never printed.
    if fields and "header" in lowered:
        try:
            header_map = {str(k).lower(): v for k, v in (response_headers or {}).items()}
        except Exception:
            header_map = None
        if header_map is not None:
            name = fields[0]
            if name.lower() not in header_map:
                return False, f"header '{name}' MISSING", True
            detail = f"header '{name}' present"
            if "equals" in lowered and len(fields) > 1:
                if str(header_map[name.lower()]) == fields[1]:
                    detail += ", value matches"
                else:
                    return False, detail + ", value differs", True
            return True, detail, True

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
        return all_ok, "; ".join(details), True

    return False, "Rule not mechanically verifiable — manual review recommended", False


def _is_sensitive_header(name: Any) -> bool:
    lowered = str(name).lower()
    return any(hint in lowered for hint in _SENSITIVE_HEADER_HINTS)


def _build_request(
    test_case: Dict[str, Any],
    endpoint: str,
    method: str,
    headers: Optional[Dict[str, Any]],
    body: Optional[Dict[str, Any]],
) -> Tuple[str, str, Dict[str, Any], Any, List[str]]:
    """
    Build the HTTP request for a test case — the same request its export
    would send. Cases carry their own ``request`` object; it wins over the
    form for body and (per category) headers:

      - positive / assertion: form headers first, then case headers on top
        (so a positive test runs with the user's real credentials)
      - negative / edge: form headers minus sensitive/auth headers, then
        case headers on top — the case is authoritative for auth
      - a case header with an empty-string value means "omit this header"

    A case with no ``request`` at all falls back to a generic per-category
    mutation; the returned notes say so explicitly.

    Returns (url, method, headers, body, fallback_notes).
    """
    tc_request = test_case.get("request")
    if not isinstance(tc_request, dict):
        tc_request = None

    if tc_request:
        url = tc_request.get("endpoint") or endpoint
        req_method = (tc_request.get("method") or method or "GET").upper()

        category = test_case.get("category", "positive")
        if category in ("negative", "edge"):
            # The case is authoritative for auth: "missing Authorization"
            # really sends none. Start from the form headers minus secrets.
            req_headers = {
                k: v for k, v in (headers or {}).items() if not _is_sensitive_header(k)
            }
        else:
            req_headers = dict(headers or {})
        for key, value in (tc_request.get("headers") or {}).items():
            if value == "":
                # Explicit "omit this header"
                req_headers.pop(key, None)
            elif (
                category in ("negative", "edge")
                or not _is_sensitive_header(key)
                or not any(k.lower() == key.lower() for k in (headers or {}))
            ):
                req_headers[key] = value
            # else: a positive/assertion case tried to overwrite a credential
            # header the form already supplies (the AI often writes a fake
            # token into the case). The user's real credentials win.

        if "body" in tc_request:
            # The case body wins even when it is None or {} — deliberate.
            # Deep-copy: mutating it (fallback edge cases) must never change
            # the stored test case or the form body shared across the suite.
            req_body = copy.deepcopy(tc_request["body"])
        else:
            req_body = copy.deepcopy(body)
        return url, req_method, req_headers, req_body, []

    # --- fallback: no scenario request in this case ---
    url = endpoint
    req_method = (method or "GET").upper()
    req_headers = dict(headers or {})
    req_body = copy.deepcopy(body)

    category = test_case.get("category", "positive")
    notes: List[str] = []

    if category == "negative":
        mutated = False
        for key in list(req_headers):
            if "authorization" in key.lower():
                req_headers[key] = "Bearer invalid-token-000"
                notes.append(f"replaced {key} with an invalid token")
                mutated = True
        if isinstance(req_body, dict) and req_body:
            removed = next(iter(req_body))
            req_body = {k: v for k, v in req_body.items() if k != removed}
            notes.append(f"removed required field '{removed}' from the body")
            mutated = True
        if not mutated:
            req_headers["Content-Type"] = "text/plain"
            notes.append("sent wrong Content-Type: text/plain")

    elif category == "edge":
        if isinstance(req_body, dict) and req_body:
            boundary_values = ["", "A" * 10000, None, "special !@#$%^&*<>'\"` chars"]
            labels = ["emptied", "set to max length (10000 chars)", "set to null",
                      "set to special characters"]
            for i, key in enumerate(list(req_body)):
                choice = i % len(boundary_values)
                req_body[key] = boundary_values[choice]
                notes.append(f"field '{key}' {labels[choice]}")
        else:
            notes.append("no request body to mutate; sent as-is")

    return url, req_method, req_headers, req_body, notes


def _describe_request(
    method: str, url: str, req_headers: Dict[str, Any], req_body: Any
) -> str:
    """One masked info line about what was actually sent: header NAMES only
    (sensitive ones marked), never header values or body contents."""
    if req_headers:
        names = [
            f"{key} (masked)" if _is_sensitive_header(key) else str(key)
            for key in req_headers
        ]
        header_part = f"headers: {', '.join(names)}"
    else:
        header_part = "no headers"
    body_part = "no body" if req_body is None else "body sent"
    return f"{method} {url} · {header_part} · {body_part}"


def execute_test_case(
    test_case: Dict[str, Any],
    endpoint: str,
    method: str,
    headers: Optional[Dict[str, Any]] = None,
    body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Execute a single test case against the live API.

    Returns a structured result dict; never raises. Each result carries an
    ``outcome`` (passed / failed / needs_review / not_run / skipped) alongside
    the boolean ``passed`` kept for compatibility.
    """
    skip = test_case.get("skip")
    if skip:
        # Never sent: a skipped case (e.g. a login test on a public endpoint)
        # would always fail, so the API is never contacted.
        reason = skip.get("reason") if isinstance(skip, dict) else str(skip)
        return {
            "test_case_id": str(test_case.get("id", "")),
            "title": test_case.get("title") or test_case.get("rule") or "Untitled",
            "passed": False,
            "outcome": "skipped",
            "expected_status": _get_expected_status(test_case),
            "actual_status": None,
            "actual_response_preview": "",
            "assertion_results": [],
            "error_message": reason or "This test was skipped.",
            "duration_ms": 0,
            "category": test_case.get("category", "positive"),
        }

    category = test_case.get("category", "positive")
    expected_status = _get_expected_status(test_case)
    expected_codes = _normalize_expected_status(expected_status)
    assertion_results: List[Dict[str, Any]] = []
    error_message: Optional[str] = None
    actual_status = 0
    preview = ""
    outcome = "failed"
    url = endpoint
    req_method = (method or "GET").upper()

    start = time.perf_counter()
    try:
        url, req_method, req_headers, req_body, fallback_notes = _build_request(
            test_case, endpoint, method, headers, body
        )
        assertion_results.append({
            "kind": "info",
            "assertion": "Request sent",
            "detail": _describe_request(req_method, url, req_headers, req_body),
        })
        for note in fallback_notes:
            assertion_results.append({
                "kind": "info",
                "assertion": "Request sent",
                "detail": f"No scenario request in this case; applied a generic "
                          f"{category} change: {note}",
            })

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

        if expected_codes:
            joined = " or ".join(str(c) for c in expected_codes)
            assertion_results.append({
                "assertion": f"Status code is {joined}",
                "passed": actual_status in expected_codes,
                "detail": f"Expected HTTP {joined}, got {actual_status}",
            })

        expected = test_case.get("expected") or {}
        for rule in expected.get("validation_rules") or []:
            ok, detail, verifiable = _run_assertion_rule(
                rule, response_data, actual_status, response_headers=response.headers
            )
            assertion_results.append({
                "assertion": rule, "passed": ok, "detail": detail, "verifiable": verifiable,
            })

        # Cases from the assertions list carry their own free-text rule
        if test_case.get("rule"):
            ok, detail, verifiable = _run_assertion_rule(
                test_case["rule"], response_data, actual_status,
                response_headers=response.headers,
            )
            assertion_results.append({
                "assertion": test_case["rule"], "passed": ok, "detail": detail,
                "verifiable": verifiable,
            })

    except BlockedURLError as e:
        # The SSRF guard blocked the URL before anything was sent.
        outcome = "not_run"
        error_message = f"Not sent: the SSRF guard blocked this URL, so the API was never tested. ({e})"
    except _CLIENT_REFUSAL_ERRORS as e:
        # The HTTP client refused to send (bad header, bad URL scheme).
        outcome = "not_run"
        error_message = (
            f"Not sent: the HTTP client refused the request ({e}), "
            f"so the API was never tested."
        )
    except requests.exceptions.Timeout:
        error_message = f"Request timed out after {REQUEST_TIMEOUT_SECONDS}s"
    except requests.exceptions.RequestException as e:
        error_message = f"Request failed: {e}"
    except Exception as e:  # never crash on malformed test case data
        error_message = f"Execution error: {e}"

    duration_ms = int((time.perf_counter() - start) * 1000)

    if error_message is None:
        # Info entries (what was sent) are not checks and never count.
        checks = [a for a in assertion_results if a.get("kind") != "info"]
        verifiable_checks = [a for a in checks if a.get("verifiable", True)]
        unverifiable = [a for a in checks if a.get("verifiable") is False]
        if any(not a["passed"] for a in verifiable_checks):
            outcome = "failed"
        elif verifiable_checks:
            outcome = "passed"
        elif unverifiable:
            # Everything checkable-by-nothing: honest "needs review".
            outcome = "needs_review"
        else:
            # No checks at all: a successful response counts as a pass.
            outcome = "passed" if 200 <= actual_status < 400 else "failed"

    return {
        "test_case_id": str(test_case.get("id", "")),
        "title": test_case.get("title") or test_case.get("rule") or "Untitled",
        "passed": outcome == "passed",
        "outcome": outcome,
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
) -> Dict[str, Any]:
    """Execute a list of test cases sequentially.

    Returns the results plus an outcome summary. pass_rate counts only
    passed vs failed — needs_review and not_run are honest non-answers,
    not failures.
    """
    results = [
        execute_test_case(tc, endpoint, method, headers=headers, body=body)
        for tc in test_cases
    ]
    passed = sum(1 for r in results if r["outcome"] == "passed")
    failed = sum(1 for r in results if r["outcome"] == "failed")
    needs_review = sum(1 for r in results if r["outcome"] == "needs_review")
    not_run = sum(1 for r in results if r["outcome"] == "not_run")
    skipped = sum(1 for r in results if r["outcome"] == "skipped")
    decided = passed + failed
    return {
        "results": results,
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "needs_review": needs_review,
        "not_run": not_run,
        "skipped_count": skipped,
        "executed_count": len(results) - not_run - skipped,
        "duration_ms": sum(r.get("duration_ms", 0) for r in results),
        "pass_rate": f"{round(passed / decided * 100)}%" if decided else "0%",
    }
