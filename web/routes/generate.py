"""
FastAPI route for generating API test cases.
Reuses Phase 1 core engine.
"""
import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.generator import generate_test_cases
from core.live_fetcher import fetch_api_response, LiveFetchError
from core.database import save_session, save_test_cases

router = APIRouter(tags=["generate"])
logger = logging.getLogger(__name__)


def _cases_for_db(result: Dict[str, Any]) -> list:
    """Flatten a generation result into database test-case rows."""
    rows = []
    for category, key in (
        ("positive", "positive_test_cases"),
        ("negative", "negative_test_cases"),
        ("edge", "edge_cases"),
    ):
        for case in result.get(key) or []:
            expected = case.get("expected") or {}
            rows.append({
                "category": category,
                "title": case.get("title"),
                "description": case.get("description"),
                "expected_status": expected.get("status_code"),
                "assertions": expected.get("validation_rules"),
                "payload": case.get("request"),
                # Original LLM id (e.g. "TC-POS-01") — lets history reload match
                # stored execution results back to their restored cards.
                "case_ref": case.get("id"),
            })
    for assertion in result.get("assertions") or []:
        rows.append({
            "category": "assertion",
            "title": assertion.get("rule"),
            "description": assertion.get("category"),
            "expected_status": None,
            "assertions": [assertion.get("rule")] if assertion.get("rule") else [],
            "payload": None,
            "severity": assertion.get("severity"),
        })
    return rows


class GenerateRequest(BaseModel):
    endpoint: str = Field(..., description="API endpoint URL")
    method: str = Field(default="GET", description="HTTP method")
    auto_fetch: bool = Field(
        default=False, description="Automatically fetch sample response from the live API"
    )
    headers: Optional[Dict[str, Any]] = Field(
        default=None, description="Optional request headers"
    )
    request_body: Optional[Dict[str, Any]] = Field(
        default=None, description="Optional request body for POST/PUT/PATCH"
    )
    sample_response: Optional[Any] = Field(
        default=None, description="Optional sample JSON response (manual fallback)"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "endpoint": "https://fakestoreapi.com/products/1",
                "method": "GET",
                "auto_fetch": False,
                "headers": {},
                "request_body": {},
                "sample_response": {
                    "id": 1,
                    "title": "test product",
                    "price": 109.95,
                },
            }
        }


@router.post("/generate-tests")
def generate_tests(payload: GenerateRequest) -> Dict[str, Any]:
    """
    Generate structured API test cases using the Phase 1 AI engine.
    """
    if not payload.endpoint or not payload.endpoint.strip():
        raise HTTPException(status_code=422, detail="Endpoint is required.")

    endpoint = payload.endpoint.strip()
    method = payload.method.upper()
    sample_response = payload.sample_response

    if payload.auto_fetch:
        try:
            # Coerce header values to strings for safe HTTP transmission
            headers = None
            if payload.headers:
                headers = {str(k): str(v) for k, v in payload.headers.items()}

            fetched = fetch_api_response(
                endpoint=endpoint,
                method=method,
                headers=headers,
                request_body=payload.request_body,
            )
            sample_response = fetched
        except LiveFetchError as e:
            raise HTTPException(status_code=502, detail=str(e))

    result = generate_test_cases(
        endpoint=endpoint,
        method=method,
        sample_response=sample_response,
    )

    if "_error" in result:
        raise HTTPException(status_code=502, detail=result["_error"])

    # Persist the session for history (best-effort; never breaks generation)
    try:
        session_id = save_session(
            endpoint=endpoint,
            method=method,
            headers=payload.headers,
            body=payload.request_body,
            sample_response=sample_response,
        )
        save_test_cases(session_id, _cases_for_db(result))
        result["_session_id"] = session_id
    except Exception as e:
        logger.warning("Failed to save session for %s: %s", endpoint, e)

    return result
