"""
FastAPI routes for session history (SQLite persistence).
History is an enhancement — DB failures degrade to clean errors, never crashes.

Every route is scoped to the caller's browser via the anonymous apitrek_sid
cookie (see web/client_id.py): one visitor can never list, open, rerun, or
delete another visitor's sessions.
"""
import sys
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from core.database import (
    delete_session,
    get_recent_sessions,
    get_session,
    save_execution_results,
)
from core.test_executor import execute_test_suite
from web.client_id import browser_id, ensure_browser_id
from web.routes.execute import _build_summary

router = APIRouter(tags=["history"])


@router.get("/history/sessions")
async def list_sessions(
    request: Request, response: Response, limit: int = 20
) -> Dict[str, Any]:
    """List recent sessions (newest first) for THIS browser only."""
    client_id = ensure_browser_id(request, response)
    return {"sessions": get_recent_sessions(limit=limit, client_id=client_id)}


@router.get("/history/sessions/{session_id}")
async def read_session(session_id: int, request: Request) -> Dict[str, Any]:
    """Return one session with its test cases and execution results."""
    session = _owned_session(session_id, request)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    return session


def _owned_session(session_id: int, request: Request):
    """The session only when it belongs to this browser; None otherwise.

    A request without the apitrek_sid cookie owns nothing — the list route
    issues the cookie, so after first contact every browser carries one and
    guessing an id (or omitting the cookie) is never enough to read, rerun,
    or delete someone else's session.
    """
    client_id = browser_id(request)
    if client_id is None:
        return None
    return get_session(session_id, client_id=client_id)


def _rebuild_executor_cases(session: Dict[str, Any]) -> list:
    """Reconstruct execute_test_case-compatible dicts from stored rows."""
    cases = []
    for tc in session.get("test_cases", []):
        expected: Dict[str, Any] = {}
        if tc.get("expected_status") is not None:
            expected["status_code"] = tc["expected_status"]
        if tc.get("assertions"):
            expected["validation_rules"] = tc["assertions"]
        payload = tc.get("payload") or {}
        # A stored skip marker rides inside the payload (see generate.py);
        # split it out so the runner skips the case without sending anything.
        skip = payload.get("skip") if isinstance(payload, dict) else None
        request = {k: v for k, v in payload.items() if k != "skip"} if skip else payload
        cases.append({
            # Prefer the original LLM id so rerun results key the same way a live
            # run does; fall back to the DB row id for legacy rows without one.
            "id": str(tc.get("case_ref") or tc.get("id", "")),
            "title": tc.get("title") or "Untitled",
            "category": tc.get("category") or "positive",
            "request": request,
            "expected": expected,
            "skip": skip,
        })
    return cases


@router.post("/history/sessions/{session_id}/rerun")
def rerun_session(session_id: int, request: Request) -> Dict[str, Any]:
    """Re-execute a stored session's test cases against the live API."""
    session = _owned_session(session_id, request)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found.")

    cases = _rebuild_executor_cases(session)
    if not cases:
        raise HTTPException(status_code=422, detail="Session has no stored test cases.")

    results = execute_test_suite(
        cases,
        endpoint=session["endpoint"],
        method=session["method"],
        headers=session.get("headers"),
        body=session.get("body"),
    )
    try:
        save_execution_results(session_id, results["results"])
    except Exception:
        pass  # history persistence is best-effort

    return {"results": results["results"], "summary": _build_summary(results["results"])}


@router.delete("/history/sessions/{session_id}", status_code=204)
async def remove_session(session_id: int, request: Request) -> Response:
    """Delete a session and its data (own sessions only)."""
    if _owned_session(session_id, request) is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    delete_session(session_id)
    return Response(status_code=204)
