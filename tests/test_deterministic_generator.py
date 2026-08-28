"""Tests for the deterministic fallback generator."""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.deterministic_generator import generate_deterministic_cases


def test_returns_all_four_keys():
    result = generate_deterministic_cases("https://api.example.com/users/1", "GET")
    assert set(result.keys()) >= {
        "positive_test_cases",
        "negative_test_cases",
        "edge_cases",
        "assertions",
        "_degraded",
        "_degraded_reason",
    }
    assert result["_degraded"] is True


def test_get_produces_non_empty_lists():
    result = generate_deterministic_cases("https://api.example.com/users/1", "GET")
    assert len(result["positive_test_cases"]) > 0
    assert len(result["negative_test_cases"]) > 0
    assert len(result["edge_cases"]) > 0
    assert len(result["assertions"]) > 0


def test_post_produces_non_empty_lists():
    result = generate_deterministic_cases("https://api.example.com/users", "POST")
    assert len(result["positive_test_cases"]) > 0
    assert len(result["negative_test_cases"]) > 0
    assert len(result["edge_cases"]) > 0
    assert len(result["assertions"]) > 0


def test_cases_marked_as_deterministic():
    result = generate_deterministic_cases("https://api.example.com/users/1", "GET")
    for case in (
        result["positive_test_cases"]
        + result["negative_test_cases"]
        + result["edge_cases"]
    ):
        assert case.get("source") == "deterministic"


def test_assertions_reference_sample_fields():
    sample = {"id": 1, "name": "Ada", "email": "ada@example.com"}
    result = generate_deterministic_cases(
        "https://api.example.com/users/1", "GET", sample_response=sample
    )
    rules = [a["rule"] for a in result["assertions"]]
    assert any("id" in r for r in rules)
    assert any("name" in r for r in rules)
    assert any("email" in r for r in rules)


def test_assertions_reference_list_sample_fields():
    sample = [{"id": 1, "title": "first"}, {"id": 2, "title": "second"}]
    result = generate_deterministic_cases(
        "https://api.example.com/posts", "GET", sample_response=sample
    )
    rules = [a["rule"] for a in result["assertions"]]
    assert any("title" in r for r in rules)


def test_no_duplicate_assertions():
    result = generate_deterministic_cases(
        "https://api.example.com/users/1", "GET", sample_response={"id": 1}
    )
    rules = [a["rule"] for a in result["assertions"]]
    assert len(rules) == len(set(rules))


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
