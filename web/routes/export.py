"""
FastAPI route for exporting generated test cases as pytest scripts.
"""
import sys
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from exports.python_test_generator import generate_pytest_script
from exports.postman_generator import generate_postman_collection

router = APIRouter(tags=["export"])


class ExportRequest(BaseModel):
    endpoint: str = Field(..., description="API endpoint URL")
    method: str = Field(default="GET", description="HTTP method")
    test_data: Dict[str, Any] = Field(..., description="Generated test case payload")

    class Config:
        json_schema_extra = {
            "example": {
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


@router.post("/export/python")
async def export_python(payload: ExportRequest) -> Response:
    """
    Export generated test cases as a downloadable pytest Python script.
    """
    if not payload.endpoint or not payload.endpoint.strip():
        raise HTTPException(status_code=422, detail="Endpoint is required.")

    try:
        script = generate_pytest_script(
            endpoint=payload.endpoint.strip(),
            method=payload.method.upper(),
            test_data=payload.test_data,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate script: {e}")

    filename = "test_api.py"
    return Response(
        content=script,
        media_type="text/x-python",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/export/postman")
async def export_postman(payload: ExportRequest) -> Response:
    """
    Export generated test cases as a downloadable Postman Collection JSON.
    """
    if not payload.endpoint or not payload.endpoint.strip():
        raise HTTPException(status_code=422, detail="Endpoint is required.")

    try:
        collection = generate_postman_collection(
            endpoint=payload.endpoint.strip(),
            method=payload.method.upper(),
            test_data=payload.test_data,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate collection: {e}")

    return Response(
        content=collection,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="collection.json"'},
    )
