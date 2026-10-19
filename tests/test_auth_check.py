"""
Unit tests for the shared auth-state classifier (core/auth_check) and the
auth-check behavior of the generate route (web/routes/generate.py).
"""
import copy
import ipaddress
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from core.auth_check import (
    AUTH_STATES,
    classify_auth,
    credential_header_names,
    is_auth_test,
)


# --- classify_auth -----------------------------------------------------------

def test_all_five_states_exist():
    assert AUTH_STATES == ("public", "authenticated", "rejected", "required", "unknown")


def test_public_when_2xx_without_credentials():
    result = classify_auth(None, 200)
    assert result["state"] == "public"
    assert result["status"] == 200
    assert result["credential_headers"] == []


def test_public_with_only_benign_headers():
    result = classify_auth({"Content-Type": "application/json", "Accept": "*/*"}, 200)
    assert result["state"] == "public"
    assert result["credential_headers"] == []


def test_authenticated_with_authorization_header():
    result = classify_auth({"Authorization": "Bearer x"}, 200)
    assert result["state"] == "authenticated"
    assert result["credential_headers"] == ["Authorization"]


def test_authenticated_with_cookie_only():
    result = classify_auth({"Cookie": "token=abc123"}, 200)
    assert result["state"] == "authenticated"
    assert result["credential_headers"] == ["Cookie"]


def test_authenticated_with_api_key_only():
    result = classify_auth({"X-Api-Key": "secret-key"}, 200)
    assert result["state"] == "authenticated"
    assert result["credential_headers"] == ["X-Api-Key"]


def test_lowercase_header_names_match():
    result = classify_auth({"authorization": "Bearer x", "cookie": "a=b"}, 200)
    assert result["state"] == "authenticated"
    assert set(result["credential_headers"]) == {"authorization", "cookie"}


def test_rejected_on_401_with_credentials():
    result = classify_auth({"Authorization": "Bearer bad"}, 401)
    assert result["state"] == "rejected"
    assert result["status"] == 401


def test_rejected_on_403_with_cookie():
    # Restful Booker returns 403 for a bad token — 401 and 403 mean the same here.
    result = classify_auth({"Cookie": "token=abc123"}, 403)
    assert result["state"] == "rejected"
    assert result["status"] == 403


def test_required_on_401_without_credentials():
    result = classify_auth(None, 401)
    assert result["state"] == "required"


def test_required_on_403_without_credentials():
    result = classify_auth({"Accept": "application/json"}, 403)
    assert result["state"] == "required"
    assert result["credential_headers"] == []


def test_unknown_on_404():
    assert classify_auth(None, 404)["state"] == "unknown"
    assert classify_auth({"Authorization": "Bearer x"}, 404)["state"] == "unknown"


def test_unknown_on_400_and_redirects():
    assert classify_auth(None, 400)["state"] == "unknown"
    assert classify_auth(None, 302)["state"] == "unknown"


def test_unknown_when_no_response():
    assert classify_auth(None, None)["state"] == "unknown"
    assert classify_auth({"Cookie": "a=b"}, None)["state"] == "unknown"


def test_names_only_never_values():
    names = credential_header_names({"Cookie": "topsecret-value", "Accept": "*/*"})
    assert names == ["Cookie"]
    assert "topsecret-value" not in json.dumps(names)


# --- is_auth_test ------------------------------------------------------------

def test_is_auth_test_variants():
    assert is_auth_test({"expected": {"status_code": 401}})
    assert is_auth_test({"expected": {"status_code": "403"}})
    assert is_auth_test({"expected": {"status_code": [401, 403]}})
    assert is_auth_test({"expected_status": 401})
    assert not is_auth_test({"expected": {"status_code": [200, 401]}})
    assert not is_auth_test({"expected": {"status_code": 400}})
    assert not is_auth_test({"expected": {}})
    assert not is_auth_test({})
    assert not is_auth_test({"expected": {"status_code": None}})


# --- generate route ----------------------------------------------------------

from fastapi.testclient import TestClient  # noqa: E402
from web.app import app  # noqa: E402
from core.live_fetcher import LiveFetchError  # noqa: E402


# Route tests use public-looking URLs whose hosts never resolve — stub the
# guard's DNS lookup so the suite runs fully offline (same pattern as
# tests/test_rate_limit.py).
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


