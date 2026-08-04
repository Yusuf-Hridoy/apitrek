"""
Unit tests for the core.analyzer module.
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from core.analyzer import (
    analyze_endpoint,
    analyze_fields,
    analyze_sample_response,
    build_analysis_metadata,
    infer_type,
)


def test_infer_type():
    assert infer_type(42) == "integer"
    assert infer_type(3.14) == "number"
    assert infer_type("hello") == "string"
    assert infer_type(True) == "boolean"
    assert infer_type(None) == "null"
    assert infer_type([1, 2, 3]) == "array<integer>"
    assert infer_type([]) == "array<unknown>"
    assert infer_type({"a": 1}) == "object"


def test_analyze_fields_flat():
    data = {"id": 1, "name": "Alice"}
    fields = analyze_fields(data)
    paths = [f["path"] for f in fields]
    assert "id" in paths
    assert "name" in paths
    assert all(not f["is_nested"] for f in fields)


def test_analyze_fields_nested():
    data = {"user": {"id": 1, "email": "a@b.com"}}
    fields = analyze_fields(data)
    paths = [f["path"] for f in fields]
    assert "user" in paths
    assert "user.id" in paths
    assert "user.email" in paths


def test_analyze_sample_response_empty():
    result = analyze_sample_response(None)
    assert result["field_count"] == 0
    assert result["note"] == "No sample response provided"


def test_analyze_sample_response_with_data():
    data = {"id": 1, "title": "Book", "price": 9.99}
    result = analyze_sample_response(data)
    assert result["field_count"] == 3
    assert set(result["top_level_keys"]) == {"id", "title", "price"}
    assert "integer" in result["data_types"]
    assert "string" in result["data_types"]
    assert "number" in result["data_types"]


def test_analyze_endpoint_with_id():
    result = analyze_endpoint("https://api.example.com/products/123")
    assert result["has_path_param"] is True
    assert "products" in result["inferred_resource"]
    assert "GET (retrieve one)" in result["method_hints"]


def test_analyze_endpoint_without_id():
    result = analyze_endpoint("https://api.example.com/products")
    assert result["has_path_param"] is False
    assert "GET (list)" in result["method_hints"]
    assert "POST (create)" in result["method_hints"]


def test_build_analysis_metadata():
    result = build_analysis_metadata("https://api.example.com/users/1", "GET", {"id": 1})
    assert "endpoint_analysis" in result
    assert "response_analysis" in result
    assert result["provided_method"] == "GET"


def test_analyze_endpoint_category_auth():
    result = analyze_endpoint("https://api.example.com/auth/login")
    assert result["endpoint_category"] == "authentication"


def test_analyze_endpoint_category_commerce():
    result = analyze_endpoint("https://api.example.com/checkout/orders")
    assert result["endpoint_category"] == "commerce"


def test_analyze_endpoint_category_file_media():
    result = analyze_endpoint("https://api.example.com/uploads/images")
    assert result["endpoint_category"] == "file_media"


def test_analyze_endpoint_param_type_uuid():
    result = analyze_endpoint("https://api.example.com/users/550e8400-e29b-41d4-a716-446655440000")
    assert result["path_param_type"] == "uuid"


def test_analyze_endpoint_param_type_numeric():
    result = analyze_endpoint("https://api.example.com/products/123")
    assert result["path_param_type"] == "numeric_id"


def test_analyze_endpoint_param_type_slug():
    result = analyze_endpoint("https://api.example.com/posts/hello-world")
    assert result["path_param_type"] == "slug"


def test_analyze_endpoint_category_general():
    result = analyze_endpoint("https://api.example.com/health")
    assert result["endpoint_category"] == "general"
    assert result["path_param_type"] is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
