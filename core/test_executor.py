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
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from core.assertion_rules import (
    MULTI_CASE_STATUS_REASON,
    resolve_field_path,
    rule_checks_value,
    rule_field_and_value,
    rule_implies_absence,
    status_code_rule,
    unwrap_field_path,
    values_equal,
)
from core.auth_check import _normalize_expected_status, is_auth_test
from core.database import _SENSITIVE_HEADER_HINTS, _is_sensitive_body_key
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


def _run_assertion_rule(
    rule: str, response_data: Any, status_code: int, response_headers: Any = None
) -> Tuple[bool, str, bool]:
    """
    Evaluate one free-text validation rule as best as mechanically possible.

    Shares its rule interpretation with the export translator
    (``core.assertion_rules``): the first dotted path (or quoted name) is the
    field or header, a quoted token after an equals-style phrase is the
    expected VALUE (never a field name), and negation means the field or
    header must be ABSENT. A ``response.``/``body.``/``json.`` wrapper is
    tried as written and then stripped, and ``headers['User-Agent']`` reads
    as a header path. A VALUE comparison ("equals 'alice'") on a field the
    response doesn't contain is verifiable=False — Needs Review, not a
    failure — while a presence rule on a missing field stays a fail. Header
    VALUES are never printed. Rules that cannot be checked mechanically
    (including one rule describing several different cases) are flagged
    verifiable=False so the UI can distinguish them from real passes — they
    never count as passed checks.
    """
    rule = str(rule)
    lowered = rule.lower()

    # Status-code rules first: "status code is 200", "must return HTTP 200",
    # "responds with 201". Recognized even without the word "status".
    status_rule = status_code_rule(rule)
    if status_rule is not None:
        if status_rule["multi_case"]:
            # "200 on success; 401 on auth failure" — one run can't decide it.
            return False, MULTI_CASE_STATUS_REASON, False
        codes = status_rule["codes"]
        joined = " or ".join(str(c) for c in codes)
        return status_code in codes, f"Expected HTTP {joined}, got {status_code}", True

    target, expected_value = rule_field_and_value(rule)
    if target is None:
        return False, "Rule not mechanically verifiable — manual review recommended", False
    wants_absent = rule_implies_absence(rule)

    # "response.headers['User-Agent']" names a header inside a path.
    unwrapped = unwrap_field_path(target) or target
    segments = unwrapped.split(".")
    header_path = len(segments) > 1 and segments[0].lower() == "headers"

    def _value_repr(value: Any, redact: bool = False) -> str:
        # Compare quietly; truncate long values. A header value, and any
        # credential-shaped field, prints its type rather than its content.
        if redact or _is_sensitive_body_key(str(target).split(".")[-1]):
            return f"<{type(value).__name__}>"
        text = repr(value)
        return text if len(text) <= 80 else text[:77] + "..."

    def _check_header(name: str):
        """Check one header against the real response headers, or None when
        the response carried no readable headers."""
        try:
            header_map = {str(k).lower(): v for k, v in (response_headers or {}).items()}
        except Exception:
            return None
        present = str(name).lower() in header_map
        if wants_absent:
            if present:
                return False, f"header '{name}' present, expected absent", True
            return True, f"header '{name}' absent", True
        if not present:
            return False, f"header '{name}' MISSING", True
        detail = f"header '{name}' present"
        if expected_value is not None:
            actual = header_map[str(name).lower()]
            if values_equal(actual, expected_value):
                return True, detail + ", value matches", True
            return False, (
                detail + f", value differs (expected {expected_value!r}, "
                f"got {_value_repr(actual, redact=True)})"
            ), True
        return True, detail, True

    def _check_field(found: bool, value: Any, redact: bool = False):
        if wants_absent:
            if found:
                return False, f"field '{target}' present, expected absent", True
            return True, f"field '{target}' absent", True
        if not found:
            if rule_checks_value(rule, expected_value):
                # A value comparison on a field that isn't there has nothing
                # to compare — not a failure, just unverifiable (Needs
                # Review): the rule may assume a field the request never sent.
                return False, (
                    f"field '{target}' isn't in the response, so its value can't be "
                    "checked — the test may assume a field that wasn't sent"
                ), False
            return False, f"field '{target}' MISSING", True
        if expected_value is not None:
            if values_equal(value, expected_value):
                return True, f"field '{target}' value matches", True
            return False, (
                f"field '{target}' value differs (expected {expected_value!r}, "
                f"got {_value_repr(value, redact)})"
            ), True
        detail = f"field '{target}' present"
        for word in _TYPE_WORDS:
            if word in lowered:
                if _type_matches(value, word):
                    return True, detail + f", type {word} OK", True
                return False, (
                    detail + f", expected type {word} (got {type(value).__name__})"
                ), True
        return True, detail, True

    if header_path:
        # Plenty of APIs (httpbin and friends) echo the request headers in
        # the body — look there first, then at the real response headers.
        found, value = resolve_field_path(response_data, target)
        if found:
            return _check_field(True, value, redact=True)
        result = _check_header(".".join(segments[1:]))
        if result is not None:
            return result
        return _check_field(False, None, redact=True)

    # Header-targeted rules: check the response headers, not the body.
    if "header" in lowered:
        result = _check_header(target)
        if result is not None:
            return result

    # Field rules: dotted paths resolve through nested objects/lists.
    found, value = resolve_field_path(response_data, target)
    return _check_field(found, value)


