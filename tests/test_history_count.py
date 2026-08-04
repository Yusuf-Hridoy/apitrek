"""
Regression test: generated test cases must show up in the history sidebar count.
"""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest


def test_generate_then_history_shows_correct_test_count():
    with tempfile.TemporaryDirectory() as tmp:
        with patch.dict("os.environ", {"DATABASE_PATH": str(Path(tmp) / "h.db")}):
            from fastapi.testclient import TestClient
            from web.app import app

            fake_result = {
                "positive_test_cases": [
                    {"id": "TC-POS-01", "title": "t", "description": "d",
                     "request": {"method": "GET"}, "expected": {"status_code": 200}},
                ],
                "negative_test_cases": [{"id": "TC-NEG-01", "title": "t2"}],
                "edge_cases": [],
                "assertions": [{"category": "status_code", "rule": "200 OK", "severity": "high"}],
            }

            with TestClient(app) as client:
                with patch("web.routes.generate.generate_test_cases",
                           return_value=dict(fake_result)):
                    r = client.post("/generate-tests", json={
                        "endpoint": "https://api.example.com/users/1", "method": "GET",
                    })
                assert r.status_code == 200
                assert r.json()["_session_id"]

                r = client.get("/api/history/sessions")
                sessions = r.json()["sessions"]
                assert len(sessions) == 1
                # 1 positive + 1 negative + 1 assertion = 3 stored test cases
                assert sessions[0]["test_count"] == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