@pytest.fixture
def client():
    app.state.limiter._limiter.storage.reset()
    return TestClient(app)


GENERATED = {
    "positive_test_cases": [],
    "negative_test_cases": [
        {"id": "TC-NEG-01", "title": "Missing token is rejected",
         "expected": {"status_code": 401}},
        {"id": "TC-NEG-02", "title": "Bad body is a 400",
         "expected": {"status_code": 400}},
    ],
    "edge_cases": [],
    "assertions": [],
}


def _post_generate(client, fetch_result, headers=None, extra=None):
    captured = {}

    def _fake_generate(**kwargs):
        captured.update(kwargs)
        return copy.deepcopy(GENERATED)

    payload = {
        "endpoint": "https://api.example.com/users/1",
        "method": "GET",
        "auto_fetch": True,
        "headers": headers,
    }
    payload.update(extra or {})
    with patch("web.routes.generate.fetch_with_status", return_value=fetch_result), \
         patch("web.routes.generate.generate_test_cases", side_effect=_fake_generate) as gen_mock, \
         patch("web.routes.generate.save_session", return_value=1), \
         patch("web.routes.generate.save_test_cases"):
        res = client.post("/generate-tests", json=payload)
    return res, captured, gen_mock


def test_route_public_marks_login_tests_skipped(client):
    res, captured, _ = _post_generate(client, {"status": 200, "body": {"uuid": "x"}})
    assert res.status_code == 200
    body = res.json()
    assert body["auth_check"]["state"] == "public"
    assert body["auth_check"]["status"] == 200
    assert body["auth_check"]["credential_headers"] == []

    # The generator was told the state and given the real sample response.
    assert captured["auth_state"]["state"] == "public"
    assert captured["sample_response"] == {"uuid": "x"}

    cases = {c["id"]: c for c in body["negative_test_cases"]}
    assert cases["TC-NEG-01"]["skip"]["reason"] == (
        "No login needed here, so a test expecting 401 or 403 can't pass."
    )
    assert "skip" not in cases["TC-NEG-02"]


def test_route_rejected_cookie_403_generation_continues(client):
    res, captured, _ = _post_generate(
        client,
        {"status": 403, "body": {"error": "Forbidden"}},
        headers={"Cookie": "token=abc123"},
        extra={"stability_check": True},
    )
    # 401/403 no longer stops generation with a 502.
    assert res.status_code == 200
    body = res.json()
    assert body["auth_check"]["state"] == "rejected"
    assert body["auth_check"]["status"] == 403
    assert body["auth_check"]["credential_headers"] == ["Cookie"]

    # An error body is not the endpoint's real response.
    assert captured["sample_response"] is None
    assert "sample_response" not in body

    # Stability wasn't checked because the first request was rejected.
    assert body["stability"]["ran"] is False
    assert "rejected" in body["stability"]["reason"]

    # The credential VALUE never appears in the response.
    assert "abc123" not in json.dumps(body)


def test_route_required_when_401_without_credentials(client):
    res, captured, _ = _post_generate(client, {"status": 401, "body": None})
    assert res.status_code == 200
    assert res.json()["auth_check"]["state"] == "required"
    assert captured["auth_state"]["state"] == "required"


