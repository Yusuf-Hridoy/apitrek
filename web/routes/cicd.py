"""
FastAPI route for exporting generated test cases as CI/CD pipeline configs.
"""
import sys
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from exports.cicd_generator import (
    generate_github_actions_yaml,
    generate_gitlab_ci_yaml,
    generate_azure_pipelines_yaml,
)

router = APIRouter(tags=["cicd"])


FORMATS = [
    {
        "id": "github",
        "name": "GitHub Actions",
        "description": "Workflow that runs your API tests on every push and pull request.",
        "filename": "api-tests.yml",
    },
    {
        "id": "gitlab",
        "name": "GitLab CI",
        "description": "Pipeline with test and report stages, JUnit artifacts and coverage.",
        "filename": ".gitlab-ci.yml",
    },
    {
        "id": "azure",
        "name": "Azure Pipelines",
        "description": "Pipeline that runs API tests and publishes results and artifacts.",
        "filename": "azure-pipelines.yml",
    },
]

_GENERATORS = {
    "github": generate_github_actions_yaml,
    "gitlab": generate_gitlab_ci_yaml,
    "azure": generate_azure_pipelines_yaml,
}


class CicdExportRequest(BaseModel):
    format: str = Field(..., description="CI/CD format: github | gitlab | azure")
    endpoint: str = Field(..., description="API endpoint URL")
    method: str = Field(default="GET", description="HTTP method")
    test_data: Dict[str, Any] = Field(..., description="Generated test case payload")

    class Config:
        json_schema_extra = {
            "example": {
                "format": "github",
                "endpoint": "https://fakestoreapi.com/products/1",
                "method": "GET",
                "test_data": {
                    "positive_test_cases": [],
                    "negative_test_cases": [],
                    "edge_cases": [],
                    "assertions": [],
                },
            }
        }


class CicdExportResponse(BaseModel):
    yaml_content: str
    filename: str


@router.get("/export/cicd/formats")
async def list_cicd_formats() -> List[Dict[str, str]]:
    """List the supported CI/CD export formats."""
    return FORMATS


@router.post("/export/cicd", response_model=CicdExportResponse)
async def export_cicd(payload: CicdExportRequest) -> CicdExportResponse:
    """Generate a CI/CD pipeline config for the generated test cases."""
    if not payload.endpoint or not payload.endpoint.strip():
        raise HTTPException(status_code=422, detail="Endpoint is required.")

    generator = _GENERATORS.get(payload.format)
    if generator is None:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported format '{payload.format}'. "
            f"Supported: {sorted(_GENERATORS)}",
        )

    try:
        yaml_content = generator(
            endpoint=payload.endpoint.strip(),
            method=payload.method.upper(),
            test_data=payload.test_data,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate pipeline: {e}")

    filename = next(f["filename"] for f in FORMATS if f["id"] == payload.format)
    return CicdExportResponse(yaml_content=yaml_content, filename=filename)
