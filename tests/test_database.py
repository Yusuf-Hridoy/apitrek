"""
Unit tests for the core.database module (SQLite persistence).
Uses tempfile-based databases via the DATABASE_PATH env override.
"""
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Fresh initialized database in a temp dir for each test."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    from core import database

    database.init_db()
    return database


def test_init_db_creates_file_and_tables(db):
    assert Path(db.get_db_path()).exists()
    with db._connect() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert {"sessions", "test_cases", "execution_results"} <= tables


def test_save_and_get_session(db):
    session_id = db.save_session(
        endpoint="https://api.example.com/users/1",
        method="POST",
        headers={"Authorization": "Bearer x"},
        body={"name": "y"},
        sample_response={"id": 1},
        mode="functional",
    )
    assert session_id > 0

    session = db.get_session(session_id)
    assert session["endpoint"] == "https://api.example.com/users/1"
    assert session["method"] == "POST"
    assert session["headers"] == {"Authorization": "Bearer x"}
    assert session["body"] == {"name": "y"}
    assert session["sample_response"] == {"id": 1}
    assert session["mode"] == "functional"
    assert session["created_at"]


def test_get_session_not_found(db):
    assert db.get_session(9999) is None


def test_save_test_cases_and_results(db):
    session_id = db.save_session(endpoint="https://api.example.com/x", method="GET")
    inserted = db.save_test_cases(session_id, [
        {"category": "positive", "title": "Valid", "description": "d",
         "expected_status": 200, "assertions": ["Field 'id' present"],
         "payload": {"endpoint": "https://api.example.com/x"}},
        {"category": "assertion", "title": "Status 200", "severity": "high"},
    ])
    assert inserted == 2

    results = [{
        "test_case_id": "1", "passed": True, "actual_status": 200,
        "actual_response_preview": '{"id": 1}', "error_message": None,
        "duration_ms": 42,
    }]
    assert db.save_execution_results(session_id, results) == 1

    session = db.get_session(session_id)
    assert len(session["test_cases"]) == 2
    assert session["test_cases"][0]["assertions"] == ["Field 'id' present"]
    assert session["test_cases"][0]["payload"] == {"endpoint": "https://api.example.com/x"}
    assert session["test_cases"][1]["severity"] == "high"
    assert len(session["execution_results"]) == 1
    assert session["execution_results"][0]["passed"] == 1
    assert session["execution_results"][0]["duration_ms"] == 42


def test_get_recent_sessions_order_and_aggregates(db):
    first = db.save_session(endpoint="https://a.com/1", method="GET")
    second = db.save_session(endpoint="https://b.com/2", method="POST")
    db.save_test_cases(first, [{"category": "positive", "title": "t"}])
    db.save_execution_results(first, [
        {"test_case_id": "1", "passed": True, "actual_status": 200},
        {"test_case_id": "2", "passed": False, "actual_status": 500},
    ])

    recent = db.get_recent_sessions()
    assert [s["id"] for s in recent] == [second, first]  # newest first
    assert recent[1]["test_count"] == 1
    assert recent[1]["executed_count"] == 2
    assert recent[1]["passed_count"] == 1


def test_delete_session_cascades(db):
    session_id = db.save_session(endpoint="https://a.com/1", method="GET")
    db.save_test_cases(session_id, [{"category": "positive", "title": "t"}])
    db.save_execution_results(session_id, [{"test_case_id": "1", "passed": True}])

    assert db.delete_session(session_id) is True
    assert db.get_session(session_id) is None
    assert db.delete_session(session_id) is False

    with db._connect() as conn:
        orphans = conn.execute(
            "SELECT COUNT(*) FROM test_cases WHERE session_id = ?", (session_id,)
        ).fetchone()[0]
    assert orphans == 0


def test_parameterized_queries_safe(db):
    """Malicious input is stored as data, not executed as SQL."""
    evil = "https://x.com/'); DROP TABLE sessions;--"
    session_id = db.save_session(endpoint=evil, method="GET")
    assert db.get_session(session_id)["endpoint"] == evil
    assert len(db.get_recent_sessions()) == 1  # table still exists


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
