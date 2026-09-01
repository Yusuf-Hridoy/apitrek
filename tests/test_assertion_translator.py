"""
Unit tests for exports.assertion_translator.
"""
import ast
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from exports.assertion_translator import to_postman, to_pytest
from exports.python_test_generator import generate_pytest_script


def _has_assert_line(lines):
    return any(line.strip().startswith("assert ") for line in lines)


def test_positive_integer_yields_real_assert():
    lines = to_pytest("userId is a positive integer")
    assert _has_assert_line(lines)
    text = "\n".join(lines)
    assert 'data["userId"]' in text
    # "positive" takes precedence over "integer"
    assert "data[\"userId\"] > 0" in text or "isinstance(data[\"userId\"], int)" in text


def test_matches_id_literal():
    lines = to_pytest("id field matches requested ID (1)")
    assert _has_assert_line(lines)
    text = "\n".join(lines)
    assert 'data["id"]' in text
    # Either a literal equality or a safe presence fallback is acceptable;
    # the important thing is that it is a real assert, not just a comment.
    assert "data[\"id\"] == 1" in text or '"id" in data' in text


def test_quoted_field_presence():
    lines = to_pytest('body has field "price"')
    assert _has_assert_line(lines)
    assert any('assert "price" in data' in line for line in lines)


def test_string_type_assert():
    lines = to_pytest("rating should be a string")
    assert _has_assert_line(lines)
    assert any('assert isinstance(data["rating"], str)' in line for line in lines)


def test_to_pytest_never_comment_only():
    rules = [
        "userId is a positive integer",
        "id field matches requested ID (1)",
        'body has field "price"',
        "rating should be a string",
        "email should be present",
        "active should be boolean",
        "score should be a number",
        "tags should be an array",
        "name is not null",
        "count should equal 10",
        "some weird unmatched rule",
    ]
    for rule in rules:
        lines = to_pytest(rule)
        assert _has_assert_line(lines), f"Rule produced no assert line: {rule}"


def test_postman_presence():
    lines = to_postman("name should be present")
    assert any('pm.expect(jsonData).to.have.property("name")' in line for line in lines)


def test_postman_string_type():
    lines = to_postman("name should be a string")
    assert any('pm.expect(jsonData.name).to.be.a("string")' in line for line in lines)


def test_postman_number_type():
    lines = to_postman("score should be a number")
    assert any('pm.expect(jsonData.score).to.be.a("number")' in line for line in lines)


def test_postman_never_comment_only():
    rules = [
        "userId is a positive integer",
        "id field matches requested ID (1)",
        'body has field "price"',
        "rating should be a string",
        "email should be present",
    ]
    for rule in rules:
        lines = to_postman(rule)
        assert any(line.strip().startswith("pm.expect") for line in lines), (
            f"Rule produced no pm.expect line: {rule}"
        )


def test_pytest_export_has_no_comment_only_validations():
    """Regression guard: no exported case or assertion may be comment-only."""
    test_data = {
        "positive_test_cases": [
            {
                "id": "TC-POS-01",
                "title": "Valid response",
                "expected": {
                    "status_code": 200,
                    "validation_rules": [
                        "id field matches requested ID (1)",
                        "userId is a positive integer",
                        "body has field \"price\"",
                        "rating should be a string",
                        "email should be present",
                    ],
                },
            }
        ],
        "assertions": [
            {"rule": "name should be present", "category": "data", "severity": "medium"},
        ],
    }
    script = generate_pytest_script("https://api.example.com/items/1", "GET", test_data)
    # Must be valid Python.
    ast.parse(script)

    # Every "# Validation:" comment must be followed by an assert line before the
    # next blank line or function definition.
    for match in re.finditer(r"^    # Validation: .*$", script, re.MULTILINE):
        start = match.end()
        # Look at the next few lines for an assert.
        snippet = script[start : start + 300]
        next_lines = [ln for ln in snippet.splitlines() if ln.strip()]
        assert next_lines and next_lines[0].strip().startswith("assert "), (
            f"Comment-only validation found near: {match.group(0)}"
        )
