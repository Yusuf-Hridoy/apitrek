"""
Route tests for /api/export/cicd.
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from fastapi.testclient import TestClient

from web.app import app


client = TestClient(app)

TEST_DATA = {
    "positive_test_cases": [
        {
            "id": "POS-001",
            "title": "Valid product fetch",
            "description": "Fetch a product with a valid ID",
            "request": {"method": "GET"},
            "expected": {"status_code": 200},
        }
    ],
    "negative_test_cases": [],
    "edge_cases": [],
    "assertions": [],
}


@pytest.mark.parametrize("fmt", ["github", "gitlab", "azure"])
def test_export_cicd_returns_nonempty_yaml(fmt):
    payload = {
        "format": fmt,
        "endpoint": "https://fakestoreapi.com/products/1",
        "method": "GET",
        "test_data": TEST_DATA,
    }
    res = client.post("/api/export/cicd", json=payload)
    assert res.status_code == 200, res.text
    data = res.json()
    assert "yaml_content" in data
    assert "filename" in data
    assert data["yaml_content"].strip()
    assert data["filename"].endswith(".yml")


def test_export_cicd_unsupported_format():
    payload = {
        "format": "bitbucket",
        "endpoint": "https://fakestoreapi.com/products/1",
        "method": "GET",
        "test_data": TEST_DATA,
    }
    res = client.post("/api/export/cicd", json=payload)
    assert res.status_code == 422


def test_export_cicd_empty_endpoint():
    payload = {
        "format": "github",
        "endpoint": "   ",
        "method": "GET",
        "test_data": TEST_DATA,
    }
    res = client.post("/api/export/cicd", json=payload)
    assert res.status_code == 422


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
