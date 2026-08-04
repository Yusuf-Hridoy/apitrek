"""
Unit tests for the core.contract_tester module.
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from core.contract_tester import (
    generate_contract_tests,
    validate_response_against_schema,
)

USER_SCHEMA = {
    "type": "object",
    "required": ["id", "email", "role"],
    "properties": {
        "id": {"type": "integer"},
        "email": {"type": "string"},
        "role": {"type": "string", "enum": ["admin", "user", "guest"]},
        "age": {"type": ["integer", "null"]},
        "tags": {"type": "array", "items": {"type": "string"}},
        "address": {
            "type": "object",
            "required": ["city"],
            "properties": {"city": {"type": "string"}},
        },
    },
}


def test_valid_response_has_no_violations():
    actual = {
        "id": 1,
        "email": "a@b.com",
        "role": "admin",
        "age": None,
        "tags": ["x", "y"],
        "address": {"city": "Berlin"},
    }
    assert validate_response_against_schema(actual, USER_SCHEMA) == []


def test_missing_required_field():
    actual = {"id": 1, "role": "user"}
    violations = validate_response_against_schema(actual, USER_SCHEMA)
    assert any(v["path"] == "email" and "missing" in v["message"] for v in violations)


def test_wrong_type_detected():
    actual = {"id": "not-an-int", "email": "a@b.com", "role": "user"}
    violations = validate_response_against_schema(actual, USER_SCHEMA)
    assert any(
        v["path"] == "id" and v["expected"] == "integer" and v["actual"] == "string"
        for v in violations
    )


def test_enum_violation():
    actual = {"id": 1, "email": "a@b.com", "role": "superadmin"}
    violations = validate_response_against_schema(actual, USER_SCHEMA)
    assert any(v["path"] == "role" and "enum" in v["message"] for v in violations)


def test_nested_object_path():
    actual = {"id": 1, "email": "a@b.com", "role": "user", "address": {}}
    violations = validate_response_against_schema(actual, USER_SCHEMA)
    assert any(v["path"] == "address.city" for v in violations)


def test_array_items_validated():
    actual = {"id": 1, "email": "a@b.com", "role": "user", "tags": ["ok", 42]}
    violations = validate_response_against_schema(actual, USER_SCHEMA)
    assert any(v["path"] == "tags[1]" for v in violations)


def test_bool_is_not_integer():
    actual = {"id": True, "email": "a@b.com", "role": "user"}
    violations = validate_response_against_schema(actual, USER_SCHEMA)
    assert any(v["path"] == "id" and v["actual"] == "boolean" for v in violations)


def test_null_not_allowed():
    violations = validate_response_against_schema(None, {"type": "string"})
    assert violations and violations[0]["actual"] == "null"


def test_nullable_openapi30():
    assert validate_response_against_schema(None, {"type": "string", "nullable": True}) == []


def test_root_array():
    schema = {"type": "array", "items": {"type": "integer"}}
    violations = validate_response_against_schema([1, "two", 3], schema)
    assert len(violations) == 1
    assert violations[0]["path"] == "[1]"


def test_generate_contract_tests_shape():
    endpoint_info = {
        "path": "/users",
        "method": "POST",
        "request_body_schema": {"type": "object", "required": ["name"]},
        "response_schemas": {"201": {"type": "object"}, "400": {"type": "object"}},
    }
    tests = generate_contract_tests(endpoint_info)
    assert any(t["check"] == "required_field" and t["field"] == "name" for t in tests)
    schema_tests = [t for t in tests if t["check"] == "response_schema"]
    assert len(schema_tests) == 2
    assert any(t["expected_status"] == 201 for t in schema_tests)
    status_test = next(t for t in tests if t["check"] == "status_documented")
    assert status_test["documented_statuses"] == ["201", "400"]


def test_generate_contract_tests_empty_schemas():
    tests = generate_contract_tests({"path": "/x", "method": "GET", "response_schemas": {}})
    assert tests == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
