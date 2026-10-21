"""
History reload must rebuild the LIVE view, not just the form:

- functional: per-assertion check detail (verified / unverified / failed),
  needs-review outcomes, and the stability summary all round-trip, so the
  scorecard, the needs-review count, and the stability strip match the run;
- security: the scan response (findings, summary, risk score, not-applicable)
  round-trips so the findings view renders on load;
- assertion cases get stable ids — without them every assertion result is
  stored under an empty id and only one survives the reload.
"""
import sys
import ipaddress
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from fastapi.testclient import TestClient

from web.app import app

FAKE_GENERATE = {
    "positive_test_cases": [
        {"id": "TC-POS-01", "title": "ok", "request": {"method": "GET"},
         "expected": {"status_code": 200}},
    ],
    "negative_test_cases": [],
    "edge_cases": [],
    "assertions": [
        {"category": "schema", "rule": "field 'id' is present", "severity": "high"},
        {"category": "schema", "rule": "field 'name' is a string", "severity": "medium"},
    ],
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "roundtrip.db"))
    with TestClient(app) as c:
        yield c


# Route tests target public-looking URLs whose hosts don't need to resolve —
# stub the guard's DNS lookup so they run fully offline (same approach as
# tests/test_security_scanner.py).
@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    from core import url_guard
    real_resolve = url_guard._resolve_all_ips

    def _resolve(host, port):
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return ["93.184.216.34"]
        return real_resolve(host, port)

    monkeypatch.setattr("core.url_guard._resolve_all_ips", _resolve)


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    # Route tests post repeatedly; the shared in-memory limiter must not
    # leak counts between tests (same pattern as tests/test_security_scanner.py).
    app.state.limiter._limiter.storage.reset()


# --- functional: stability + assertion detail round-trip --------------------


def test_stability_summary_round_trips(client):
    stability = {
        "ran": True, "calls": 3,
        "summary": {"stable": 4, "dynamic": 1, "flaky": 1},
        "fields": {"id": {"status": "stable"}},
    }
    with patch("web.routes.generate.generate_test_cases",
               return_value=dict(FAKE_GENERATE)):
        r = client.post("/generate-tests", json={
            "endpoint": "https://api.example.com/users/1", "method": "GET",
        })
    session_id = r.json()["_session_id"]

    # Simulate the generate route persisting stability (it does when the
    # stability check ran; here we set it directly to isolate the round-trip).
    from core.database import _connect
    import json as _json
    with _connect() as conn:
        conn.execute("UPDATE sessions SET stability_json = ? WHERE id = ?",
                     (_json.dumps(stability), session_id))

    s = client.get(f"/api/history/sessions/{session_id}").json()
    assert s["stability"]["ran"] is True
    assert s["stability"]["summary"]["flaky"] == 1


def test_assertion_results_with_needs_review_round_trip(client):
    from core import database

    session_id = database.save_session(
        endpoint="https://api.example.com/x", method="GET", client_id="browser-1")
    database.save_test_cases(session_id, [
        {"category": "assertion", "title": "field 'x' is present",
         "case_ref": "AST-01", "payload": {"grounded": False}},
    ])
    database.save_execution_results(session_id, [{
        "test_case_id": "AST-01", "passed": False, "outcome": "needs_review",
        "actual_status": 200,
        "assertion_results": [
            {"assertion": "field 'x' is present", "passed": False,
             "detail": "field 'x' MISSING", "verifiable": False},
        ],
    }])

    s = database.get_session(session_id, client_id="browser-1")
    result = s["execution_results"][0]
    assert result["outcome"] == "needs_review"
    checks = result["assertion_results"]
    assert len(checks) == 1
    assert checks[0]["verifiable"] is False
    # Everything the scorecard and needs-review count need is present.


def test_generate_assigns_stable_assertion_ids(client):
    """Without ids, every assertion result is stored under '' and reload
    keeps only the last one. The generate route must assign stable ids and
    persist them as case_ref."""
    with patch("web.routes.generate.generate_test_cases",
               return_value=dict(FAKE_GENERATE)):
        r = client.post("/generate-tests", json={
            "endpoint": "https://api.example.com/users/1", "method": "GET",
        })
    data = r.json()
    ids = [a["id"] for a in data["assertions"]]
    assert ids == ["AST-01", "AST-02"]

    session_id = data["_session_id"]
    s = client.get(f"/api/history/sessions/{session_id}").json()
    assertion_rows = [tc for tc in s["test_cases"] if tc["category"] == "assertion"]
    assert [row["case_ref"] for row in assertion_rows] == ["AST-01", "AST-02"]


