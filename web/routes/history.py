"""
FastAPI routes for session history (SQLite persistence).
History is an enhancement — DB failures degrade to clean errors, never crashes.
"""
import sys
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from core.database import (
    delete_session,
    get_recent_sessions,
    get_session,
    save_execution_results,
)
from core.test_executor import execute_test_suite
from web.routes.execute import _build_summary

router = APIRouter(tags=["history"])


@router.get("/history/sessions")
async def list_sessions(limit: int = 20) -> Dict[str, Any]:
    """List recent sessions (newest first)."""
    return {"sessions": get_recent_sessions(limit=limit)}


@router.get("/history/sessions/{session_id}")
async def read_session(session_id: int) -> Dict[str, Any]:
    """Return one session with its test cases and execution results."""
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    return session


def _rebuild_executor_cases(session: Dict[str, Any]) -> list:
    """Reconstruct execute_test_case-compatible dicts from stored rows."""
    cases = []
    for tc in session.get("test_cases", []):
        expected: Dict[str, Any] = {}
        if tc.get("expected_status") is not None:
            expected["status_code"] = tc["expected_status"]
        if tc.get("assertions"):
            expected["validation_rules"] = tc["assertions"]
        cases.append({
            # Prefer the original LLM id so rerun results key the same way a live
            # run does; fall back to the DB row id for legacy rows without one.
            "id": str(tc.get("case_ref") or tc.get("id", "")),
            "title": tc.get("title") or "Untitled",
            "category": tc.get("category") or "positive",
            "request": tc.get("payload") or {},
            "expected": expected,
        })
    return cases


@router.post("/history/sessions/{session_id}/rerun")
def rerun_session(session_id: int) -> Dict[str, Any]:
    """Re-execute a stored session's test cases against the live API."""
    session = get_session(session_id)
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
        save_execution_results(session_id, results)
    except Exception:
        pass  # history persistence is best-effort

    return {"results": results, "summary": _build_summary(results)}


@router.delete("/history/sessions/{session_id}", status_code=204)
async def remove_session(session_id: int) -> Response:
    """Delete a session and its data."""
    if not delete_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found.")
    return Response(status_code=204)
