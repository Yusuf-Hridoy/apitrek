"""
Unit tests for the core.test_executor module.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
import requests
from core.test_executor import execute_test_case, execute_test_suite

ENDPOINT = "https://api.example.com/items/1"


def _mock_response(status=200, payload=None, text=None):
    response = MagicMock()
    response.status_code = status
    body = payload if payload is not None else {}
    response.json.return_value = body
    response.text = text if text is not None else '{"id": 1}'
    return response


@patch("core.test_executor.safe_request")
def test_positive_case_passes_on_expected_status(mock_request):
    mock_request.return_value = _mock_response(status=200, payload={"id": 1, "title": "x"})
    result = execute_test_case(
        {"id": "TC-POS-01", "title": "Valid fetch", "expected": {"status_code": 200}},
        ENDPOINT,
        "GET",
    )
    assert result["passed"] is True
    assert result["actual_status"] == 200
    assert result["expected_status"] == 200
    assert result["error_message"] is None
    assert result["category"] == "positive"
    assert isinstance(result["duration_ms"], int)


@patch("core.test_executor.safe_request")
def test_status_mismatch_fails(mock_request):
    mock_request.return_value = _mock_response(status=404)
    result = execute_test_case(
        {"id": "TC-1", "title": "t", "expected": {"status_code": 200}},
        ENDPOINT,
        "GET",
    )
    assert result["passed"] is False
    status_assertions = [a for a in result["assertion_results"] if "Status code" in a["assertion"]]
    assert status_assertions and status_assertions[0]["passed"] is False


@patch("core.test_executor.safe_request")
def test_network_error_returns_structured_result(mock_request):
    mock_request.side_effect = requests.exceptions.ConnectionError("boom")
    result = execute_test_case({"id": "TC-1", "title": "t"}, ENDPOINT, "GET")
    assert result["passed"] is False
    assert result["actual_status"] == 0
    assert "boom" in result["error_message"]


@patch("core.test_executor.safe_request")
def test_timeout_returns_structured_result(mock_request):
    mock_request.side_effect = requests.exceptions.Timeout()
    result = execute_test_case({"id": "TC-1", "title": "t"}, ENDPOINT, "GET")
    assert result["passed"] is False
    assert "timed out" in result["error_message"]


@patch("core.test_executor.safe_request")
def test_negative_case_mutates_request(mock_request):
    mock_request.return_value = _mock_response(status=400)
    execute_test_case(
        {"id": "TC-NEG-01", "title": "t", "category": "negative",
         "expected": {"status_code": 400}},
        ENDPOINT,
        "POST",
        headers={"Authorization": "Bearer real-token"},
        body={"name": "x", "price": 10},
    )
    _, kwargs = mock_request.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer invalid-token-000"
    # First body field removed
    assert "name" not in kwargs["json"]
    assert kwargs["json"] == {"price": 10}


@patch("core.test_executor.safe_request")
def test_edge_case_applies_boundary_values(mock_request):
    mock_request.return_value = _mock_response(status=200)
    execute_test_case(
        {"id": "TC-EDGE-01", "title": "t", "category": "edge"},
        ENDPOINT,
        "POST",
        body={"name": "x", "description": "y", "note": "z"},
    )
    _, kwargs = mock_request.call_args
    assert kwargs["json"]["name"] == ""
    assert kwargs["json"]["description"] == "A" * 10000
    assert kwargs["json"]["note"] is None


@patch("core.test_executor.safe_request")
def test_validation_rules_checked(mock_request):
    mock_request.return_value = _mock_response(status=200, payload={"id": 1, "price": 9.99})
    result = execute_test_case(
        {
            "id": "TC-1",
            "title": "t",
            "expected": {
                "status_code": 200,
                "validation_rules": ["Field 'price' must be a number", "Field 'missing' is present"],
            },
        },
        ENDPOINT,
        "GET",
    )
    rule_results = result["assertion_results"][1:]
    assert rule_results[0]["passed"] is True
    assert rule_results[1]["passed"] is False
    assert result["passed"] is False


@patch("core.test_executor.safe_request")
def test_assertion_category_runs_rule(mock_request):
    mock_request.return_value = _mock_response(status=200, payload=[{"id": 1}])
    result = execute_test_case(
        {"id": "", "rule": "Response status code is 200", "category": "assertion"},
        ENDPOINT,
        "GET",
    )
    assert result["passed"] is True
    assert any("status code" in a["assertion"].lower() for a in result["assertion_results"])


@patch("core.test_executor.safe_request")
def test_response_preview_truncated(mock_request):
    mock_request.return_value = _mock_response(status=200, text="x" * 1000)
    result = execute_test_case({"id": "TC-1", "title": "t"}, ENDPOINT, "GET")
    assert len(result["actual_response_preview"]) == 500


@patch("core.test_executor.safe_request")
def test_uses_15s_timeout(mock_request):
    mock_request.return_value = _mock_response()
    execute_test_case({"id": "TC-1", "title": "t"}, ENDPOINT, "GET")
    _, kwargs = mock_request.call_args
    assert kwargs["timeout"] == 15


@patch("core.test_executor.safe_request")
def test_execute_test_suite_runs_all_sequentially(mock_request):
    mock_request.return_value = _mock_response(status=200)
    cases = [
        {"id": "TC-1", "title": "a", "expected": {"status_code": 200}},
        {"id": "TC-2", "title": "b", "expected": {"status_code": 200}},
        {"id": "TC-3", "title": "c", "expected": {"status_code": 200}},
    ]
    results = execute_test_suite(cases, ENDPOINT, "GET")
    assert len(results) == 3
    assert mock_request.call_count == 3
    assert [r["test_case_id"] for r in results] == ["TC-1", "TC-2", "TC-3"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
