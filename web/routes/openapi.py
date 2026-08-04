"""
FastAPI routes for OpenAPI import and contract testing.
"""
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.openapi_parser import parse_openapi, extract_endpoint_schemas
from core.contract_tester import (
    generate_contract_tests,
    validate_response_against_schema,
)

router = APIRouter(tags=["openapi"])


class ParseRequest(BaseModel):
    spec: str = Field(..., description="OpenAPI spec text (JSON or YAML)")


class ContractTestsRequest(BaseModel):
    spec: str = Field(..., description="OpenAPI spec text (JSON or YAML)")
    path: str = Field(..., description="Endpoint path from the spec")
    method: str = Field(default="GET", description="HTTP method")


class ValidateResponseRequest(BaseModel):
    actual_response: Any = Field(..., description="Actual API response payload")
    schema_: Dict[str, Any] = Field(..., alias="schema", description="JSON schema to validate against")

    class Config:
        populate_by_name = True


@router.post("/openapi/parse")
async def parse_spec(payload: ParseRequest) -> Dict[str, Any]:
    """Parse an OpenAPI 3.0/3.1 spec and return its endpoint inventory."""
    if not payload.spec or not payload.spec.strip():
        raise HTTPException(status_code=422, detail="Spec content is required.")

    parsed = parse_openapi(payload.spec)
    if not parsed["valid"]:
        raise HTTPException(status_code=422, detail=parsed["error"])
    return parsed


@router.post("/openapi/contract-tests")
async def contract_tests(payload: ContractTestsRequest) -> Dict[str, Any]:
    """Generate contract tests for one endpoint from the spec."""
    parsed = parse_openapi(payload.spec)
    if not parsed["valid"]:
        raise HTTPException(status_code=422, detail=parsed["error"])

    endpoint_info = extract_endpoint_schemas(parsed, payload.path, payload.method)
    if not endpoint_info["found"]:
        raise HTTPException(status_code=404, detail=endpoint_info["error"])

    return {
        "endpoint": {"path": payload.path, "method": payload.method.upper()},
        "contract_tests": generate_contract_tests(endpoint_info),
        "schemas": endpoint_info,
    }


@router.post("/openapi/validate-response")
async def validate_response(payload: ValidateResponseRequest) -> Dict[str, Any]:
    """Validate an actual API response against a JSON schema."""
    violations = validate_response_against_schema(payload.actual_response, payload.schema_)
    return {
        "valid": len(violations) == 0,
        "violation_count": len(violations),
        "violations": violations,
    }
