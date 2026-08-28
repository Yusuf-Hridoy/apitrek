"""Tests for assertion grounding (verified/unverified against fetched response)."""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.generator import _is_grounded, annotate_grounding


RESP = {"id": 1, "title": "x", "price": 9.99, "rating": {"count": 5, "rate": 4.1}}


def test_field_present_verified():
    assert _is_grounded('body has field "price"', RESP) is True


def test_field_absent_unverified():
    assert _is_grounded('body has field "currency"', RESP) is False


def test_nested_path_present():
    assert _is_grounded("rating.count >= 0", RESP) is True


def test_nested_path_absent():
    assert _is_grounded("rating.median >= 0", RESP) is False


def test_bare_field_token():
    assert _is_grounded("price is a number", RESP) is True


def test_no_response_all_unverified():
    assert _is_grounded('body has field "price"', None) is False


def test_valid_json_structural_true():
    assert _is_grounded("Response body is valid JSON", RESP) is True


def test_status_and_timing_unverified():
    assert _is_grounded("Response status code is 200", RESP) is False
    assert _is_grounded("Response time is under 2000ms", RESP) is False


def test_list_response_field():
    assert _is_grounded('body has field "title"', [{"title": "a"}]) is True


def test_annotate_adds_key_and_never_crashes():
    out = annotate_grounding(
        [{"rule": 'has "price"'}, {"rule": 'has "nope"'}, "bad"],
        RESP,
    )
    assert out[0]["grounded"] is True
    assert out[1]["grounded"] is False


def test_annotate_no_response_all_false():
    out = annotate_grounding([{"rule": 'has "price"'}], None)
    assert out[0]["grounded"] is False


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
