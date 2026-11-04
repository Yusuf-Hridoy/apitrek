"""
Unit tests for core.assertion_rules — the one rule parser the live runner and
the exporters share.

The cases here are the ones that used to be misread: a ``response.`` wrapper
treated as a field, bracket notation split in half, a value with spaces or a
URL never captured, a host name or an abbreviation mistaken for a field path,
and a status rule that never says the word "status".
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from core.assertion_rules import (
    normalize_bracket_paths,
    quoted_tokens,
    resolve_field_path,
    rule_field_and_value,
    status_code_rule,
    unwrap_field_path,
    values_equal,
)


# --- fields and values --------------------------------------------------------

@pytest.mark.parametrize("rule,field,value", [
    ("response.url equals 'https://httpbin.org/get'",
     "response.url", "https://httpbin.org/get"),
    ("response.args.foo equals 'bar'", "response.args.foo", "bar"),
    ("response.args.empty equals ''", "response.args.empty", ""),
    ("response.headers['User-Agent'] equals 'qa-engine/1.0 (integration test)'",
     "response.headers.User-Agent", "qa-engine/1.0 (integration test)"),
    ('response.headers["X-Request-Id"] equals \'abc 123\'',
     "response.headers.X-Request-Id", "abc 123"),
    ("args.name equals 'alice'", "args.name", "alice"),
    ("header 'Content-Encoding' equals 'gzip'", "Content-Encoding", "gzip"),
    ("'id' is present", "id", None),
    ("data.id is an integer", "data.id", None),
])
def test_field_and_value_parsing(rule, field, value):
    assert rule_field_and_value(rule) == (field, value)


def test_url_is_never_read_as_a_field():
    """A host name only looks like a dotted path."""
    field, _ = rule_field_and_value(
        "GET https://httpbin.org/uuid must return a body"
    )
    assert field is None


def test_bare_domain_is_not_a_field():
    field, _ = rule_field_and_value("httpbin.org must answer")
    assert field is None


def test_abbreviation_is_not_a_field():
    """'e.g.' used to be read as the field 'e.g'."""
    field, _ = rule_field_and_value(
        "The 'origin' field must match IPv4 address pattern (e.g., 1.2.3.4)"
    )
    assert field == "origin"


def test_quoted_tokens_keep_any_content():
    assert quoted_tokens("x equals 'a b/c' and ''") == ["a b/c", ""]


def test_normalize_bracket_paths():
    assert normalize_bracket_paths("headers['User-Agent']") == "headers.User-Agent"
    assert normalize_bracket_paths('headers["User-Agent"]') == "headers.User-Agent"


# --- wrapper prefixes ---------------------------------------------------------

def test_unwrap_field_path_strips_only_wrappers():
    assert unwrap_field_path("response.args.foo") == "args.foo"
    assert unwrap_field_path("body.id") == "id"
    assert unwrap_field_path("json.id") == "id"
    # "data" is a real key in plenty of APIs — never stripped.
    assert unwrap_field_path("data.id") is None
    assert unwrap_field_path("id") is None


def test_resolve_tries_the_path_as_written_first():
    """An API that really has a top-level 'response' key still resolves."""
    body = {"response": {"ok": True}}
    assert resolve_field_path(body, "response.ok") == (True, True)


def test_resolve_falls_back_to_the_unwrapped_path():
    body = {"args": {"foo": "bar"}}
    assert resolve_field_path(body, "response.args.foo") == (True, "bar")


def test_resolve_never_strips_data():
    assert resolve_field_path({"data": {"id": 5}}, "data.id") == (True, 5)
    assert resolve_field_path({"id": 5}, "data.id") == (False, None)


# --- value comparison ---------------------------------------------------------

@pytest.mark.parametrize("actual,expected", [
    ("bar", "bar"),
    (" bar ", "bar"),
    (10, "10"),
    ("10", "10"),
    (True, "true"),
    (True, "True"),
    (False, "false"),
    (None, "null"),
    (1.0, "1"),
    ("", ""),
])
def test_values_equal(actual, expected):
    assert values_equal(actual, expected) is True


@pytest.mark.parametrize("actual,expected", [
    ("bar", "baz"),
    (10, "11"),
    (True, "false"),
    ("bar", ""),
    ("bar", None),
])
def test_values_differ(actual, expected):
    assert values_equal(actual, expected) is False


# --- status-code rules --------------------------------------------------------

def test_status_rule_without_the_word_status():
    assert status_code_rule(
        "GET https://httpbin.org/uuid must return HTTP 200 under normal conditions"
    ) == {"codes": [200], "multi_case": False}
    assert status_code_rule("The endpoint responds with 201") == {
        "codes": [201], "multi_case": False
    }


def test_status_rule_alternatives_are_one_check():
    assert status_code_rule("status must be 200 or 201") == {
        "codes": [200, 201], "multi_case": False
    }
    assert status_code_rule("status code is one of 200, 201") == {
        "codes": [200, 201], "multi_case": False
    }


def test_status_rule_with_several_cases_is_flagged():
    rule = "Successful GET must return 200; auth failures must return 401/403"
    parsed = status_code_rule(rule)
    assert parsed["codes"] == [200, 401, 403]
    assert parsed["multi_case"] is True


def test_non_status_rules_are_not_status_rules():
    assert status_code_rule("response.args.num equals '123'") is None
    assert status_code_rule("field 'price' must be a number") is None


def test_url_digits_are_not_status_codes():
    assert status_code_rule("GET https://api.example.com/v1/404 returns a body") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