def _is_sensitive_header(name: Any) -> bool:
    lowered = str(name).lower()
    return any(hint in lowered for hint in _SENSITIVE_HEADER_HINTS)


# Words that mark a negative/edge test as being ABOUT the login itself.
_LOGIN_TEST_WORDS = (
    "auth", "login", "logged", "token", "cookie", "session", "credential",
    "jwt", "bearer", "api key", "apikey", "unauthorized", "forbidden",
    "expired", "signature",
)


def _is_login_test(test_case: Dict[str, Any]) -> bool:
    """True when this case exists to test the login itself.

    A login test is a negative or edge case that either expects only 401/403
    (``core.auth_check.is_auth_test``) or says in its title/description that
    the credential is what it attacks ("Missing session cookie", "Expired
    token"). Such a test must send the broken or missing credential it
    describes; every other test keeps the user's real one.
    """
    if not isinstance(test_case, dict):
        return False
    if test_case.get("category", "positive") not in ("negative", "edge"):
        return False
    if is_auth_test(test_case):
        return True
    text = (
        f"{test_case.get('title') or ''} {test_case.get('description') or ''}"
    ).lower()
    return any(word in text for word in _LOGIN_TEST_WORDS)


def _build_request(
    test_case: Dict[str, Any],
    endpoint: str,
    method: str,
    headers: Optional[Dict[str, Any]],
    body: Optional[Dict[str, Any]],
) -> Tuple[str, str, Dict[str, Any], Any, List[str], List[str]]:
    """
    Build the HTTP request for a test case — the same request its export
    would send. Cases carry their own ``request`` object; it wins over the
    form for the body, and case headers apply on top of the form headers.
    How credential headers are treated depends on what the test is FOR:

      - a LOGIN test (``_is_login_test``: a negative/edge case about the
        login itself) starts from the form headers MINUS the credentials and
        then applies every case header as written — a missing, expired or
        malformed credential IS the test, so sending the user's real login
        instead would make it impossible to fail. A case with no credential
        header sends none; a case header set to "" omits that header.
      - every OTHER test (all positives, assertions, and negatives/edges
        about validation rather than login) keeps the form's real credential:
        a case header that carries a credential is the AI guessing and is
        IGNORED, so a fabricated ``token=valid123`` is never sent. Any other
        case header applies on top of the form's, and "" omits it.

    A case with no ``request`` at all falls back to a generic per-category
    mutation; the returned notes say so explicitly.

    Returns (url, method, headers, body, fallback_notes, test_credentials),
    where test_credentials names the credential headers that came from the
    test case (values from the test, not the user's login) so the info line
    can say so without printing them.
    """
    tc_request = test_case.get("request")
    if not isinstance(tc_request, dict):
        tc_request = None

    if tc_request:
        url = tc_request.get("endpoint") or endpoint
        req_method = (tc_request.get("method") or method or "GET").upper()
        case_headers = tc_request.get("headers") or {}
        test_credentials: List[str] = []

        if _is_login_test(test_case):
            # The credential is what this test attacks: drop the real one and
            # send exactly what the case describes (possibly nothing).
            req_headers = {
                key: value
                for key, value in (headers or {}).items()
                if not _is_sensitive_header(key)
            }
            for key, value in case_headers.items():
                if value == "":
                    # Explicit "omit this header"
                    req_headers.pop(key, None)
                    continue
                req_headers[key] = value
                if _is_sensitive_header(key):
                    test_credentials.append(str(key))
        else:
            req_headers = dict(headers or {})
            for key, value in case_headers.items():
                if _is_sensitive_header(key):
                    # The AI often writes a fabricated credential into the
                    # case. On a test that isn't about the login, the user's
                    # real credential wins; an invented one (with or without
                    # a real counterpart) is never sent.
                    continue
                if value == "":
                    # Explicit "omit this header"
                    req_headers.pop(key, None)
                else:
                    req_headers[key] = value

        if "body" in tc_request:
            # The case body wins even when it is None or {} — deliberate.
            # Deep-copy: mutating it (fallback edge cases) must never change
            # the stored test case or the form body shared across the suite.
            req_body = copy.deepcopy(tc_request["body"])
        else:
            req_body = copy.deepcopy(body)
        return url, req_method, req_headers, req_body, [], test_credentials

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

    return url, req_method, req_headers, req_body, notes, []


def _describe_request(
    method: str,
    url: str,
    req_headers: Dict[str, Any],
    req_body: Any,
    test_credentials: Optional[List[str]] = None,
) -> str:
    """One masked info line about what was actually sent: header NAMES only
    (sensitive ones marked), never header values or body contents.

    A credential that came from the test case itself (a login test sending a
    missing, expired or malformed credential) is labelled as such, so nobody
    reads the line as "my real login was sent". The value is still never
    shown. When a login test sends no credential at all, none is listed."""
    labelled = {str(name) for name in (test_credentials or [])}
    if req_headers:
        names = []
        for key in req_headers:
            if str(key) in labelled:
                names.append(f"{key} (test value, not your login)")
            elif _is_sensitive_header(key):
                names.append(f"{key} (masked)")
            else:
                names.append(str(key))
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
        (url, req_method, req_headers, req_body, fallback_notes,
         test_credentials) = _build_request(
            test_case, endpoint, method, headers, body
        )
        assertion_results.append({
            "kind": "info",
            "assertion": "Request sent",
            "detail": _describe_request(
                req_method, url, req_headers, req_body, test_credentials
            ),
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
