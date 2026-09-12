"""
Regression test: sessions whose endpoint contains quotes/angle brackets
('" <>) must save and restore fully — endpoint intact, every stored test case
back, history count correct. Guards the "restores as 0 tests" bug class:
unescaped interpolation or a serialization mismatch on save/restore.
"""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest

SPECIAL_ENDPOINT = 'https://x.com/a?q="v\'<>'


def test_special_char_endpoint_round_trips_through_history():
    with tempfile.TemporaryDirectory() as tmp:
        with patch.dict("os.environ", {"DATABASE_PATH": str(Path(tmp) / "sc.db")}):
            from fastapi.testclient import TestClient
            from web.app import app

            fake_result = {
                "positive_test_cases": [
                    {"id": "TC-POS-01", "title": "Get the \"active\" item", "description": "d1",
                     "request": {"method": "GET", "endpoint": SPECIAL_ENDPOINT},
                     "expected": {"status_code": 200}},
                ],
                "negative_test_cases": [
                    {"id": "TC-NEG-01", "title": "Missing auth", "description": "d2",
                     "request": {"headers": {"Authorization": "Bearer 'expired'<>"}},
                     "expected": {"status_code": 401}},
                ],
                "edge_cases": [
                    {"id": "TC-EDGE-01", "title": "Odd query", "description": "d3",
                     "request": {"endpoint": SPECIAL_ENDPOINT}, "expected": {"status_code": 200}},
                ],
                "assertions": [],
            }

            with TestClient(app) as client:
                with patch("web.routes.generate.generate_test_cases",
                           return_value=dict(fake_result)):
                    r = client.post("/generate-tests", json={
                        "endpoint": SPECIAL_ENDPOINT, "method": "GET",
                    })
                assert r.status_code == 200
                session_id = r.json()["_session_id"]

                # History list: correct count, endpoint intact
                r = client.get("/api/history/sessions")
                sessions = r.json()["sessions"]
                assert len(sessions) == 1
                assert sessions[0]["endpoint"] == SPECIAL_ENDPOINT
                assert sessions[0]["test_count"] == 3

                # Full restore: endpoint + every stored test case comes back verbatim
                r = client.get(f"/api/history/sessions/{session_id}")
                assert r.status_code == 200
                s = r.json()
                assert s["endpoint"] == SPECIAL_ENDPOINT
                assert len(s["test_cases"]) == 3
                by_ref = {tc["case_ref"]: tc for tc in s["test_cases"]}
                assert by_ref["TC-POS-01"]["payload"]["endpoint"] == SPECIAL_ENDPOINT
                assert by_ref["TC-NEG-01"]["payload"]["headers"]["Authorization"] == "Bearer 'expired'<>"
                assert by_ref["TC-EDGE-01"]["title"] == "Odd query"
                assert by_ref["TC-POS-01"]["expected_status"] == 200


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
