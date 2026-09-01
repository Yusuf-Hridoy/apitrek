"""
Unit tests for exports.python_test_generator.
"""
import ast
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from exports.python_test_generator import generate_pytest_script


def test_generate_minimal_script():
    script = generate_pytest_script("https://api.example.com/items", "GET", {})
    assert "import requests" in script
    assert 'ENDPOINT = "https://api.example.com/items"' in script
    assert "def test_endpoint_is_reachable" in script
    assert "assert response.status_code in (200, 201, 204)" in script


def test_generate_with_positive_case():
    data = {
        "positive_test_cases": [
            {
                "id": "TC-POS-01",
                "title": "Get item 200",
                "description": "Valid request",
                "expected": {"status_code": 200},
            }
        ]
    }
    script = generate_pytest_script("https://api.example.com/items/1", "GET", data)
    assert "def test_positive_get_item_200" in script
    assert "assert response.status_code == 200" in script
    assert "TC-POS-01" in script


def test_generate_with_negative_case():
    data = {
        "negative_test_cases": [
            {
                "id": "TC-NEG-01",
                "title": "Not found 404",
                "expected": {"status_code": 404},
            }
        ]
    }
    script = generate_pytest_script("https://api.example.com/items/999", "GET", data)
    assert "def test_negative_not_found_404" in script
    assert "assert response.status_code == 404" in script


def test_generate_with_edge_case():
    data = {
        "edge_cases": [
            {
                "id": "TC-EDGE-01",
                "title": "Empty body",
                "expected": {"status_code": 200},
            }
        ]
    }
    script = generate_pytest_script("https://api.example.com/items", "GET", data)
    assert "def test_edge_empty_body" in script


def test_generate_with_assertions():
    data = {
        "assertions": [
            {
                "rule": "Status should be 200",
                "category": "status_code",
                "severity": "critical",
            }
        ]
    }
    script = generate_pytest_script("https://api.example.com/items", "GET", data)
    assert "def test_assertion_status_should_be_200" in script
    assert "[critical] status_code: Status should be 200" in script


def test_generate_with_validation_rules():
    data = {
        "positive_test_cases": [
            {
                "id": "TC-POS-01",
                "title": "Valid response",
                "expected": {
                    "status_code": 200,
                    "validation_rules": [
                        "id should be integer",
                        "name should be string",
                        "active should be boolean",
                        "score should be number",
                        "email should be present",
                    ],
                },
            }
        ]
    }
    script = generate_pytest_script("https://api.example.com/items", "GET", data)
    assert "assert response.status_code == 200" in script
    assert "data = response.json()" in script
    assert 'assert isinstance(data["id"], int)' in script
    assert 'assert isinstance(data["name"], str)' in script
    assert 'assert isinstance(data["active"], bool)' in script
    assert 'assert isinstance(data["score"], (int, float))' in script
    assert 'assert "email" in data' in script


def test_generate_with_post_method_and_body():
    data = {
        "positive_test_cases": [
            {
                "id": "TC-POS-01",
                "title": "Create item",
                "expected": {"status_code": 201},
                "request": {"method": "POST", "body": {"name": "test"}},
            }
        ]
    }
    script = generate_pytest_script("https://api.example.com/items", "POST", data)
    assert 'HTTP_METHOD = "POST"' in script
    assert "_make_request" in script
    assert '"POST"' in script


def test_unique_function_names_for_duplicates():
    data = {
        "positive_test_cases": [
            {"id": "TC-1", "title": "Same name", "expected": {"status_code": 200}},
            {"id": "TC-2", "title": "Same name", "expected": {"status_code": 201}},
        ]
    }
    script = generate_pytest_script("https://api.example.com/items", "GET", data)
    assert "def test_positive_same_name" in script
    assert "def test_positive_same_name_1" in script


def test_runnable_syntax():
    data = {
        "positive_test_cases": [{"title": "OK", "expected": {"status_code": 200}}],
        "negative_test_cases": [{"title": "Fail", "expected": {"status_code": 404}}],
        "assertions": [
            {"rule": "Status is 200", "category": "status", "severity": "high"}
        ],
    }
    script = generate_pytest_script("https://api.example.com/items", "GET", data)
    tree = ast.parse(script)
    assert isinstance(tree, ast.Module)


def test_malformed_assertions_fallback():
    data = {
        "assertions": [
            {},  # empty assertion
            {"rule": "", "category": "", "severity": ""},
        ]
    }
    script = generate_pytest_script("https://api.example.com/items", "GET", data)
    assert "def test_assertion_assertion_0" in script
    assert "def test_assertion_" in script
    tree = ast.parse(script)
    assert isinstance(tree, ast.Module)


def test_missing_endpoint_fallback():
    script = generate_pytest_script("", "GET", {})
    assert 'ENDPOINT = "https://example.com/api"' in script


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