def test_live_run_results_match_by_assertion_id(client):
    """A live run stores results keyed by the assertion id, so a reload can
    match every result back to its card."""
    with patch("web.routes.generate.generate_test_cases",
               return_value=dict(FAKE_GENERATE)), \
         patch("core.test_executor.safe_request") as mock_req:
        mock_req.return_value = MagicMock(
            status_code=200, text='{"id": 1}',
            **{"json.return_value": {"id": 1, "name": "x"}})
        r = client.post("/generate-tests", json={
            "endpoint": "https://api.example.com/users/1", "method": "GET",
        })
        session_id = r.json()["_session_id"]
        for tc in r.json()["assertions"]:
            res = client.post("/api/execute-single", json={
                "endpoint": "https://api.example.com/users/1", "method": "GET",
                "test_case": {**tc, "category": "assertion"},
                "session_id": session_id,
            })
            assert res.status_code == 200
            assert res.json()["test_case_id"] == tc["id"]

    s = client.get(f"/api/history/sessions/{session_id}").json()
    stored_ids = sorted(r["test_case_id"] for r in s["execution_results"])
    assert stored_ids == ["AST-01", "AST-02"]


# --- security: scan response round-trip --------------------------------------


def test_security_scan_response_round_trips(client):
    finding = {
        "finding": "Vulnerable", "vulnerable": True, "severity": "High",
        "title": "No rate limiting",
        "owasp_category": "API6:2023 - Unrestricted Resource Consumption",
        "payload_used": {"set_headers": {"Authorization": "Bearer SECRETTok"}},
        "actual_status": 200, "finding_reason": "15/15 repeats succeeded",
        "test_case_id": "SEC-API6-01", "duration_ms": 5,
    }
    with patch("web.routes.security.detect_auth_context",
               return_value={"state": "authenticated", "status": 200,
                             "credential_headers": ["Authorization"],
                             "endpoint_is_public": False, "baseline_status": 200}), \
         patch("web.routes.security.execute_security_test",
               return_value=dict(finding)), \
         patch("web.routes.security.generate_security_tests",
               return_value=[{"owasp_category": "API6:2023 - x", "payload": {}}]), \
         patch("web.routes.security.not_applicable_categories",
               return_value=[{"category": "API1:2023", "reason": "no object ids"}]):
        r = client.post("/api/security/scan",
                        json={"endpoint": "https://x.com/users", "method": "GET"})
    assert r.status_code == 200
    session_id = r.json()["_session_id"]

    s = client.get(f"/api/history/sessions/{session_id}").json()
    assert s["mode"] == "security"
    scan = s["scan"]
    assert len(scan["findings"]) == 1
    assert scan["findings"][0]["finding"] == "Vulnerable"
    assert scan["risk_score"] == r.json()["risk_score"]
    assert scan["not_applicable"][0]["category"] == "API1:2023"
    assert scan["summary"]["auth_check"]["state"] == "authenticated"
    # Probe payloads are redacted before they reach disk.
    stored_header = scan["findings"][0]["payload_used"]["set_headers"]["Authorization"]
    assert stored_header == "***REDACTED***"
    assert "SECRETTok" not in str(scan)


def test_security_scan_saves_with_browser_scope(client):
    with patch("web.routes.security.detect_auth_context",
               return_value={"state": "public", "status": 200,
                             "credential_headers": [],
                             "endpoint_is_public": True, "baseline_status": 200}), \
         patch("web.routes.security.execute_security_test",
               return_value={"finding": "Secure", "vulnerable": False,
                             "severity": "Low", "title": "t",
                             "owasp_category": "API8:2023 - x",
                             "payload_used": {}, "duration_ms": 1}):
        r = client.post("/api/security/scan",
                        json={"endpoint": "https://x.com/users", "method": "GET"})
    assert r.status_code == 200

    listed = client.get("/api/history/sessions").json()["sessions"]
    assert len(listed) == 1
    assert listed[0]["mode"] == "security"

    # Another browser can't open it.
    other = TestClient(app)
    assert other.get(f"/api/history/sessions/{listed[0]['id']}").status_code == 404


# --- reliable saves -----------------------------------------------------------


def test_execute_results_surface_failed_save(client):
    """A save failure is reported, not silently dropped."""
    from core import database
    session_id = database.save_session(
        endpoint="https://api.example.com/x", method="GET", client_id="browser-1")
    database.delete_session(session_id)  # session id now points at nothing

    with patch("core.test_executor.safe_request") as mock_req:
        mock_req.return_value = MagicMock(
            status_code=200, text="{}", **{"json.return_value": {}})
        r = client.post("/api/execute-single", json={
            "endpoint": "https://api.example.com/x", "method": "GET",
            "test_case": {"id": "TC-1", "title": "t",
                          "expected": {"status_code": 200}},
            "session_id": session_id,
        })
    assert r.status_code == 200
    assert r.json()["history_saved"] is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
