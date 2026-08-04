"""
Unit tests for the core.openapi_parser module.
"""
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from core.openapi_parser import parse_openapi, extract_endpoint_schemas

MINIMAL_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Test API", "version": "1.2.3"},
    "servers": [{"url": "https://api.example.com/v1"}],
    "paths": {
        "/users/{id}": {
            "get": {
                "summary": "Get a user",
                "parameters": [
                    {"name": "id", "in": "path", "required": True,
                     "schema": {"type": "integer"}}
                ],
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["id", "name"],
                                    "properties": {
                                        "id": {"type": "integer"},
                                        "name": {"type": "string"},
                                    },
                                }
                            }
                        },
                    }
                },
            },
            "delete": {"summary": "Delete a user", "responses": {"204": {"description": "Gone"}}},
        },
        "/users": {
            "post": {
                "summary": "Create user",
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["name"],
                                "properties": {"name": {"type": "string"}},
                            }
                        }
                    }
                },
                "responses": {"201": {"description": "Created"}},
                "security": [{"bearerAuth": []}],
            }
        },
    },
}

YAML_SPEC = """
openapi: 3.1.0
info:
  title: YAML API
  version: "2.0"
paths:
  /health:
    get:
      summary: Health check
      responses:
        "200":
          description: OK
"""

REF_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Ref API", "version": "1.0"},
    "paths": {
        "/items": {
            "get": {
                "responses": {
                    "200": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ItemList"}
                            }
                        }
                    }
                }
            }
        }
    },
    "components": {
        "schemas": {
            "ItemList": {
                "type": "array",
                "items": {"$ref": "#/components/schemas/Item"},
            },
            "Item": {"type": "object", "properties": {"id": {"type": "integer"}}},
        }
    },
}


def test_parse_json_spec():
    result = parse_openapi(json.dumps(MINIMAL_SPEC))
    assert result["valid"] is True
    assert result["error"] is None
    assert result["info"] == {"title": "Test API", "version": "1.2.3"}
    assert result["base_url"] == "https://api.example.com/v1"
    assert len(result["endpoints"]) == 3


def test_parse_yaml_spec_autodetected():
    result = parse_openapi(YAML_SPEC)
    assert result["valid"] is True
    assert result["info"]["title"] == "YAML API"
    assert result["endpoints"][0]["path"] == "/health"
    assert result["endpoints"][0]["method"] == "GET"


def test_invalid_spec_returns_error():
    result = parse_openapi("{not json or yaml: [")
    assert result["valid"] is False
    assert result["error"]


def test_empty_spec():
    result = parse_openapi("   ")
    assert result["valid"] is False
    assert "empty" in result["error"].lower()


def test_swagger_2_rejected():
    result = parse_openapi(json.dumps({"swagger": "2.0", "paths": {}}))
    assert result["valid"] is False
    assert "Swagger 2.0" in result["error"]


def test_unsupported_version_rejected():
    result = parse_openapi(json.dumps({"openapi": "4.0.0", "paths": {}}))
    assert result["valid"] is False
    assert "Unsupported" in result["error"]


def test_no_endpoints_rejected():
    result = parse_openapi(json.dumps({"openapi": "3.0.0", "info": {}, "paths": {}}))
    assert result["valid"] is False
    assert "no endpoints" in result["error"]


def test_local_refs_resolved():
    result = parse_openapi(json.dumps(REF_SPEC))
    assert result["valid"] is True
    schema = result["endpoints"][0]["response_schemas"]["200"]
    assert schema["type"] == "array"
    assert schema["items"]["type"] == "object"
    assert schema["items"]["properties"]["id"]["type"] == "integer"


def test_endpoint_details():
    result = parse_openapi(json.dumps(MINIMAL_SPEC))
    get_user = next(e for e in result["endpoints"] if e["method"] == "GET")
    assert get_user["summary"] == "Get a user"
    assert get_user["parameters"][0]["name"] == "id"
    assert get_user["parameters"][0]["required"] is True
    assert "200" in get_user["response_schemas"]

    create = next(e for e in result["endpoints"] if e["method"] == "POST")
    assert create["request_body_schema"]["required"] == ["name"]
    assert create["security"] == [{"bearerAuth": []}]


def test_extract_endpoint_schemas():
    parsed = parse_openapi(json.dumps(MINIMAL_SPEC))
    found = extract_endpoint_schemas(parsed, "/users", "post")
    assert found["found"] is True
    assert found["request_body_schema"]["required"] == ["name"]

    missing = extract_endpoint_schemas(parsed, "/nope", "GET")
    assert missing["found"] is False
    assert missing["error"]


def test_extract_endpoint_schemas_invalid_spec():
    result = extract_endpoint_schemas({"valid": False, "error": "bad"}, "/x", "GET")
    assert result["found"] is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
