"""
Unit tests for exports.postman_generator.
"""
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from exports.postman_generator import generate_postman_collection


def test_generate_minimal_collection():
    collection = generate_postman_collection("https://api.example.com/items", "GET", {})
    data = json.loads(collection)
    assert data["info"]["schema"] == "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
    assert len(data["item"]) == 1
    assert data["item"][0]["name"] == "Smoke Test"
    assert data["item"][0]["request"]["method"] == "GET"
    assert "200, 201, 204" in json.dumps(data["item"][0]["event"][0]["script"]["exec"])


def test_generate_with_positive_case():
    test_data = {
        "positive_test_cases": [
            {
                "id": "TC-POS-01",
                "title": "Get item 200",
                "description": "Valid request",
                "expected": {"status_code": 200},
            }
        ]
    }
    collection = generate_postman_collection("https://api.example.com/items/1", "GET", test_data)
    data = json.loads(collection)
    assert len(data["item"]) == 1
    assert data["item"][0]["name"] == "TC-POS-01 - Get item 200"
    assert data["item"][0]["request"]["method"] == "GET"
    assert data["item"][0]["request"]["url"]["raw"] == "https://api.example.com/items/1"
    exec_lines = data["item"][0]["event"][0]["script"]["exec"]
    assert any("Status code is 200" in line for line in exec_lines)
    assert any("pm.response.to.have.status(200)" in line for line in exec_lines)


def test_generate_with_negative_case():
    test_data = {
        "negative_test_cases": [
            {
                "id": "TC-NEG-01",
                "title": "Not found 404",
                "expected": {"status_code": 404},
            }
        ]
    }
    collection = generate_postman_collection("https://api.example.com/items/999", "GET", test_data)
    data = json.loads(collection)
    assert len(data["item"]) == 1
    assert data["item"][0]["name"] == "TC-NEG-01 - Not found 404"
    exec_lines = data["item"][0]["event"][0]["script"]["exec"]
    assert any("Status code is 404" in line for line in exec_lines)


def test_generate_with_edge_case():
    test_data = {
        "edge_cases": [
            {
                "id": "TC-EDGE-01",
                "title": "Empty body",
                "expected": {"status_code": 200},
            }
        ]
    }
    collection = generate_postman_collection("https://api.example.com/items", "GET", test_data)
    data = json.loads(collection)
    assert len(data["item"]) == 1
    assert data["item"][0]["name"] == "TC-EDGE-01 - Empty body"


def test_generate_with_assertions():
    test_data = {
        "assertions": [
            {
                "rule": "Status should be 200",
                "category": "status_code",
                "severity": "critical",
            }
        ]
    }
    collection = generate_postman_collection("https://api.example.com/items", "GET", test_data)
    data = json.loads(collection)
    assert len(data["item"]) == 1
    assert data["item"][0]["name"] == "[critical] Status should be 200"
    exec_lines = data["item"][0]["event"][0]["script"]["exec"]
    assert any("[critical] status_code: Status should be 200" in line for line in exec_lines)


def test_generate_with_validation_rules():
    test_data = {
        "positive_test_cases": [
            {
                "id": "TC-POS-01",
                "title": "Valid response",
                "expected": {
                    "status_code": 200,
                    "validation_rules": [
                        "id should be integer",
                        "name should be string",
                        "active should be boolean",
                        "score should be number",
                        "email should be present",
                    ],
                },
            }
        ],
        "sample_response": {
            "id": 1,
            "name": "Widget",
            "active": True,
            "score": 99.5,
            "email": "test@example.com",
        },
    }
    collection = generate_postman_collection("https://api.example.com/items", "GET", test_data)
    data = json.loads(collection)
    exec_lines = data["item"][0]["event"][0]["script"]["exec"]
    exec_text = "\n".join(exec_lines)
    assert 'pm.expect(jsonData["id"]).to.be.a(\'number\')' in exec_text
    assert 'pm.expect(jsonData["name"]).to.be.a(\'string\')' in exec_text
    assert 'pm.expect(jsonData["active"]).to.be.a(\'boolean\')' in exec_text
    assert 'pm.expect(jsonData["score"]).to.be.a(\'number\')' in exec_text
    assert 'pm.expect(jsonData["email"]).to.be.a(\'string\')' in exec_text


def test_generate_with_post_method_and_body():
    test_data = {
        "positive_test_cases": [
            {
                "id": "TC-POS-01",
                "title": "Create item",
                "expected": {"status_code": 201},
                "request": {"method": "POST", "body": {"name": "test"}},
            }
        ]
    }
    collection = generate_postman_collection("https://api.example.com/items", "POST", test_data)
    data = json.loads(collection)
    req = data["item"][0]["request"]
    assert req["method"] == "POST"
    assert req["body"]["mode"] == "raw"
    assert '"name": "test"' in req["body"]["raw"]


def test_url_parsing_with_query_params():
    collection = generate_postman_collection("https://api.example.com/items?limit=10&offset=0", "GET", {})
    data = json.loads(collection)
    url = data["item"][0]["request"]["url"]
    assert url["raw"] == "https://api.example.com/items?limit=10&offset=0"
    assert url["protocol"] == "https"
    assert url["host"] == ["api", "example", "com"]
    assert url["path"] == ["items"]
    queries = url["query"]
    assert any(q["key"] == "limit" and q["value"] == "10" for q in queries)
    assert any(q["key"] == "offset" and q["value"] == "0" for q in queries)


def test_missing_endpoint_fallback():
    collection = generate_postman_collection("", "GET", {})
    data = json.loads(collection)
    assert data["item"][0]["request"]["url"]["raw"] == "https://example.com/api"


