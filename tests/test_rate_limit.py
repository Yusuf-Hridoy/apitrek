"""
Unit tests for slowapi rate limiting on expensive routes.
"""
import ipaddress
import sys
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from fastapi.testclient import TestClient
from limits import parse

from web.app import app


# These route tests target public-looking URLs whose hosts don't need to
# resolve — stub the guard's DNS lookup so the suite runs fully offline.
# Named hosts get a public IP; literal IPs still go through real resolution
# (no DNS involved) so blocked-target tests keep working. Kept module-local
# on purpose: tests/test_url_guard.py must keep testing real resolution
# behavior, including private/loopback blocking.
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


# Keep tests fast by using very restrictive limits. The route handlers still
# reference the same limiter instance they were decorated with, so we patch
# the parsed Limit objects in place and reset in-memory storage.
ROUTE_LIMIT_OVERRIDES = {
    # Keep the two limits on different periods so their counters do not collide
    # when they share the same key/scope in slowapi's in-memory storage.
    "web.routes.generate.generate_tests": ["1/minute", "1000/day"],
    "web.routes.security.security_scan": ["1/minute", "1000/day"],
    "web.routes.execute.execute_tests": ["1/minute", "1000/day"],
    "web.routes.execute.execute_single": ["1/minute", "1000/day"],
}


@pytest.fixture
def limited_client(monkeypatch):
    limiter = app.state.limiter
    limiter._limiter.storage.reset()

    # Save original limits so we can restore them after the test.
    originals = {}
    for name, new_values in ROUTE_LIMIT_OVERRIDES.items():
        limits = list(limiter._route_limits.get(name, []))
        originals[name] = [lim.limit for lim in limits]
        for lim, value in zip(limits, new_values):
            lim.limit = parse(value)

    yield TestClient(app)

    # Restore original limits.
    for name, original_values in originals.items():
        limits = list(limiter._route_limits.get(name, []))
        for lim, value in zip(limits, original_values):
            lim.limit = value


def test_generate_rate_limited_after_first_request(limited_client):
    payload = {
        "endpoint": "https://fakestoreapi.com/products/1",
        "method": "GET",
    }
    with (
        patch("web.routes.generate.generate_test_cases", return_value={
            "positive_test_cases": [],
            "negative_test_cases": [],
            "edge_cases": [],
            "assertions": [],
        }),
        patch("web.routes.generate.save_session", return_value=1),
        patch("web.routes.generate.save_test_cases"),
    ):
        r1 = limited_client.post("/generate-tests", json=payload)
        assert r1.status_code == 200

        r2 = limited_client.post("/generate-tests", json=payload)
        assert r2.status_code == 429
        assert "Retry-After" in r2.headers
        assert "Rate limit exceeded" in r2.json()["detail"]


def test_execute_tests_rate_limited(limited_client):
    payload = {
        "endpoint": "https://fakestoreapi.com/products/1",
        "method": "GET",
        "test_cases": [],
    }
    with (
        patch("web.routes.execute.execute_test_suite",
              return_value={"results": [], "passed": 0, "failed": 0, "needs_review": 0,
                            "not_run": 0, "executed_count": 0, "pass_rate": "0%",
                            "duration_ms": 0, "total": 0}),
        patch("web.routes.execute.save_execution_results"),
    ):
        r1 = limited_client.post("/api/execute-tests", json=payload)
        assert r1.status_code == 200

        r2 = limited_client.post("/api/execute-tests", json=payload)
        assert r2.status_code == 429


def test_security_scan_rate_limited(limited_client):
    payload = {
        "endpoint": "https://fakestoreapi.com/products/1",
        "method": "GET",
    }
    with (
        patch("web.routes.security.generate_security_tests", return_value=[]),
        patch("web.routes.security.save_session", return_value=1),
        patch("web.routes.security.save_test_cases"),
    ):
        r1 = limited_client.post("/api/security/scan", json=payload)
        assert r1.status_code == 200

        r2 = limited_client.post("/api/security/scan", json=payload)
        assert r2.status_code == 429


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
