"""
Unit tests for exports.security_report_generator.

The Markdown generator once embedded a backslash escape inside an f-string
expression, which is a SyntaxError before Python 3.12. The module failed to
import on Python 3.11 (the version our own generated CI pins). The import
test below would have caught that crash.
"""
import ast
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Import alone guards against the Python 3.11 SyntaxError regression.
from exports.security_report_generator import (
    generate_html_report,
    generate_markdown_report,
)


def test_importable_on_python_311_grammar():
    """The module source must parse under the Python 3.11 grammar."""
    source = (PROJECT_ROOT / "exports" / "security_report_generator.py").read_text()
    ast.parse(source, feature_version=(3, 11))


def _scan_results():
    return {
        "risk_score": 42,
        "summary": {
            "total_tests": 2,
            "vulnerable_count": 1,
            "needs_review_count": 0,
            "critical": 0,
            "high": 1,
            "medium": 0,
            "low": 0,
        },
        "scan_duration_ms": 120,
        "findings": [
            {
                "severity": "High",
                "owasp_category": "API1:2023",
                "title": "Rate limit bypass | no throttle observed",
                "finding": "Vulnerable",
                "finding_reason": "Server accepted 200 rapid requests.",
                "actual_status": "200",
                "remediation": "Enforce rate limiting.",
                "payload_used": {},
                "vulnerable": True,
            },
            {
                "severity": "Low",
                "owasp_category": "API8:2023",
                "title": "Security headers | missing X-Frame-Options",
                "finding": "Secure",
                "finding_reason": "Headers present.",
                "actual_status": "200",
                "remediation": "Keep headers.",
                "payload_used": {},
                "vulnerable": False,
            },
        ],
    }


def test_markdown_report_escapes_pipes_in_titles():
    import re

    report = generate_markdown_report(_scan_results(), "https://api.example.com")
    assert "Rate limit bypass \\| no throttle observed" in report
    # Escaped pipes must not break the table structure: each finding row has
    # exactly 6 structural (unescaped) pipes for 5 cells.
    rows = [ln for ln in report.splitlines() if ln.startswith("| ") and "Severity" not in ln and "---" not in ln]
    assert rows, "no table rows found"
    for row in rows:
        structural = len(re.findall(r"(?<!\\)\|", row))
        assert structural == 6, f"Malformed table row: {row}"


def test_html_report_renders_findings():
    report = generate_html_report(_scan_results(), "https://api.example.com")
    assert "<table>" in report
    assert "Rate limit bypass | no throttle observed" in report
