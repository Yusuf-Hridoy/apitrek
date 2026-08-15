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
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

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
                severity TEXT,
                case_ref TEXT
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
        _migrate_add_column(conn, "test_cases", "case_ref", "TEXT")


def _migrate_add_column(
    conn: sqlite3.Connection, table: str, column: str, col_type: str
) -> None:
    """Add a column to an existing table if it isn't already present (idempotent)."""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


REDACTED = "***REDACTED***"

# Substrings (case-insensitive) that mark a header as carrying a secret. Kept
# broad on purpose — over-redacting a benign header is harmless, leaking a token
# to disk is not.
_SENSITIVE_HEADER_HINTS = (
    "authorization", "auth", "cookie", "token", "secret", "password", "passwd",
    "pwd", "api-key", "apikey", "api_key", "credential", "x-csrf", "session",
    "access-key", "private-key", "bearer",
)


def redact_headers(headers: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Return a copy of ``headers`` with sensitive values masked as ``REDACTED``.

    Header names are preserved so history stays intelligible; only the values of
    credential-bearing headers (Authorization, Cookie, API keys, tokens, ...) are
    replaced. Non-dict input is returned unchanged.
    """
    if not isinstance(headers, dict):
        return headers
    return {
        key: (REDACTED if any(h in str(key).lower() for h in _SENSITIVE_HEADER_HINTS) else value)
        for key, value in headers.items()
    }


# Body/response field names that carry secrets. `_CONTAINS` is matched as a
# substring of the normalized (lowercased, alphanumeric-only) key; `_EXACT` must
# match the whole normalized key. Bodies are test *input* reused by rerun, so this
# set is deliberately tighter than the header set to avoid corrupting benign data.
_SENSITIVE_BODY_CONTAINS = (
    "password", "passwd", "passphrase", "secret", "token", "apikey", "accesskey",
    "privatekey", "clientsecret", "credential", "authorization",
)
_SENSITIVE_BODY_EXACT = {"pwd", "auth", "otp", "totp", "cvv", "pin"}


def _is_sensitive_body_key(key: Any) -> bool:
    norm = "".join(ch for ch in str(key).lower() if ch.isalnum())
    if norm in _SENSITIVE_BODY_EXACT:
        return True
    return any(marker in norm for marker in _SENSITIVE_BODY_CONTAINS)


def redact_body(value: Any) -> Any:
    """
    Recursively mask credential-bearing fields in a JSON body/response.

    Walks nested objects and arrays; any dict key that looks like a secret
    (password, token, api_key, client_secret, ...) has its value replaced with
    ``REDACTED``. Non-container values pass through unchanged.
    """
    if isinstance(value, dict):
        return {
            k: (REDACTED if _is_sensitive_body_key(k) else redact_body(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact_body(item) for item in value]
    return value


# Query-param names that carry secrets in a URL. Reuses the body markers plus
# short bare names common in API URLs (?key=, ?sig=, ?sas=).
_SENSITIVE_QUERY_EXACT = {"key", "sig", "signature", "sas"}


def _is_sensitive_query_key(key: str) -> bool:
    norm = "".join(ch for ch in str(key).lower() if ch.isalnum())
    return norm in _SENSITIVE_QUERY_EXACT or _is_sensitive_body_key(key)


def redact_url(url: Any) -> Any:
    """
    Mask credential-bearing query-string values in a URL.

    The scheme, host, and path are left untouched; only the values of sensitive
    query params (api_key, token, key, sig, ...) are replaced with ``REDACTED``.
    URLs with no query string, or none whose params look sensitive, are returned
    byte-for-byte unchanged.
    """
    if not isinstance(url, str) or "?" not in url:
        return url
    try:
        parts = urlsplit(url)
        if not parts.query:
            return url
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        if not any(_is_sensitive_query_key(k) for k, _ in pairs):
            return url
        redacted = [
            (k, REDACTED if _is_sensitive_query_key(k) else v) for k, v in pairs
        ]
        # safe="*" keeps the REDACTED marker readable (not percent-encoded).
        return urlunsplit(parts._replace(query=urlencode(redacted, safe="*")))
    except Exception:
        return url


def save_session(
    endpoint: str,
    method: str = "GET",
    headers: Optional[Dict[str, Any]] = None,
    body: Optional[Dict[str, Any]] = None,
    sample_response: Optional[Any] = None,
    mode: str = "functional",
) -> int:
    """
    Insert a session row and return its id.

    Sensitive values are redacted before storage so credentials never land in the
    database: header values (bearer tokens, API keys, cookies), credential-like
    fields inside the request body and sample response (password, token, ...), and
    secret query-string params in the endpoint URL (?api_key=..., ?sig=...).
    """
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO sessions (endpoint, method, headers_json, body_json,
                                  sample_response_json, created_at, mode)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                redact_url(endpoint),
                method,
                json.dumps(redact_headers(headers)) if headers is not None else None,
                json.dumps(redact_body(body)) if body is not None else None,
                json.dumps(redact_body(sample_response)) if sample_response is not None else None,
                _now(),
                mode,
            ),
        )
        return int(cursor.lastrowid)


def save_test_cases(session_id: int, test_cases: List[Dict[str, Any]]) -> int:
    """
    Persist test cases for a session. Each dict may carry:
    category, title, description, expected_status, assertions, payload,
    owasp_category, severity, case_ref. Returns rows inserted.

    ``case_ref`` is the original LLM-assigned case id (e.g. "TC-POS-01"); it lets
    stored execution results be matched back to their cases on history reload.
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
            tc.get("case_ref"),
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
                                    owasp_category, severity, case_ref)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
