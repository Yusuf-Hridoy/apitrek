"""
OWASP API Security Top 10 (2023) scanner.

Deterministic — NO LLM calls. Generates safe, non-destructive security
tests for the applicable OWASP categories and executes them against the
target API. All payloads are benign: SSRF/redirect probes reference
localhost only, and no destructive operations are performed.

AUTHORIZED TESTING ONLY: run these checks only against APIs you own or
have explicit written permission to test.
"""
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

from core.url_guard import safe_request, BlockedURLError

REQUEST_TIMEOUT_SECONDS = 15
RESPONSE_PREVIEW_LENGTH = 500
RATE_LIMIT_ATTEMPTS = 15

OWASP_CATEGORIES = {
    "API1:2023": "Broken Object Level Authorization",
    "API2:2023": "Broken Authentication",
    "API5:2023": "Broken Function Level Authorization",
    "API6:2023": "Unrestricted Access to Sensitive Business Flows",
    "API7:2023": "Server Side Request Forgery",
    "API8:2023": "Security Misconfiguration",
    "API10:2023": "Unsafe Consumption of APIs",
}

OWASP_DESCRIPTIONS = {
    "API1:2023": "Attackers access other users' objects by tampering with IDs in the request.",
    "API2:2023": "Authentication mechanisms are weak, misconfigured, or missing.",
    "API5:2023": "Regular users can invoke administrative or privileged functions.",
    "API6:2023": "Sensitive flows (signup, purchase, OTP) lack rate limiting or automation protection.",
    "API7:2023": "The API fetches attacker-supplied URLs, reaching internal resources.",
    "API8:2023": "Verbose errors, missing security headers, or insecure defaults expose the stack.",
    "API10:2023": "The API trusts data from third-party services without validation.",
}

SEVERITY_WEIGHTS = {"Critical": 25, "High": 15, "Medium": 8, "Low": 3}

# Finding verdicts. "Needs Review" = the probe ran but cannot be auto-judged
# (e.g. SSRF without an out-of-band listener). It is NOT counted as a
# vulnerability in the risk score, but it is surfaced to the user so they can
# verify manually instead of being given a false all-clear.
FINDING_VULNERABLE = "Vulnerable"
FINDING_SECURE = "Secure"
FINDING_NEEDS_REVIEW = "Needs Review"
FINDING_ERROR = "Error"

_SSRF_SIGNALS = (
    "connection refused",
    "econnrefused",
    "failed to connect",
    "no route to host",
    "connection timed out",
    "could not resolve",
    "127.0.0.1",
    "localhost",
)

ERROR_DISCLOSURE_PATTERNS = (
    "traceback",
    "stack trace",
    "nullpointerexception",
    "syntax error",
    "unhandled exception",
    "internal server error at line",
    "warning: mysql",
    "django",
)

RECOMMENDED_SECURITY_HEADERS = (
    "x-frame-options",
    "content-security-policy",
    "x-content-type-options",
    "strict-transport-security",
)

_URL_PARAM_HINTS = (
    "url", "uri", "link", "callback", "redirect", "webhook",
    "image", "img", "feed", "target", "dest", "next",
)


def _make_test(
    test_id: str,
    category_key: str,
    severity: str,
    title: str,
    description: str,
    payload: Dict[str, Any],
    expected_status: int,
    remediation: str,
) -> Dict[str, Any]:
    return {
        "id": test_id,
        "owasp_category": f"{category_key} - {OWASP_CATEGORIES[category_key]}",
        "severity": severity,
        "title": title,
        "description": description,
        "payload": payload,
        "expected_status": expected_status,
        "remediation": remediation,
    }


def _numeric_path_id(endpoint: str) -> Optional[str]:
    match = re.search(r"/(\d+)(?:[/?#]|$)", endpoint)
    return match.group(1) if match else None


def _replace_path_id(endpoint: str, old_id: str, new_id: str) -> str:
    return re.sub(rf"/{re.escape(old_id)}(?=[/?#]|$)", f"/{new_id}", endpoint, count=1)


