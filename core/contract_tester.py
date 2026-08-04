"""
Contract testing: validate live API responses against OpenAPI schemas and
generate contract test definitions from endpoint metadata. Deterministic.
"""
from typing import Any, Dict, List, Optional

_TYPE_CHECKS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _schema_types(schema: Dict[str, Any]) -> List[str]:
    """OpenAPI 3.1 allows type lists ('string' or ['string', 'null'])."""
    t = schema.get("type")
    if isinstance(t, list):
        return [str(x) for x in t]
    if isinstance(t, str):
        return [t]
    return []


def validate_response_against_schema(
    actual_response: Any,
    schema: Dict[str, Any],
    _path: str = "",
) -> List[Dict[str, Any]]:
    """
    Validate a response payload against a JSON schema subset
    (type, required, properties, items, enum, nullable).

    Returns violations: [{"path", "expected", "actual", "message"}].
    """
    violations: List[Dict[str, Any]] = []
    if not isinstance(schema, dict) or not schema:
        return violations

    location = _path or "(root)"

    # Nullable (OpenAPI 3.0) and type-list null (3.1)
    if actual_response is None:
        nullable = bool(schema.get("nullable")) or "null" in _schema_types(schema)
        if not nullable and _schema_types(schema):
            violations.append({
                "path": location,
                "expected": "/".join(_schema_types(schema)),
                "actual": "null",
                "message": f"{location}: got null but schema does not allow it",
            })
        return violations

    # Enum
    if "enum" in schema and actual_response not in schema["enum"]:
        violations.append({
            "path": location,
            "expected": f"one of {schema['enum']}",
            "actual": repr(actual_response),
            "message": f"{location}: value {actual_response!r} is not in the allowed enum",
        })

    # Type
    types = _schema_types(schema)
    if types and not any(
        _TYPE_CHECKS.get(t, lambda v: True)(actual_response) for t in types
    ):
        violations.append({
            "path": location,
            "expected": "/".join(types),
            "actual": _type_name(actual_response),
            "message": (
                f"{location}: expected type {'/'.join(types)}, "
                f"got {_type_name(actual_response)}"
            ),
        })
        return violations  # no point descending into a wrongly-typed value

    # Object: required + properties
    if isinstance(actual_response, dict):
        for field in schema.get("required") or []:
            if field not in actual_response:
                child = f"{_path}.{field}" if _path else field
                violations.append({
                    "path": child,
                    "expected": "present",
                    "actual": "missing",
                    "message": f"{child}: required field is missing",
                })
        properties = schema.get("properties") or {}
        for field, subschema in properties.items():
            if field in actual_response:
                child = f"{_path}.{field}" if _path else field
                violations.extend(
                    validate_response_against_schema(actual_response[field], subschema, child)
                )

    # Array: items
    if isinstance(actual_response, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for i, item in enumerate(actual_response):
                violations.extend(
                    validate_response_against_schema(item, item_schema, f"{_path}[{i}]")
                )

    return violations


def generate_contract_tests(endpoint_info: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Generate contract test definitions for one endpoint:
    required fields, type checks, enum checks, and documented status codes.
    """
    tests: List[Dict[str, Any]] = []
    path = endpoint_info.get("path", "")
    method = endpoint_info.get("method", "GET")
    response_schemas = endpoint_info.get("response_schemas") or {}

    # Required request body fields
    body_schema = endpoint_info.get("request_body_schema") or {}
    for field in body_schema.get("required") or []:
        tests.append({
            "id": f"CONTRACT-REQ-{field.upper()}",
            "title": f"Request body requires '{field}'",
            "description": f"Send the request without '{field}'; the API must reject it (4xx).",
            "check": "required_field",
            "field": field,
            "expected_status": 400,
        })

    # Response contract: required fields, types, enums per documented status
    for status, schema in response_schemas.items():
        tests.append({
            "id": f"CONTRACT-SCHEMA-{status}",
            "title": f"Response matches schema for HTTP {status}",
            "description": (
                f"Validate the {method} {path} response against the documented "
                f"schema for status {status} (required fields, types, enums)."
            ),
            "check": "response_schema",
            "expected_status": int(status) if str(status).isdigit() else None,
            "schema": schema,
        })

    # Documented status codes exist
    if response_schemas:
        tests.append({
            "id": "CONTRACT-STATUS-01",
            "title": "Response status code is documented",
            "description": (
                f"The actual status code of {method} {path} must be one of the "
                f"documented statuses: {', '.join(response_schemas)}."
            ),
            "check": "status_documented",
            "expected_status": None,
            "documented_statuses": list(response_schemas),
        })

    return tests
