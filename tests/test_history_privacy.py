"""
History privacy: sessions are scoped to the browser that created them via
the anonymous apitrek_sid cookie. One visitor must never list, open, rerun,
or delete another visitor's sessions, and ownerless legacy rows belong to
nobody — they never appear in anyone's list.
"""
import sys
import tempfile
import ipaddress
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from fastapi.testclient import TestClient

from web.app import app

FAKE_GENERATE = {
    "positive_test_cases": [
        {"id": "TC-POS-01", "title": "t", "description": "d",
         "request": {"method": "GET"}, "expected": {"status_code": 200}},
    ],
    "negative_test_cases": [],
    "edge_cases": [],
    "assertions": [{"category": "status_code", "rule": "200 OK", "severity": "high"}],
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "privacy.db"))
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


def _generate(client):
    with patch("web.routes.generate.generate_test_cases",
               return_value=dict(FAKE_GENERATE)):
        r = client.post("/generate-tests", json={
            "endpoint": "https://api.example.com/users/1", "method": "GET",
        })
    assert r.status_code == 200
    return r.json()["_session_id"]


def test_first_history_request_sets_cookie_and_empty_list(client):
    r = client.get("/api/history/sessions")
    assert r.status_code == 200
    assert r.json()["sessions"] == []
    # A brand-new browser gets its own id: HttpOnly, long-lived, lax.
    set_cookie = r.headers["set-cookie"]
    assert "apitrek_sid=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie
    assert "Max-Age=31536000" in set_cookie


def test_two_browsers_see_only_their_own_sessions(client):
    sid_a = _generate(client)
    cookie_a = client.cookies.get("apitrek_sid")
    assert cookie_a

    # A second browser (its own cookie jar) creates its own session.
    client_b = TestClient(app)
    sid_b = _generate(client_b)
    cookie_b = client_b.cookies.get("apitrek_sid")
    assert cookie_b and cookie_b != cookie_a

    listed_a = [s["id"] for s in client.get("/api/history/sessions").json()["sessions"]]
    listed_b = [s["id"] for s in client_b.get("/api/history/sessions").json()["sessions"]]
    assert listed_a == [sid_a]
    assert listed_b == [sid_b]


def test_cannot_open_another_browsers_session(client):
    sid_a = _generate(client)
    client_b = TestClient(app)
    _generate(client_b)
    # Guessing the id is not enough: it belongs to another browser.
    r = client_b.get(f"/api/history/sessions/{sid_a}")
    assert r.status_code == 404


def test_cannot_delete_another_browsers_session(client):
    sid_a = _generate(client)
    client_b = TestClient(app)
    _generate(client_b)
    r = client_b.delete(f"/api/history/sessions/{sid_a}")
    assert r.status_code == 404
    # The owner can still open it.
    assert client.get(f"/api/history/sessions/{sid_a}").status_code == 200


def test_cannot_rerun_another_browsers_session(client):
    sid_a = _generate(client)
    client_b = TestClient(app)
    _generate(client_b)
    r = client_b.post(f"/api/history/sessions/{sid_a}/rerun")
    assert r.status_code == 404


def test_legacy_ownerless_rows_belong_to_nobody(client):
    """Sessions saved before per-browser scoping (client_id NULL) never show
    in anyone's history — the old shared data quietly retires itself."""
    from core.database import save_session
    legacy_id = save_session(endpoint="https://old.example.com/x", method="GET")
    assert legacy_id > 0

    _generate(client)
    listed = [s["id"] for s in client.get("/api/history/sessions").json()["sessions"]]
    assert legacy_id not in listed
    # And they can't be opened either.
    assert client.get(f"/api/history/sessions/{legacy_id}").status_code == 404


def test_browser_without_cookie_gets_one_and_sees_nothing(client):
    # Craft a request with no cookie header at all.
    r = client.get("/api/history/sessions", headers={"Cookie": ""})
    assert r.status_code == 200
    assert r.json()["sessions"] == []
    assert "apitrek_sid" in r.headers.get("set-cookie", "")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