def test_unsupported_method_passed_through():
    collection = generate_postman_collection("https://api.example.com/items", "TRACE", {})
    data = json.loads(collection)
    assert data["item"][0]["request"]["method"] == "TRACE"


def test_valid_json_output():
    test_data = {
        "positive_test_cases": [
            {"title": "OK", "expected": {"status_code": 200}}
        ],
        "negative_test_cases": [
            {"title": "Fail", "expected": {"status_code": 404}}
        ],
        "assertions": [
            {"rule": "Status is 200", "category": "status", "severity": "high"}
        ],
    }
    collection = generate_postman_collection("https://api.example.com/items", "GET", test_data)
    # Must parse without error
    data = json.loads(collection)
    assert len(data["item"]) == 3


def test_global_headers_and_body_applied():
    test_data = {}
    headers = {"Authorization": "Bearer token", "Accept": "application/json"}
    body = {"key": "value"}
    collection = generate_postman_collection(
        "https://api.example.com/items", "POST", test_data, headers=headers, request_body=body
    )
    data = json.loads(collection)
    req = data["item"][0]["request"]
    assert req["header"][0]["key"] == "Authorization"
    assert req["header"][0]["value"] == "Bearer token"
    assert req["body"]["mode"] == "raw"
    assert '"key": "value"' in req["body"]["raw"]


def test_malformed_assertions_fallback():
    test_data = {
        "assertions": [
            {},
            {"rule": "", "category": "", "severity": ""},
        ]
    }
    collection = generate_postman_collection("https://api.example.com/items", "GET", test_data)
    data = json.loads(collection)
    assert len(data["item"]) == 2
    # Should still be valid JSON and contain requests
    for item in data["item"]:
        assert "request" in item


# --- corrective-overhaul regression guards ---

def test_case_request_endpoint_and_headers_are_applied():
    """Each Postman item must send the case's own URL/headers, not the baseline."""
    test_data = {
        "positive_test_cases": [
            {"id": "TC-POS-01", "title": "Get item", "expected": {"status_code": 200}},
        ],
        "negative_test_cases": [
            {
                "id": "TC-NEG-01",
                "title": "Missing Authorization header",
                "expected": {"status_code": 401},
                "request": {"headers": {"Authorization": "Bearer expired"}},
            },
            {
                "id": "TC-NEG-04",
                "title": "Non-numeric path parameter",
                "expected": {"status_code": 400},
                "request": {"endpoint": "https://api.example.com/items/abc"},
            },
        ],
    }
    collection = generate_postman_collection("https://api.example.com/items/1", "GET", test_data)
    data = json.loads(collection)
    items = {item["name"].split(" ")[0]: item for item in data["item"]}
    # positive hits the baseline
    assert items["TC-POS-01"]["request"]["url"]["raw"] == "https://api.example.com/items/1"
    assert items["TC-POS-01"]["request"]["header"] == []
    # negative carries its own header and mutated URL
    assert items["TC-NEG-01"]["request"]["header"] == [
        {"key": "Authorization", "value": "Bearer expired", "type": "text"}
    ]
    assert items["TC-NEG-04"]["request"]["url"]["raw"] == "https://api.example.com/items/abc"


def test_list_status_code_uses_oneOf():
    test_data = {
        "edge_cases": [
            {"id": "TC-EDGE-01", "title": "Rate limit burst", "expected": {"status_code": [200, 429]}},
        ]
    }
    collection = generate_postman_collection("https://api.example.com/items", "GET", test_data)
    data = json.loads(collection)
    exec_lines = data["item"][0]["event"][0]["script"]["exec"]
    assert any("to.be.oneOf([200, 429])" in line for line in exec_lines)
    assert not any("to.have.status([200" in line for line in exec_lines)


def test_non_numeric_status_code_degrades_gracefully():
    test_data = {
        "positive_test_cases": [
            {"id": "TC-1", "title": "Timeout", "expected": {"status_code": "client_timeout"}},
        ]
    }
    collection = generate_postman_collection("https://api.example.com/items", "GET", test_data)
    data = json.loads(collection)
    exec_lines = data["item"][0]["event"][0]["script"]["exec"]
    assert any("below(500)" in line for line in exec_lines)
    assert not any("client_timeout" in line for line in exec_lines)


# --- honest assertion-test regression guards ---

def test_ungroundable_assertion_is_skipped_not_faked():
    """Ungroundable rules must be marked manual/skipped, never a fabricated pass."""
    test_data = {
        "assertions": [
            {"rule": "Malformed Authorization header must not expose internal errors",
             "category": "security", "severity": "critical", "grounded": False},
            {"rule": 'body has field "id"', "category": "schema", "severity": "low", "grounded": True},
        ],
        "sample_response": {"id": 1, "title": "x"},
    }
    collection = generate_postman_collection("https://x.com/a", "GET", test_data)
    data = json.loads(collection)
    items = data["item"]
    assert len(items) == 2
    # ungroundable -> skipped/manual-marked
    ungrounded_lines = items[0]["event"][0]["script"]["exec"]
    assert any("pm.test.skip" in line and "[MANUAL]" in line for line in ungrounded_lines)
    assert not any("pm.expect(pm.response.code)" in line for line in ungrounded_lines)
    # still a real request item (groundable path untouched below)
    assert "request" in items[0]
    # groundable -> real pm.expect checks
    grounded_text = "\n".join(items[1]["event"][0]["script"]["exec"])
    assert 'pm.expect(jsonData["id"])' in grounded_text
    assert "pm.test.skip" not in grounded_text


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
