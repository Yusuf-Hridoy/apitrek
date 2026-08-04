"""
SQLite persistence for API Sentinel: sessions, test cases, execution results.

Uses the built-in sqlite3 module with parameterized queries throughout.
The database lives at DATABASE_PATH (default: data/api_sentinel.db under the
project root). History is an enhancement — every public function raises on
real errors, so callers should wrap calls in try/except and degrade quietly.
"""
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "api_sentinel.db"


def get_db_path() -> Path:
    """Resolve the DB path (env-overridable, useful for tests)."""
    return Path(os.getenv("DATABASE_PATH", str(DEFAULT_DB_PATH)))


def _connect() -> sqlite3.Connection:
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Create tables if they do not exist."""
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                endpoint TEXT NOT NULL,
                method TEXT NOT NULL DEFAULT 'GET',
                headers_json TEXT,
                body_json TEXT,
                sample_response_json TEXT,
                created_at TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'functional'
            );

            CREATE TABLE IF NOT EXISTS test_cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                category TEXT,
                title TEXT,
                description TEXT,
                expected_status INTEGER,
                assertions_json TEXT,
                payload_json TEXT,
                owasp_category TEXT,
                severity TEXT
            );

            CREATE TABLE IF NOT EXISTS execution_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                test_case_id TEXT,
                passed INTEGER NOT NULL DEFAULT 0,
                actual_status INTEGER,
                actual_response TEXT,
                error_message TEXT,
                duration_ms INTEGER,
                executed_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_test_cases_session ON test_cases(session_id);
            CREATE INDEX IF NOT EXISTS idx_results_session ON execution_results(session_id);
        """)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_session(
    endpoint: str,
    method: str = "GET",
    headers: Optional[Dict[str, Any]] = None,
    body: Optional[Dict[str, Any]] = None,
    sample_response: Optional[Any] = None,
    mode: str = "functional",
) -> int:
    """Insert a session row and return its id."""
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO sessions (endpoint, method, headers_json, body_json,
                                  sample_response_json, created_at, mode)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                endpoint,
                method,
                json.dumps(headers) if headers is not None else None,
                json.dumps(body) if body is not None else None,
                json.dumps(sample_response) if sample_response is not None else None,
                _now(),
                mode,
            ),
        )
        return int(cursor.lastrowid)


def save_test_cases(session_id: int, test_cases: List[Dict[str, Any]]) -> int:
    """
    Persist test cases for a session. Each dict may carry:
    category, title, description, expected_status, assertions, payload,
    owasp_category, severity. Returns rows inserted.
    """
    rows = [
        (
            session_id,
            tc.get("category"),
            tc.get("title"),
            tc.get("description"),
            tc.get("expected_status"),
            json.dumps(tc.get("assertions")) if tc.get("assertions") is not None else None,
            json.dumps(tc.get("payload")) if tc.get("payload") is not None else None,
            tc.get("owasp_category"),
            tc.get("severity"),
        )
        for tc in test_cases
    ]
    if not rows:
        return 0
    with _connect() as conn:
        conn.executemany(
            """
            INSERT INTO test_cases (session_id, category, title, description,
                                    expected_status, assertions_json, payload_json,
                                    owasp_category, severity)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


def save_execution_results(session_id: int, results: List[Dict[str, Any]]) -> int:
    """Persist execution results for a session. Returns rows inserted."""
    now = _now()
    rows = [
        (
            session_id,
            str(r.get("test_case_id", "")),
            1 if r.get("passed") else 0,
            r.get("actual_status"),
            r.get("actual_response_preview") or r.get("actual_response"),
            r.get("error_message"),
            r.get("duration_ms"),
            now,
        )
        for r in results
    ]
    if not rows:
        return 0
    with _connect() as conn:
        conn.executemany(
            """
            INSERT INTO execution_results (session_id, test_case_id, passed,
                                           actual_status, actual_response,
                                           error_message, duration_ms, executed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


def get_recent_sessions(limit: int = 20) -> List[Dict[str, Any]]:
    """Return the most recent sessions with pass/fail aggregates."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT s.id, s.endpoint, s.method, s.created_at, s.mode,
                   (SELECT COUNT(*) FROM test_cases t WHERE t.session_id = s.id) AS test_count,
                   (SELECT COUNT(*) FROM execution_results e
                     WHERE e.session_id = s.id AND e.passed = 1) AS passed_count,
                   (SELECT COUNT(*) FROM execution_results e
                     WHERE e.session_id = s.id) AS executed_count
            FROM sessions s
            ORDER BY s.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_session(session_id: int) -> Optional[Dict[str, Any]]:
    """Return one session with its test cases and execution results, or None."""
    with _connect() as conn:
        session_row = conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if session_row is None:
            return None
        test_cases = conn.execute(
            "SELECT * FROM test_cases WHERE session_id = ? ORDER BY id", (session_id,)
        ).fetchall()
        results = conn.execute(
            "SELECT * FROM execution_results WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()

    session = dict(session_row)
    for key in ("headers_json", "body_json", "sample_response_json"):
        if session.get(key):
            session[key.replace("_json", "")] = json.loads(session[key])
        else:
            session[key.replace("_json", "")] = None

    def _decode_case(row: sqlite3.Row) -> Dict[str, Any]:
        case = dict(row)
        case["assertions"] = json.loads(case["assertions_json"]) if case.get("assertions_json") else []
        case["payload"] = json.loads(case["payload_json"]) if case.get("payload_json") else None
        return case

    session["test_cases"] = [_decode_case(row) for row in test_cases]
    session["execution_results"] = [dict(row) for row in results]
    return session


def delete_session(session_id: int) -> bool:
    """Delete a session (cascades to its rows). Returns True if it existed."""
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        return cursor.rowcount > 0
