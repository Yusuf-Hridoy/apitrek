"""
Unit tests for the core.security_scanner module.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
import requests
from core.security_scanner import (
    OWASP_CATEGORIES,
    calculate_risk_score,
    execute_security_test,
    generate_security_tests,
)

ENDPOINT = "https://api.example.com/users/1"


def _resp(status=200, text="{}", headers=None):
    response = MagicMock()
    response.status_code = status
    response.text = text
    response.headers = headers or {}
    return response


# --- generate_security_tests ---

def test_generates_bola_tests_for_numeric_id():
    tests = generate_security_tests(ENDPOINT, "GET")
    bola = [t for t in tests if t["owasp_category"].startswith("API1:2023")]
    assert len(bola) == 2
    urls = {t["payload"]["modified_endpoint"] for t in bola}
    assert "https://api.example.com/users/2" in urls
    assert "https://api.example.com/users/99999" in urls
    assert all(t["severity"] == "Critical" for t in bola)


def test_no_bola_tests_without_numeric_id():
    tests = generate_security_tests("https://api.example.com/profile", "GET")
    assert not [t for t in tests if t["owasp_category"].startswith("API1:2023")]


def test_always_generates_auth_and_misconfig_tests():
    tests = generate_security_tests(ENDPOINT, "GET")
    categories = {t["owasp_category"].split(" - ")[0] for t in tests}
    assert {"API2:2023", "API5:2023", "API6:2023", "API8:2023"} <= categories


def test_method_override_only_for_get():
    get_tests = generate_security_tests(ENDPOINT, "GET")
    assert any(t["payload"].get("method_override") == "DELETE" for t in get_tests)
    post_tests = generate_security_tests(ENDPOINT, "POST")
    assert not any(t["payload"].get("method_override") for t in post_tests)


def test_idempotency_test_only_for_write_methods():
    post_tests = generate_security_tests(ENDPOINT, "POST", body={"a": 1})
    assert any(t["id"] == "SEC-API6-02" for t in post_tests)
    get_tests = generate_security_tests(ENDPOINT, "GET")
    assert not any(t["id"] == "SEC-API6-02" for t in get_tests)


def test_ssrf_tests_only_for_urlish_params():
    plain = generate_security_tests(ENDPOINT, "GET")
    assert not [t for t in plain if t["owasp_category"].startswith("API7:2023")]

    with_param = generate_security_tests(
        "https://api.example.com/fetch?url=https://x.com", "GET"
    )
    ssrf = [t for t in with_param if t["owasp_category"].startswith("API7:2023")]
    assert ssrf
    # Probes must be safe: localhost references only
    assert all("127.0.0.1" in t["payload"]["set_param"]["value"] for t in ssrf)


def test_ssrf_detects_body_url_fields():
    tests = generate_security_tests(
        "https://api.example.com/import", "POST", body={"webhook": "https://x.com/hook"}
    )
    ssrf = [t for t in tests if t["owasp_category"].startswith("API7:2023")]
    assert ssrf and ssrf[0]["payload"]["set_param"]["location"] == "body"


def test_all_tests_have_required_shape():
    for t in generate_security_tests(ENDPOINT, "GET"):
        assert set(t) == {
            "id", "owasp_category", "severity", "title", "description",
            "payload", "expected_status", "remediation",
        }
        assert t["severity"] in ("Critical", "High", "Medium", "Low")


# --- execute_security_test ---

@patch("core.security_scanner.safe_request")
def test_vulnerable_when_protected_endpoint_returns_200(mock_request):
    mock_request.return_value = _resp(status=200, text='{"data": "secret"}')
    test = {
        "id": "SEC-API2-01", "owasp_category": "API2:2023 - Broken Authentication",
        "severity": "Critical", "title": "No token", "payload": {"remove_headers": ["Authorization"]},
        "expected_status": 401, "remediation": "fix",
    }
    result = execute_security_test(test, ENDPOINT, "GET",
                                   headers={"Authorization": "Bearer real"})
    assert result["finding"] == "Vulnerable"
    assert result["vulnerable"] is True
    assert result["actual_status"] == 200
    # Authorization header was removed from the outgoing request
    _, kwargs = mock_request.call_args
    assert "Authorization" not in kwargs["headers"]


@patch("core.security_scanner.safe_request")
def test_secure_when_unauthorized_returned(mock_request):
    mock_request.return_value = _resp(status=401)
    test = {
        "id": "SEC-API2-02", "owasp_category": "API2:2023 - Broken Authentication",
        "severity": "High", "title": "Bad token",
        "payload": {"set_headers": {"Authorization": "Bearer invalid.token.value"}},
        "expected_status": 401, "remediation": "fix",
    }
    result = execute_security_test(test, ENDPOINT, "GET")
    assert result["finding"] == "Secure"
    assert result["vulnerable"] is False


@patch("core.security_scanner.safe_request")
def test_error_on_server_error(mock_request):
    mock_request.return_value = _resp(status=500)
    test = {
        "id": "T", "owasp_category": "API2:2023 - Broken Authentication",
        "severity": "High", "title": "t", "payload": {},
        "expected_status": 401, "remediation": "fix",
    }
    result = execute_security_test(test, ENDPOINT, "GET")
    assert result["finding"] == "Error"
    assert result["vulnerable"] is False


@patch("core.security_scanner.safe_request")
def test_network_failure_returns_error_finding(mock_request):
    mock_request.side_effect = requests.exceptions.ConnectionError("down")
    test = {"id": "T", "owasp_category": "API8:2023 - Security Misconfiguration",
            "severity": "Low", "title": "t", "payload": {}, "expected_status": 200,
            "remediation": "fix"}
    result = execute_security_test(test, ENDPOINT, "GET")
    assert result["finding"] == "Error"
    assert result["error_message"]
    assert result["actual_status"] == 0


@patch("core.security_scanner.safe_request")
def test_rate_limit_secure_when_any_429(mock_request):
    mock_request.side_effect = [_resp(status=200) for _ in range(9)] + [_resp(status=429)]
    test = {"id": "SEC-API6-01", "owasp_category": "API6:2023 - ...", "severity": "Medium",
            "title": "rate", "payload": {"repeat": 10}, "expected_status": 429, "remediation": "fix"}
    result = execute_security_test(test, ENDPOINT, "GET")
    assert result["finding"] == "Secure"
    assert mock_request.call_count == 10


@patch("core.security_scanner.safe_request")
def test_rate_limit_vulnerable_when_never_throttled(mock_request):
    mock_request.return_value = _resp(status=200)
    test = {"id": "SEC-API6-01", "owasp_category": "API6:2023 - ...", "severity": "Medium",
            "title": "rate", "payload": {"repeat": 10}, "expected_status": 429, "remediation": "fix"}
    result = execute_security_test(test, ENDPOINT, "GET")
    assert result["finding"] == "Vulnerable"


@patch("core.security_scanner.safe_request")
def test_security_headers_check(mock_request):
    mock_request.return_value = _resp(status=200, headers={"x-frame-options": "DENY"})
    test = {"id": "SEC-API8-02", "owasp_category": "API8:2023 - ...", "severity": "Medium",
            "title": "headers", "payload": {"check": "security_headers"},
            "expected_status": 200, "remediation": "fix"}
    result = execute_security_test(test, ENDPOINT, "GET")
    assert result["finding"] == "Vulnerable"  # most headers missing

    mock_request.return_value = _resp(status=200, headers={
        "X-Frame-Options": "DENY",
        "Content-Security-Policy": "default-src 'self'",
        "X-Content-Type-Options": "nosniff",
        "Strict-Transport-Security": "max-age=63072000",
    })
    result = execute_security_test(test, ENDPOINT, "GET")
    assert result["finding"] == "Secure"


@patch("core.security_scanner.safe_request")
def test_error_disclosure_check(mock_request):
    mock_request.return_value = _resp(status=400, text="Traceback (most recent call last): ...")
    test = {"id": "SEC-API8-01", "owasp_category": "API8:2023 - ...", "severity": "Medium",
            "title": "disclosure", "payload": {"check": "error_disclosure", "raw_body": "{bad,,"},
            "expected_status": 400, "remediation": "fix"}
    result = execute_security_test(test, ENDPOINT, "POST")
    assert result["finding"] == "Vulnerable"
    # Raw malformed body was sent as data
    _, kwargs = mock_request.call_args
    assert kwargs["data"] == "{bad,,"


@patch("core.security_scanner.safe_request")
def test_ssrf_query_param_applied(mock_request):
    mock_request.return_value = _resp(status=400)
    test = {"id": "SEC-API7-01", "owasp_category": "API7:2023 - ...", "severity": "High",
            "title": "ssrf",
            "payload": {"set_param": {"location": "query", "name": "url", "value": "http://127.0.0.1/"}},
            "expected_status": 400, "remediation": "fix"}
    execute_security_test(test, "https://api.example.com/fetch?url=https://x.com", "GET")
    args, _ = mock_request.call_args
    assert "url=http%3A%2F%2F127.0.0.1%2F" in args[1]


# --- calculate_risk_score ---

def _finding(severity, vulnerable=True):
    return {"severity": severity, "vulnerable": vulnerable}


def test_risk_score_weights():
    findings = [_finding("Critical"), _finding("High"), _finding("Medium"), _finding("Low")]
    assert calculate_risk_score(findings) == 25 + 15 + 8 + 3


def test_risk_score_ignores_secure_findings():
    findings = [_finding("Critical", vulnerable=False), _finding("Low")]
    assert calculate_risk_score(findings) == 3


def test_risk_score_capped_at_100():
    findings = [_finding("Critical") for _ in range(10)]
    assert calculate_risk_score(findings) == 100


def test_risk_score_empty():
    assert calculate_risk_score([]) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
