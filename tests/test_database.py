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
    # Authorization is a credential — its value is redacted before storage.
    assert session["headers"] == {"Authorization": db.REDACTED}
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


def test_sensitive_headers_are_redacted_on_save(db):
    """Credential-bearing header values must never be persisted in plaintext."""
    session_id = db.save_session(
        endpoint="https://api.example.com/x",
        method="GET",
        headers={
            "Authorization": "Bearer super-secret-token",
            "X-API-Key": "abc123",
            "Cookie": "session=deadbeef",
            "Accept": "application/json",  # benign — kept as-is
        },
    )
    stored = db.get_session(session_id)["headers"]
    assert stored["Authorization"] == db.REDACTED
    assert stored["X-API-Key"] == db.REDACTED
    assert stored["Cookie"] == db.REDACTED
    assert stored["Accept"] == "application/json"

    # And the raw secret must not appear anywhere in the stored JSON.
    with db._connect() as conn:
        raw = conn.execute(
            "SELECT headers_json FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()[0]
    assert "super-secret-token" not in raw
    assert "abc123" not in raw
    assert "deadbeef" not in raw


def test_redact_headers_handles_non_dict():
    from core import database
    assert database.redact_headers(None) is None
    assert database.redact_headers("nope") == "nope"


def test_sensitive_body_fields_are_redacted_on_save(db):
    """Credential-like fields in request body and sample response are masked."""
    session_id = db.save_session(
        endpoint="https://api.example.com/login",
        method="POST",
        body={
            "username": "alice",              # benign — kept
            "password": "hunter2",            # secret
            "profile": {"api_key": "AK-999", "displayName": "Alice"},  # nested
            "accounts": [{"password": "pw-abc"}, "ok"],  # recurse into a benign list
            "credentials": {"user": "u", "pass_hash": "ph"},  # sensitive key → whole value masked
            "sortKey": "created_at",          # benign, must NOT match "key"
        },
        sample_response={"id": 1, "access_token": "resp-token-xyz"},
    )
    session = db.get_session(session_id)
    body = session["body"]
    assert body["username"] == "alice"
    assert body["password"] == db.REDACTED
    assert body["profile"]["api_key"] == db.REDACTED
    assert body["profile"]["displayName"] == "Alice"
    assert body["accounts"][0]["password"] == db.REDACTED
    assert body["accounts"][1] == "ok"
    assert body["credentials"] == db.REDACTED  # sensitive key masks the whole subtree
    assert body["sortKey"] == "created_at"
    assert session["sample_response"]["access_token"] == db.REDACTED
    assert session["sample_response"]["id"] == 1

    # No raw secret anywhere in the stored JSON.
    with db._connect() as conn:
        row = conn.execute(
            "SELECT body_json, sample_response_json FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    raw = (row[0] or "") + (row[1] or "")
    for secret in ("hunter2", "AK-999", "pw-abc", "ph", "resp-token-xyz"):
        assert secret not in raw


def test_redact_body_handles_scalars_and_lists():
    from core import database
    assert database.redact_body("plain") == "plain"
    assert database.redact_body(42) == 42
    assert database.redact_body(None) is None
    assert database.redact_body([{"password": "x"}, "ok"]) == [{"password": database.REDACTED}, "ok"]


def test_url_query_secrets_are_redacted_on_save(db):
    """Secrets embedded in the endpoint URL query string are masked."""
    session_id = db.save_session(
        endpoint="https://api.example.com/v1/items?api_key=SECRET123&page=2",
        method="GET",
    )
    stored = db.get_session(session_id)["endpoint"]
    assert "SECRET123" not in stored
    assert "api_key=" in stored and db.REDACTED in stored
    assert "page=2" in stored              # benign param preserved
    assert stored.startswith("https://api.example.com/v1/items")  # host/path intact


def test_redact_url_leaves_benign_urls_untouched():
    from core import database
    plain = "https://api.example.com/items/1?page=2&sort=asc"
    assert database.redact_url(plain) == plain            # no sensitive param → unchanged
    assert database.redact_url("https://api.example.com/x") == "https://api.example.com/x"
    assert database.redact_url(None) is None
    # Common credential params get masked:
    for u, secret in [
        ("https://x.com/a?token=abc", "abc"),
        ("https://x.com/a?key=zzz", "zzz"),
        ("https://x.com/a?sig=qqq", "qqq"),
    ]:
        assert secret not in database.redact_url(u)


def test_case_ref_round_trips(db):
    """The original LLM case id is persisted and returned for history matching."""
    session_id = db.save_session(endpoint="https://api.example.com/x", method="GET")
    db.save_test_cases(session_id, [
        {"category": "positive", "title": "Valid", "case_ref": "TC-POS-01"},
        {"category": "assertion", "title": "Status 200"},  # no case_ref
    ])
    session = db.get_session(session_id)
    cases = {c["title"]: c for c in session["test_cases"]}
    assert cases["Valid"]["case_ref"] == "TC-POS-01"
    assert cases["Status 200"]["case_ref"] is None


def test_migrate_add_column_is_idempotent(db):
    """Re-running init_db on an existing DB must not error or duplicate columns."""
    db.init_db()  # second call — case_ref already exists
    with db._connect() as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(test_cases)").fetchall()}
    assert "case_ref" in cols


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