def _find_url_params(
    endpoint: str, body: Optional[Dict[str, Any]]
) -> List[Dict[str, str]]:
    """Locate URL-ish parameters (query or body) that could be SSRF/redirect targets."""
    found: List[Dict[str, str]] = []
    for key, _value in parse_qsl(urlparse(endpoint).query):
        if any(hint in key.lower() for hint in _URL_PARAM_HINTS):
            found.append({"location": "query", "name": key})
    if isinstance(body, dict):
        for key, value in body.items():
            looks_urlish = any(hint in key.lower() for hint in _URL_PARAM_HINTS)
            holds_url = isinstance(value, str) and value.startswith(("http://", "https://"))
            if looks_urlish or holds_url:
                found.append({"location": "body", "name": key})
    return found


def _admin_variant(endpoint: str) -> str:
    """Insert an /admin segment before the path for BFLA probing."""
    parts = urlparse(endpoint)
    path = parts.path if parts.path.startswith("/") else f"/{parts.path}"
    return urlunparse(parts._replace(path=f"/admin{path}"))


def generate_security_tests(
    endpoint: str,
    method: str,
    headers: Optional[Dict[str, Any]] = None,
    body: Optional[Dict[str, Any]] = None,
    sample_response: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Generate deterministic OWASP security tests applicable to this endpoint."""
    method = (method or "GET").upper()
    tests: List[Dict[str, Any]] = []

    # --- API1:2023 Broken Object Level Authorization ---
    object_id = _numeric_path_id(endpoint)
    if object_id:
        for i, probe_id in enumerate((str(int(object_id) + 1), "99999"), start=1):
            tests.append(_make_test(
                f"SEC-API1-{i:02d}", "API1:2023", "Critical",
                f"Access object ID {probe_id} with current credentials",
                f"Request the endpoint with the object ID swapped from {object_id} to "
                f"{probe_id}. If another user's object is returned, object-level "
                f"authorization is broken.",
                {"modified_endpoint": _replace_path_id(endpoint, object_id, probe_id)},
                403,
                "Verify ownership of every object server-side on each request; "
                "never rely on client-supplied IDs alone. Return 403/404 for objects "
                "the caller does not own.",
            ))

    # --- API2:2023 Broken Authentication ---
    tests.append(_make_test(
        "SEC-API2-01", "API2:2023", "Critical",
        "Request without authentication token",
        "Send the request with the Authorization header removed. The API must "
        "reject unauthenticated access to protected resources.",
        {"remove_headers": ["Authorization"]},
        401,
        "Require authentication on all non-public endpoints and return 401 "
        "when credentials are missing.",
    ))
    tests.append(_make_test(
        "SEC-API2-02", "API2:2023", "High",
        "Request with an invalid token",
        "Send a garbage bearer token. The API must reject tokens that fail "
        "signature or format validation.",
        {"set_headers": {"Authorization": "Bearer invalid.token.value"}},
        401,
        "Validate token signature, expiry, and issuer on every request.",
    ))
    tests.append(_make_test(
        "SEC-API2-03", "API2:2023", "Medium",
        "Malformed Authorization header",
        "Use a wrong auth scheme and malformed header value. The API must not "
        "crash or accept it.",
        {"set_headers": {"Authorization": "NotBearer ##%%"}},
        401,
        "Strictly parse the Authorization header; reject unknown schemes with 401.",
    ))

    # --- API5:2023 Broken Function Level Authorization ---
    tests.append(_make_test(
        "SEC-API5-01", "API5:2023", "High",
        "Probe administrative endpoint with user credentials",
        "Request an /admin variant of the endpoint with the current (non-admin) "
        "credentials. A 2xx is a concrete positive; 401/403 is a concrete "
        "denial; a 404 only means the guessed path likely does not exist.",
        {"modified_endpoint": _admin_variant(endpoint)},
        403,
        "Enforce role-based access control server-side for every administrative "
        "function; deny by default. For a stronger test, supply a real "
        "privileged endpoint path.",
    ))
    if method == "GET":
        tests.append(_make_test(
            "SEC-API5-02", "API5:2023", "Medium",
            "HTTP method override to DELETE",
            "Send DELETE to a read-only endpoint. Unsupported methods must be "
            "rejected, not silently honored.",
            {"method_override": "DELETE"},
            405,
            "Explicitly allow-list HTTP methods per route and return 405 for "
            "everything else.",
        ))

    # --- API6:2023 Unrestricted Access to Sensitive Business Flows ---
    tests.append(_make_test(
        "SEC-API6-01", "API6:2023", "Medium",
        "Rapid sequential requests (rate limiting)",
        f"Send {RATE_LIMIT_ATTEMPTS} requests in quick succession. A 429 confirms "
        "throttling; absence of 429 only means the probe burst did not exceed "
        "the limit and must be verified manually.",
        {"repeat": RATE_LIMIT_ATTEMPTS},
        429,
        "Apply rate limiting per API key/IP and return 429 with Retry-After "
        "when limits are exceeded.",
    ))
    if method in ("POST", "PUT", "PATCH"):
        tests.append(_make_test(
            "SEC-API6-02", "API6:2023", "Low",
            "Duplicate request (idempotency)",
            "Send the same write request twice. The API should detect and reject "
            "or safely deduplicate replays.",
            {"repeat": 2},
            409,
            "Support idempotency keys on write endpoints so retried or duplicated "
            "requests cannot create duplicate effects.",
        ))

    # --- API7:2023 Server Side Request Forgery (localhost probes only) ---
    for i, param in enumerate(_find_url_params(endpoint, body)[:2], start=1):
        tests.append(_make_test(
            f"SEC-API7-{i:02d}", "API7:2023", "High",
            f"SSRF probe via '{param['name']}' parameter (localhost)",
            f"Set the '{param['name']}' parameter to a loopback address. Automated "
            f"detection is best-effort; confirm real SSRF with an out-of-band "
            f"listener (e.g. interactsh or Burp Collaborator).",
            {"set_param": {**param, "value": "http://127.0.0.1/"}},
            400,
            "Validate and allow-list outbound URLs; block loopback, link-local, "
            "and private IP ranges; fetch remote content through an egress proxy. "
            "Confirm serious findings with an out-of-band callback service.",
        ))

    # --- API8:2023 Security Misconfiguration ---
    tests.append(_make_test(
        "SEC-API8-01", "API8:2023", "Medium",
        "Verbose error disclosure",
        "Send a malformed JSON body and inspect the error response for stack "
        "traces, framework banners, or internal details.",
        {"check": "error_disclosure", "raw_body": "{invalid json,,"},
        400,
        "Return generic error messages to clients; log stack traces server-side "
        "only. Disable debug mode in production.",
    ))
    tests.append(_make_test(
        "SEC-API8-02", "API8:2023", "Medium",
        "Missing HTTP security headers",
        "Check the response for recommended security headers "
        "(X-Frame-Options, Content-Security-Policy, X-Content-Type-Options, HSTS).",
        {"check": "security_headers"},
        200,
        "Set X-Frame-Options/Content-Security-Policy/X-Content-Type-Options and "
        "Strict-Transport-Security on all responses.",
    ))

    # --- API10:2023 Unsafe Consumption of APIs (redirect probes, localhost only) ---
    for i, param in enumerate(_find_url_params(endpoint, body)[:1], start=1):
        tests.append(_make_test(
            f"SEC-API10-{i:02d}", "API10:2023", "Low",
            f"Redirect target validation for '{param['name']}'",
            f"Supply a loopback redirect target in '{param['name']}'. The API must "
            f"validate redirect/forward targets instead of trusting them blindly.",
            {"set_param": {**param, "value": "http://localhost/redirect"}},
            400,
            "Validate redirect targets against an allow-list and reject untrusted "
            "destinations before following them.",
        ))

    return tests


def _apply_query_param(url: str, name: str, value: str) -> str:
    parts = urlparse(url)
    query = dict(parse_qsl(parts.query))
    query[name] = value
    return urlunparse(parts._replace(query=urlencode(query)))


def _baseline_response(
    endpoint: str,
    method: str,
    headers: Optional[Dict[str, Any]],
    body: Optional[Dict[str, Any]],
) -> Optional[requests.Response]:
    """Fetch the endpoint WITHOUT any injected param, for differential compare."""
    try:
        return safe_request(
            (method or "GET").upper(),
            endpoint,
            headers=headers or {},
            json=body if body is not None and (method or "GET").upper() != "GET" else None,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except Exception:
        return None


def execute_security_test(
    test: Dict[str, Any],
    endpoint: str,
    method: str,
    headers: Optional[Dict[str, Any]] = None,
    body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Execute one security test and classify the result.

    Finding logic:
      - expected 401/403 but got 200  -> Vulnerable
      - expected 401/403 and got it   -> Secure
      - actual 5xx                    -> Error (investigate)
      - actual == expected otherwise  -> Secure
    Custom checks (error disclosure, security headers) inspect the response
    content instead of the status code.
    """
    payload = test.get("payload", {})
    expected = test.get("expected_status")

    url = payload.get("modified_endpoint") or endpoint
    req_method = payload.get("method_override") or (method or "GET").upper()
    req_headers = dict(headers or {})
    for name in payload.get("remove_headers", []):
        req_headers = {k: v for k, v in req_headers.items() if k.lower() != name.lower()}
    req_headers.update(payload.get("set_headers", {}))
    req_body = body if body is not None else None
    if "set_body" in payload:
        req_body = payload["set_body"]

    set_param = payload.get("set_param")
    if set_param:
        if set_param.get("location") == "query":
            url = _apply_query_param(url, set_param["name"], set_param["value"])
        else:
            req_body = dict(req_body or {})
            req_body[set_param["name"]] = set_param["value"]

    repeat = int(payload.get("repeat", 1))
    check = payload.get("check")

    responses: List[requests.Response] = []
    error_message: Optional[str] = None
    start = time.perf_counter()
    try:
        for _ in range(repeat):
            if payload.get("raw_body") is not None:
                responses.append(safe_request(
                    req_method, url,
                    headers={**req_headers, "Content-Type": "application/json"},
                    data=payload["raw_body"],
                    timeout=REQUEST_TIMEOUT_SECONDS,
                ))
            else:
                responses.append(safe_request(
                    req_method, url,
                    headers=req_headers,
                    json=req_body if req_body is not None and req_method != "GET" else None,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                ))
    except BlockedURLError as e:
        error_message = f"Request blocked: {e}"
    except requests.exceptions.Timeout:
        error_message = f"Request timed out after {REQUEST_TIMEOUT_SECONDS}s"
    except requests.exceptions.RequestException as e:
        error_message = f"Request failed: {e}"
    duration_ms = int((time.perf_counter() - start) * 1000)

    if error_message is not None or not responses:
        return {
            "test_case_id": str(test.get("id", "")),
            "owasp_category": test.get("owasp_category", ""),
            "severity": test.get("severity", ""),
            "title": test.get("title", ""),
            "vulnerable": False,
            "actual_status": 0,
            "actual_response_preview": "",
            "finding": FINDING_ERROR,
            "finding_reason": "",
            "remediation": test.get("remediation", ""),
            "payload_used": payload,
            "duration_ms": duration_ms,
            "error_message": error_message or "No response received",
        }

    last = responses[-1]
    actual_status = last.status_code
    preview = last.text[:RESPONSE_PREVIEW_LENGTH]

    finding = FINDING_SECURE
    finding_reason = ""
    test_id = str(test.get("id", ""))

    if test_id.startswith(("SEC-API7", "SEC-API10")):
        body_l = last.text.lower()
        baseline = _baseline_response(endpoint, method, headers, body)
        base_l = baseline.text.lower() if baseline is not None else ""
        new_signals = [s for s in _SSRF_SIGNALS if s in body_l and s not in base_l]
        if new_signals:
            finding = FINDING_VULNERABLE
            finding_reason = (
                f"Response shows internal-fetch evidence: {', '.join(new_signals[:3])}."
            )
        elif (
            actual_status in (400, 422)
            and baseline is not None
            and baseline.status_code < 400
        ):
            finding = FINDING_SECURE
            finding_reason = "Target rejected the injected URL parameter."
        elif actual_status >= 500:
            finding = FINDING_NEEDS_REVIEW
            finding_reason = (
                "Server error on SSRF probe; may indicate an attempted internal fetch. "
                "Verify out-of-band."
            )
        else:
            finding = FINDING_NEEDS_REVIEW
            finding_reason = (
                "SSRF probe sent; no automated signal. Confirm with an out-of-band "
                "listener (e.g. interactsh/Burp Collaborator)."
            )
    elif test_id == "SEC-API5-01":
        if 200 <= actual_status < 300:
            finding = FINDING_VULNERABLE
            finding_reason = "Reached an /admin path with non-admin credentials."
        elif actual_status in (401, 403):
            finding = FINDING_SECURE
            finding_reason = "Admin path access was denied."
        else:
            finding = FINDING_NEEDS_REVIEW
            finding_reason = (
                f"Guessed /admin path returned {actual_status}; the path likely does "
                "not exist. Supply a real privileged endpoint to test BFLA properly."
            )
    elif test_id == "SEC-API6-01":
        throttled = any(r.status_code == 429 for r in responses)
        if throttled:
            finding = FINDING_SECURE
            finding_reason = f"Throttling observed (429) within {len(responses)} requests."
        else:
            finding = FINDING_NEEDS_REVIEW
            finding_reason = (
                f"No 429 in {len(responses)} rapid requests. This does NOT confirm "
                "missing rate limiting — the limit may exceed the probe burst."
            )
    elif actual_status >= 500:
        finding = FINDING_ERROR
        finding_reason = "The API returned a server error."
    elif check == "security_headers":
        present = {k.lower() for k in last.headers.keys()}
        missing = [h for h in RECOMMENDED_SECURITY_HEADERS if h not in present]
        finding = FINDING_VULNERABLE if missing else FINDING_SECURE
        finding_reason = (
            f"Missing headers: {', '.join(missing)}" if missing else "All recommended security headers present."
        )
    elif check == "error_disclosure":
        leaked = [p for p in ERROR_DISCLOSURE_PATTERNS if p in last.text.lower()]
        finding = FINDING_VULNERABLE if leaked else FINDING_SECURE
        finding_reason = (
            f"Disclosed: {', '.join(leaked)}" if leaked else "No verbose error disclosure detected."
        )
    elif expected in (401, 403):
        if actual_status == 200:
            finding = FINDING_VULNERABLE
            finding_reason = "Protected resource returned 200 without valid credentials."
        elif actual_status in (401, 403):
            finding = FINDING_SECURE
            finding_reason = "Request was properly rejected."
        else:
            finding = FINDING_SECURE if actual_status >= 400 else FINDING_VULNERABLE
            finding_reason = "Unexpected response status."
    elif expected is not None:
        if actual_status == expected:
            finding = FINDING_SECURE
            finding_reason = "Response matched expected status."
        elif expected >= 400 and actual_status < 400:
            finding = FINDING_VULNERABLE
            finding_reason = "Endpoint accepted input that should have been rejected."
        else:
            finding = FINDING_SECURE
            finding_reason = "Response did not match expected status."
    else:
        finding = FINDING_SECURE
        finding_reason = "No negative signal observed."

    return {
        "test_case_id": str(test.get("id", "")),
        "owasp_category": test.get("owasp_category", ""),
        "severity": test.get("severity", ""),
        "title": test.get("title", ""),
        "vulnerable": finding == FINDING_VULNERABLE,
        "actual_status": actual_status,
        "actual_response_preview": preview,
        "finding": finding,
        "finding_reason": finding_reason,
        "remediation": test.get("remediation", ""),
        "payload_used": payload,
        "duration_ms": duration_ms,
        "error_message": None,
    }


def calculate_risk_score(findings: List[Dict[str, Any]]) -> int:
    """Sum severity weights of vulnerable findings (Critical=25, High=15,
    Medium=8, Low=3), capped at 100."""
    score = sum(
        SEVERITY_WEIGHTS.get(f.get("severity", ""), 0)
        for f in findings
        if f.get("vulnerable")
    )
    return min(score, 100)
