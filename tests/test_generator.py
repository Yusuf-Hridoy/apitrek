"""
Unit tests for the core.generator module.
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from core.generator import (
    _extract_json,
    _repair_truncated_json,
    _safe_error_response,
    _validate_structure,
    generate_test_cases,
)


def test_extract_json_from_markdown():
    text = "```json\n{\"a\": 1}\n```"
    assert _extract_json(text) == '{"a": 1}'


def test_extract_json_plain():
    text = '{"positive_test_cases": [], "negative_test_cases": [], "edge_cases": [], "assertions": []}'
    assert _extract_json(text) == text


def test_extract_json_with_extra_text():
    text = "Here is your result:\n{\"a\": 1}\nHope this helps!"
    assert _extract_json(text) == '{"a": 1}'


def test_validate_structure_valid():
    data = {
        "positive_test_cases": [],
        "negative_test_cases": [],
        "edge_cases": [],
        "assertions": [],
    }
    assert _validate_structure(data) is True


def test_validate_structure_missing_key():
    data = {
        "positive_test_cases": [],
        "negative_test_cases": [],
        "edge_cases": [],
    }
    assert _validate_structure(data) is False


def test_safe_error_response():
    result = _safe_error_response("Something failed")
    assert result["_error"] == "Something failed"
    assert result["positive_test_cases"] == []
    assert result["negative_test_cases"] == []
    assert result["edge_cases"] == []
    assert result["assertions"] == []


def test_generate_test_cases_invalid_endpoint():
    result = generate_test_cases(endpoint="")
    assert "_error" in result
    assert "Invalid or missing" in result["_error"]


def test_generate_test_cases_mock_success():
    mock_client = MagicMock()
    mock_response = json.dumps({
        "positive_test_cases": [{"id": "TC-POS-01", "title": "Valid request"}],
        "negative_test_cases": [{"id": "TC-NEG-01", "title": "Invalid ID"}],
        "edge_cases": [{"id": "TC-EDGE-01", "title": "Empty response"}],
        "assertions": [{"category": "status_code", "rule": "200 OK", "severity": "critical"}],
    })
    mock_client.send_prompt.return_value = mock_response

    result = generate_test_cases(
        endpoint="https://api.example.com/items/1",
        method="GET",
        mistral_client=mock_client,
    )

    assert "_error" not in result
    assert len(result["positive_test_cases"]) == 1
    assert len(result["negative_test_cases"]) == 1
    assert len(result["edge_cases"]) == 1
    assert len(result["assertions"]) == 1


def test_generate_test_cases_mock_malformed_json():
    mock_client = MagicMock()
    mock_client.send_prompt.return_value = "not valid json {{{"

    result = generate_test_cases(
        endpoint="https://api.example.com/items/1",
        method="GET",
        mistral_client=mock_client,
    )

    assert "_error" in result
    assert "Could not extract valid JSON" in result["_error"]


def test_repair_truncated_json_unterminated_string():
    text = '{"key": "value'
    result = _repair_truncated_json(text)
    assert result == '{"key": "value"}'
    assert json.loads(result) == {"key": "value"}


def test_repair_truncated_json_missing_braces():
    text = '{"a": 1, "b": {'
    result = _repair_truncated_json(text)
    assert result == '{"a": 1, "b": {}}'
    assert json.loads(result) == {"a": 1, "b": {}}


def test_repair_truncated_json_trailing_comma():
    text = '{"a": 1,}'
    result = _repair_truncated_json(text)
    assert result == '{"a": 1}'
    assert json.loads(result) == {"a": 1}


def test_generate_test_cases_mock_missing_keys():
    mock_client = MagicMock()
    mock_client.send_prompt.return_value = json.dumps({"positive_test_cases": []})

    result = generate_test_cases(
        endpoint="https://api.example.com/items/1",
        method="GET",
        mistral_client=mock_client,
    )

    assert "_error" in result
    assert "omitted keys" in result["_error"]
    # Missing case lists are auto-filled as empty lists
    assert result.get("negative_test_cases") == []
    assert result.get("edge_cases") == []
    # Assertions are derived deterministically — never left empty
    assert len(result["assertions"]) > 0
    # Retry makes no progress, so the loop stops after one retry
    assert mock_client.send_prompt.call_count == 2


def test_generate_test_cases_retry_recovers_missing_keys():
    mock_client = MagicMock()
    incomplete = json.dumps({"positive_test_cases": [{"id": "TC-POS-01"}]})
    complete = json.dumps({
        "positive_test_cases": [{"id": "TC-POS-01", "title": "Valid request"}],
        "negative_test_cases": [{"id": "TC-NEG-01", "title": "Invalid ID"}],
        "edge_cases": [{"id": "TC-EDGE-01", "title": "Empty response"}],
        "assertions": [{"category": "status_code", "rule": "200 OK"}],
    })
    mock_client.send_prompt.side_effect = [incomplete, complete]

    result = generate_test_cases(
        endpoint="https://api.example.com/items/1",
        method="GET",
        mistral_client=mock_client,
    )

    assert mock_client.send_prompt.call_count == 2
    # The retry prompt must include the nudge about missing keys
    retry_prompt = mock_client.send_prompt.call_args_list[1].kwargs["user_prompt"]
    assert "missing these required" in retry_prompt
    assert "_error" not in result
    assert len(result["assertions"]) == 1


def test_generate_test_cases_retry_also_incomplete_keeps_first():
    mock_client = MagicMock()
    first = json.dumps({
        "positive_test_cases": [{"id": "TC-POS-01", "expected": {"status_code": 200}}],
        "negative_test_cases": [{"id": "TC-NEG-01"}],
        "edge_cases": [{"id": "TC-EDGE-01"}],
    })
    # Retry is LESS complete than the first attempt — first must be kept
    second = json.dumps({"positive_test_cases": [{"id": "TC-POS-02"}]})
    mock_client.send_prompt.side_effect = [first, second]

    result = generate_test_cases(
        endpoint="https://api.example.com/items/1",
        method="GET",
        mistral_client=mock_client,
    )

    assert mock_client.send_prompt.call_count == 2
    # Content comes from the first (more complete) response
    assert result["positive_test_cases"] == [{"id": "TC-POS-01", "expected": {"status_code": 200}}]
    assert result["edge_cases"] == [{"id": "TC-EDGE-01"}]
    # Assertions are derived from the kept cases — no error, nothing left empty
    assert "_error" not in result
    assert len(result["assertions"]) > 0
    assert any("200" in a["rule"] for a in result["assertions"])


def test_generate_test_cases_assertions_derived_from_cases():
    mock_client = MagicMock()
    payload = json.dumps({
        "positive_test_cases": [
            {"id": "TC-POS-01", "title": "Valid fetch",
             "expected": {"status_code": 200, "validation_rules": ["Field 'id' is present"]}},
        ],
        "negative_test_cases": [
            {"id": "TC-NEG-01", "title": "Invalid ID",
             "expected": {"status_code": 400}},
        ],
        "edge_cases": [],
    })
    mock_client.send_prompt.return_value = payload

    result = generate_test_cases(
        endpoint="https://api.example.com/items/1",
        method="GET",
        mistral_client=mock_client,
    )

    assert "_error" not in result
    rules = [a["rule"] for a in result["assertions"]]
    assert any("status code is 200" in r for r in rules)
    assert any("status code is 400" in r for r in rules)
    assert any("Field 'id' is present" in r for r in rules)
    assert any("valid JSON" in r for r in rules)
    # De-duplicated and capped
    assert len(rules) == len(set(rules))
    assert len(rules) <= 8


def test_generate_test_cases_retry_merges_only_missing_sections():
    mock_client = MagicMock()
    # First response is truncated: tail sections missing entirely
    first = json.dumps({
        "positive_test_cases": [{"id": "TC-POS-01"}],
        "negative_test_cases": [{"id": "TC-NEG-01"}],
    })
    # Targeted retry returns ONLY the missing sections
    second = json.dumps({
        "edge_cases": [{"id": "TC-EDGE-01"}],
        "assertions": [{"category": "status_code", "rule": "200 OK"}],
    })
    mock_client.send_prompt.side_effect = [first, second]

    result = generate_test_cases(
        endpoint="https://api.example.com/items/1",
        method="GET",
        mistral_client=mock_client,
    )

    assert mock_client.send_prompt.call_count == 2
    assert "_error" not in result
    # Sections from the first response are preserved
    assert result["positive_test_cases"] == [{"id": "TC-POS-01"}]
    assert result["negative_test_cases"] == [{"id": "TC-NEG-01"}]
    # Missing sections merged from the retry
    assert result["edge_cases"] == [{"id": "TC-EDGE-01"}]
    assert result["assertions"] == [{"category": "status_code", "rule": "200 OK", "grounded": False}]


def test_generate_test_cases_grounds_assertions_against_sample_response():
    mock_client = MagicMock()
    mock_response = json.dumps({
        "positive_test_cases": [{"id": "TC-POS-01", "title": "Valid request"}],
        "negative_test_cases": [],
        "edge_cases": [],
        "assertions": [
            {"category": "schema", "rule": 'body has field "price"', "severity": "high"},
            {"category": "schema", "rule": 'body has field "currency"', "severity": "high"},
            {"category": "performance", "rule": "Response time is under 2000ms", "severity": "medium"},
        ],
    })
    mock_client.send_prompt.return_value = mock_response

    result = generate_test_cases(
        endpoint="https://api.example.com/products/1",
        method="GET",
        sample_response={"id": 1, "title": "x", "price": 9.99},
        mistral_client=mock_client,
    )

    assert "grounded" in result["assertions"][0]
    assert result["assertions"][0]["grounded"] is True   # price exists
    assert result["assertions"][1]["grounded"] is False  # currency does not exist
    assert result["assertions"][2]["grounded"] is False  # timing cannot be grounded


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
