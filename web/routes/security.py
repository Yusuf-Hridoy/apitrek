"""
FastAPI routes for the OWASP API security scanner.
"""
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from core.security_scanner import (
    OWASP_CATEGORIES,
    OWASP_DESCRIPTIONS,
    calculate_risk_score,
    execute_security_test,
    generate_security_tests,
)
from exports.security_report_generator import (
    generate_html_report,
    generate_markdown_report,
)
from core.database import save_session, save_test_cases

router = APIRouter(tags=["security"])
logger = logging.getLogger(__name__)


class ScanRequest(BaseModel):
    endpoint: str = Field(..., description="API endpoint URL")
    method: str = Field(default="GET", description="HTTP method")
    headers: Optional[Dict[str, Any]] = Field(default=None, description="Request headers")
    body: Optional[Dict[str, Any]] = Field(default=None, description="Request body")
    sample_response: Optional[Dict[str, Any]] = Field(
        default=None, description="Optional sample response body"
    )
    categories: Optional[List[str]] = Field(
        default=None, description="OWASP category IDs to scan (default: all)"
    )


class ReportRequest(BaseModel):
    format: str = Field(..., description="Report format: markdown | html")
    endpoint: str = Field(..., description="Scanned API endpoint URL")
    scan: Dict[str, Any] = Field(..., description="Scan response payload from /security/scan")


def _build_summary(findings: List[Dict[str, Any]]) -> Dict[str, Any]:
    summary = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for finding in findings:
        if finding.get("vulnerable"):
            key = finding.get("severity", "").lower()
            if key in summary:
                summary[key] += 1
    summary["total_tests"] = len(findings)
    summary["vulnerable_count"] = sum(1 for f in findings if f.get("vulnerable"))
    return summary


@router.get("/security/owasp-categories")
async def list_owasp_categories() -> List[Dict[str, str]]:
    """List the OWASP API Top 10 categories covered by the scanner."""
    return [
        {"id": key, "name": name, "description": OWASP_DESCRIPTIONS.get(key, "")}
        for key, name in OWASP_CATEGORIES.items()
    ]


@router.post("/security/scan")
@limiter.limit(LIMIT_SECURITY_SCAN)
def security_scan(request: Request, payload: ScanRequest) -> Dict[str, Any]:
    """Generate and execute OWASP security tests against the endpoint."""
    if not payload.endpoint or not payload.endpoint.strip():
        raise HTTPException(status_code=422, detail="Endpoint is required.")

    endpoint = payload.endpoint.strip()
    method = payload.method.upper()

    try:
        validate_public_url(endpoint)
    except BlockedURLError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        tests = generate_security_tests(
            endpoint=endpoint,
            method=method,
            headers=payload.headers,
            body=payload.body,
            sample_response=payload.sample_response,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate security tests: {e}")

    if payload.categories:
        wanted = set(payload.categories)
        tests = [t for t in tests if t["owasp_category"].split(" - ")[0] in wanted]

    start = time.perf_counter()
    findings = [
        execute_security_test(
            test, endpoint, method, headers=payload.headers, body=payload.body
        )
        for test in tests
    ]
    scan_duration_ms = int((time.perf_counter() - start) * 1000)

    response: Dict[str, Any] = {
        "findings": findings,
        "risk_score": calculate_risk_score(findings),
        "summary": _build_summary(findings),
        "scan_duration_ms": scan_duration_ms,
    }

    # Persist the scan for history (best-effort; never breaks the scan).
    try:
        session_id = save_session(
            endpoint=endpoint,
            method=method,
            headers=payload.headers,
            body=payload.body,
            sample_response=payload.sample_response,
            mode="security",
        )
        save_test_cases(session_id, [
            {
                "category": "security",
                "title": f.get("title"),
                "description": f.get("owasp_category"),
                "payload": f.get("payload_used"),
                "owasp_category": f.get("owasp_category"),
                "severity": f.get("severity"),
            }
            for f in findings
        ])
        response["_session_id"] = session_id
    except Exception as e:
        logger.warning("Failed to save security scan for %s: %s", endpoint, e)

    return response


@router.post("/security/report")
async def security_report(payload: ReportRequest) -> Response:
    """Render a scan response as a downloadable Markdown or HTML report."""
    if payload.format == "markdown":
        content = generate_markdown_report(payload.scan, payload.endpoint)
        return Response(
            content=content,
            media_type="text/markdown",
            headers={"Content-Disposition": 'attachment; filename="security-report.md"'},
        )
    if payload.format == "html":
        content = generate_html_report(payload.scan, payload.endpoint)
        return Response(
            content=content,
            media_type="text/html",
            headers={"Content-Disposition": 'attachment; filename="security-report.html"'},
        )
    raise HTTPException(status_code=422, detail="Format must be 'markdown' or 'html'.")
