"""
FastAPI route for executing generated test cases against the live API.
"""
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core.test_executor import execute_test_case, execute_test_suite
from core.url_guard import validate_public_url, BlockedURLError
from web.limiter import limiter, LIMIT_EXECUTE_SUITE, LIMIT_EXECUTE_SINGLE
from core.database import save_execution_results

router = APIRouter(tags=["execute"])


class ExecuteRequest(BaseModel):
    endpoint: str = Field(..., description="API endpoint URL")
    method: str = Field(default="GET", description="HTTP method")
    headers: Optional[Dict[str, Any]] = Field(default=None, description="Request headers")
    body: Optional[Dict[str, Any]] = Field(default=None, description="Request body")
    test_cases: List[Dict[str, Any]] = Field(..., description="Test cases to execute")
    test_case_ids: Optional[List[str]] = Field(
        default=None, description="Optional subset of test case IDs to run"
    )
    session_id: Optional[int] = Field(
        default=None, description="Session to attach execution results to"
    )


class ExecuteSingleRequest(BaseModel):
    endpoint: str = Field(..., description="API endpoint URL")
    method: str = Field(default="GET", description="HTTP method")
    headers: Optional[Dict[str, Any]] = Field(default=None, description="Request headers")
    body: Optional[Dict[str, Any]] = Field(default=None, description="Request body")
    test_case: Dict[str, Any] = Field(..., description="Single test case to execute")
    session_id: Optional[int] = Field(
        default=None, description="Session to attach the execution result to"
    )


def _build_summary(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(results)
    passed = sum(1 for r in results if r.get("passed"))
    failed = total - passed
    duration_ms = sum(r.get("duration_ms", 0) for r in results)
    pass_rate = f"{round(passed / total * 100)}%" if total else "0%"
    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "duration_ms": duration_ms,
        "pass_rate": pass_rate,
    }


@router.post("/execute-tests")
@limiter.limit(LIMIT_EXECUTE_SUITE)
def execute_tests(request: Request, payload: ExecuteRequest) -> Dict[str, Any]:
    """Execute a suite of test cases and return results with a summary."""
    if not payload.endpoint or not payload.endpoint.strip():
        raise HTTPException(status_code=422, detail="Endpoint is required.")

    endpoint = payload.endpoint.strip()
    try:
        validate_public_url(endpoint)
    except BlockedURLError as e:
        raise HTTPException(status_code=400, detail=str(e))

    test_cases = payload.test_cases
    if payload.test_case_ids:
        wanted = set(payload.test_case_ids)
        test_cases = [tc for tc in test_cases if str(tc.get("id", "")) in wanted]

    try:
        results = execute_test_suite(
            test_cases,
            endpoint=endpoint,
            method=payload.method.upper(),
            headers=payload.headers,
            body=payload.body,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Test execution failed: {e}")

    if payload.session_id is not None:
        try:
            save_execution_results(payload.session_id, results)
        except Exception:
            pass  # history persistence is best-effort

    return {"results": results, "summary": _build_summary(results)}


@router.post("/execute-single")
@limiter.limit(LIMIT_EXECUTE_SINGLE)
def execute_single(request: Request, payload: ExecuteSingleRequest) -> Dict[str, Any]:
    """Execute a single test case and return its result."""
    if not payload.endpoint or not payload.endpoint.strip():
        raise HTTPException(status_code=422, detail="Endpoint is required.")

    endpoint = payload.endpoint.strip()
    try:
        validate_public_url(endpoint)
    except BlockedURLError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        result = execute_test_case(
            payload.test_case,
            endpoint=endpoint,
            method=payload.method.upper(),
            headers=payload.headers,
            body=payload.body,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Test execution failed: {e}")

    if payload.session_id is not None:
        try:
            save_execution_results(payload.session_id, [result])
        except Exception:
            pass  # history persistence is best-effort

    return result