def test_route_authenticated_with_authorization(client):
    res, captured, _ = _post_generate(
        client,
        {"status": 200, "body": {"id": 1}},
        headers={"Authorization": "Bearer realtoken999"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["auth_check"]["state"] == "authenticated"
    assert body["auth_check"]["credential_headers"] == ["Authorization"]
    assert "realtoken999" not in json.dumps(body)


def test_route_auto_fetch_off_has_no_auth_check(client):
    def _fake_generate(**kwargs):
        return copy.deepcopy(GENERATED)

    with patch("web.routes.generate.generate_test_cases", side_effect=_fake_generate), \
         patch("web.routes.generate.save_session", return_value=1), \
         patch("web.routes.generate.save_test_cases"):
        res = client.post("/generate-tests", json={
            "endpoint": "https://api.example.com/users/1",
            "method": "GET",
            "auto_fetch": False,
            "sample_response": {"id": 1},
        })
    assert res.status_code == 200
    assert "auth_check" not in res.json()


def test_route_fetch_hard_failure_still_502(client):
    def _boom(**kwargs):
        raise LiveFetchError("The API returned a server error (status 500).")

    def _fake_generate(**kwargs):
        return copy.deepcopy(GENERATED)

    with patch("web.routes.generate.fetch_with_status", side_effect=_boom), \
         patch("web.routes.generate.generate_test_cases", side_effect=_fake_generate), \
         patch("web.routes.generate.save_session", return_value=1), \
         patch("web.routes.generate.save_test_cases"):
        res = client.post("/generate-tests", json={
            "endpoint": "https://api.example.com/users/1",
            "method": "GET",
            "auto_fetch": True,
        })
    assert res.status_code == 502


def test_route_saves_functional_session_with_mode(client):
    save_mock = MagicMock(return_value=1)

    def _fake_generate(**kwargs):
        return copy.deepcopy(GENERATED)

    with patch("web.routes.generate.fetch_with_status",
               return_value={"status": 200, "body": {"id": 1}}), \
         patch("web.routes.generate.generate_test_cases", side_effect=_fake_generate), \
         patch("web.routes.generate.save_session", save_mock), \
         patch("web.routes.generate.save_test_cases"):
        res = client.post("/generate-tests", json={
            "endpoint": "https://api.example.com/users/1",
            "method": "GET",
            "auto_fetch": True,
        })
    assert res.status_code == 200
    assert save_mock.call_count == 1
    # Explicitly functional — never lands in the security history bucket.
    assert save_mock.call_args.kwargs["mode"] == "functional"
    assert "_session_id" in res.json()


def test_route_failed_case_save_leaves_no_ghost_session(client):
    """If the test cases can't be saved, the empty session row is dropped
    instead of reloading later as a misleading '0 tests' entry."""

    def _fake_generate(**kwargs):
        return copy.deepcopy(GENERATED)

    with patch("web.routes.generate.fetch_with_status",
               return_value={"status": 200, "body": {"id": 1}}), \
         patch("web.routes.generate.generate_test_cases", side_effect=_fake_generate), \
         patch("web.routes.generate.save_session", return_value=42), \
         patch("web.routes.generate.save_test_cases", side_effect=RuntimeError("db locked")), \
         patch("web.routes.generate.delete_session") as delete_mock:
        res = client.post("/generate-tests", json={
            "endpoint": "https://api.example.com/users/1",
            "method": "GET",
            "auto_fetch": True,
        })
    assert res.status_code == 200
    delete_mock.assert_called_once_with(42)
    assert "_session_id" not in res.json()


# --- prompt content ----------------------------------------------------------

from llm.prompt_templates import build_user_prompt  # noqa: E402


def test_prompt_public_state_text():
    prompt = build_user_prompt(
        "https://api.example.com/x", "GET",
        auth_state={"state": "public", "status": 200, "credential_headers": []},
    )
    assert "returned success without any credentials" in prompt
    assert "Do not generate test cases that expect 401 or 403" in prompt


def test_prompt_authenticated_names_only():
    prompt = build_user_prompt(
        "https://api.example.com/x", "GET",
        auth_state={"state": "authenticated", "status": 200,
                    "credential_headers": ["Authorization", "X-Api-Key"]},
    )
    assert "credentials sent in these headers: Authorization, X-Api-Key" in prompt
    assert "Include negative tests for missing and invalid credentials" in prompt


def test_prompt_rejected_mentions_status_and_names():
    prompt = build_user_prompt(
        "https://api.example.com/x", "GET",
        auth_state={"state": "rejected", "status": 403,
                    "credential_headers": ["Cookie"]},
    )
    assert "rejected with status 403" in prompt
    assert "these headers (Cookie)" in prompt


def test_prompt_tells_ai_the_real_login_header_and_status():
    """Cookie/403 login: the AI must not write Authorization/401 tests."""
    prompt = build_user_prompt(
        "https://api.example.com/x", "GET",
        auth_state={"state": "rejected", "status": 403,
                    "credential_headers": ["Cookie"]},
    )
    assert "Cookie" in prompt
    assert "403" in prompt
    # It names the real header and status instead of the 401/Authorization default.
    assert "target them in negative auth tests, not `Authorization`" in prompt
    assert "expect 403, not 401" in prompt


def test_prompt_required_names_status_not_401():
    prompt = build_user_prompt(
        "https://api.example.com/x", "GET",
        auth_state={"state": "required", "status": 401, "credential_headers": []},
    )
    assert "expect 401" in prompt  # 401 IS the observed status here
    prompt_403 = build_user_prompt(
        "https://api.example.com/x", "GET",
        auth_state={"state": "required", "status": 403, "credential_headers": []},
    )
    assert "A request without credentials returns 403" in prompt_403


def test_prompt_required_mentions_status():
    prompt = build_user_prompt(
        "https://api.example.com/x", "GET",
        auth_state={"state": "required", "status": 401, "credential_headers": []},
    )
    assert "rejected a request with no credentials (status 401)" in prompt
    assert "missing credentials expecting 401" in prompt


def test_prompt_unknown_or_none_adds_nothing():
    base = build_user_prompt("https://api.example.com/x", "GET")
    unknown = build_user_prompt(
        "https://api.example.com/x", "GET",
        auth_state={"state": "unknown", "status": 404, "credential_headers": []},
    )
    assert "AUTHENTICATION CONTEXT" not in base
    assert "AUTHENTICATION CONTEXT" not in unknown


def test_prompt_never_contains_header_value():
    # The classifier only returns names; even if a value is in the headers,
    # only the name may reach the prompt.
    auth_state = classify_auth({"Cookie": "SUPER_SECRET_TOKEN_VALUE"}, 403)
    prompt = build_user_prompt("https://api.example.com/x", "GET", auth_state=auth_state)
    assert "Cookie" in prompt
    assert "SUPER_SECRET_TOKEN_VALUE" not in prompt


def test_generate_test_cases_passes_auth_state_to_ai():
    from unittest.mock import MagicMock
    from core.generator import generate_test_cases

    mock_client = MagicMock()
    mock_client.send_prompt.return_value = json.dumps({
        "positive_test_cases": [],
        "negative_test_cases": [],
        "edge_cases": [],
        "assertions": [],
    })
    auth_state = classify_auth({"Cookie": "SUPER_SECRET_TOKEN_VALUE"}, 403)
    generate_test_cases(
        "https://api.example.com/x", "GET",
        mistral_client=mock_client, auth_state=auth_state,
    )
    prompt = mock_client.send_prompt.call_args.kwargs["user_prompt"]
    assert "rejected with status 403" in prompt
    assert "Cookie" in prompt
    assert "SUPER_SECRET_TOKEN_VALUE" not in prompt


# --- history round-trip ------------------------------------------------------

def test_history_round_trip_keeps_auth_check_and_skip(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    from core import database

    database.init_db()
    auth_check = {"state": "public", "status": 200, "credential_headers": []}
    session_id = database.save_session(
        endpoint="https://api.example.com/uuid",
        method="GET",
        auth_check=auth_check,
    )
    database.save_test_cases(session_id, [
        {
            "category": "negative",
            "title": "Missing token is rejected",
            "expected_status": 401,
            "payload": {"endpoint": "https://api.example.com/uuid", "method": "GET",
                        "skip": {"reason": "No login needed here, so a test expecting 401 or 403 can't pass."}},
            "case_ref": "TC-NEG-01",
        },
    ])

    session = database.get_session(session_id)
    assert session["auth_check"] == auth_check
    (case,) = session["test_cases"]
    assert case["payload"]["skip"]["reason"].startswith("No login needed")


def test_session_saved_before_migration_loads_without_banner(tmp_path, monkeypatch):
    """A row inserted by an old schema (no auth_check_json) still loads."""
    import sqlite3
    db_path = tmp_path / "old.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("""
            CREATE TABLE sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                endpoint TEXT NOT NULL,
                method TEXT NOT NULL DEFAULT 'GET',
                headers_json TEXT,
                body_json TEXT,
                sample_response_json TEXT,
                created_at TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'functional'
            )
        """)
        conn.execute(
            "INSERT INTO sessions (endpoint, method, created_at) VALUES (?, ?, ?)",
            ("https://api.example.com/x", "GET", "2026-01-01T00:00:00+00:00"),
        )
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    from core import database

    database.init_db()  # migrates: adds auth_check_json
    session = database.get_session(1)
    assert session["endpoint"] == "https://api.example.com/x"
    assert session["auth_check"] is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
