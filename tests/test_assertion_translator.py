"""
Unit tests for the schema-driven exports.assertion_translator.
"""
import ast
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from exports.assertion_translator import pytest_assertions, postman_assertions
from exports.python_test_generator import generate_pytest_script
from exports.postman_generator import generate_postman_collection


RESP = {
    "page": 1,
    "per_page": 6,
    "total": 12,
    "data": [{"id": 1, "email": "a@b.co", "first_name": "G"}],
    "support": {"url": "x", "text": "y"},
}


def test_no_prose_field_leak():
    lines = "\n".join(
        pytest_assertions(RESP, ["id field is required and should be present"])
    )
    assert 'data["required"]' not in lines  # 'required' is prose, not a field
    assert '"No" in data' not in lines and '"identical" in data' not in lines


def test_only_real_fields_asserted():
    lines = pytest_assertions(
        RESP, ["first_name should be a string", "total is a positive integer"]
    )
    text = "\n".join(lines)
    assert 'isinstance(data["total"], int)' in text
    # first_name lives under a list -> accessor must index [0]
    assert 'data["data"][0]["first_name"]' in text


def test_unmatched_rule_is_skipped_not_asserted():
    lines = pytest_assertions(RESP, ["the response should be identical to the schema"])
    text = "\n".join(lines)
    assert "identical" in text  # present only as a comment
    assert 'assert "identical"' not in text
    assert 'data["identical"]' not in text


def test_no_response_no_field_asserts():
    assert pytest_assertions(None, ["id should be present"]) == []


def test_list_root_response():
    lines = pytest_assertions([{"id": 1, "name": "x"}], ["id present"])
    text = "\n".join(lines)
    assert "isinstance(data, list)" in text
    assert "item" in text  # uses data[0] via item


def test_postman_no_prose_leak():
    lines = "\n".join(postman_assertions(RESP, ["id is required"]))
    assert "jsonData.required" not in lines and "jsonData['required']" not in lines


def test_postman_real_fields_only():
    lines = postman_assertions(RESP, ["first_name should be a string"])
    text = "\n".join(lines)
    assert 'jsonData["data"][0]["first_name"]' in text
    assert "jsonData.required" not in text


def test_pytest_export_only_asserts_real_fields():
    """Full-script regression: every field asserted on must exist in sample_response."""
    test_data = {
        "positive_test_cases": [
            {
                "id": "TC-POS-01",
                "title": "Valid response",
                "expected": {
                    "status_code": 200,
                    "validation_rules": [
                        "id field is required and should be present",
                        "first_name should be a string",
                        "total is a positive integer",
                    ],
                },
            }
        ],
        "assertions": [
            {"rule": "name should be present", "category": "data", "severity": "medium"},
        ],
        "sample_response": RESP,
    }
    script = generate_pytest_script("https://api.example.com/items/1", "GET", test_data)
    ast.parse(script)  # valid Python

    # Collect top-level keys actually present in the sample response.
    real_keys = set(RESP.keys())
    for case in RESP.get("data", [])[:1]:
        real_keys.update(case.keys())

    # Every data["<key>"] accessor must reference a real top-level key.
    for match in re.finditer(r'data\["([^"]+)"\]', script):
        key = match.group(1)
        assert key in real_keys, f"Script asserts unknown field: {key}"

    # No prose tokens from rules should appear as asserts.
    assert 'assert "required"' not in script
    assert 'assert "positive"' not in script


def test_postman_export_only_checks_real_fields():
    """Postman regression: no jsonData.<prose> accessors, only real fields."""
    test_data = {
        "positive_test_cases": [
            {
                "id": "TC-POS-01",
                "title": "Valid response",
                "expected": {
                    "status_code": 200,
                    "validation_rules": [
                        "id field is required and should be present",
                        "first_name should be a string",
                    ],
                },
            }
        ],
        "sample_response": RESP,
    }
    collection = generate_postman_collection(
        "https://api.example.com/items/1", "GET", test_data
    )
    # Must be valid JSON.
    import json

    parsed = json.loads(collection)
    script = "\n".join(
        parsed["item"][0]["event"][0]["script"]["exec"]
    )
    assert "jsonData.required" not in script
    assert 'jsonData["data"][0]["first_name"]' in script


def test_generate_route_includes_sample_response():
    """The generate endpoint must forward sample_response in its result."""
    from web.routes.generate import GenerateRequest, generate_tests
    from fastapi import Request
    from unittest.mock import MagicMock

    sample = {"id": 1, "title": "x"}
    payload = GenerateRequest(
        endpoint="https://example.com/api",
        method="GET",
        sample_response=sample,
    )
    request = MagicMock(spec=Request)
    result = generate_tests(request, payload)
    assert result.get("sample_response") == sample
