"""
Deterministic fallback generator.

Builds a schema-conformant set of API test cases from the endpoint, method, and
an optional sample response — no LLM required. Used as the resilience floor when
all AI providers fail, so the tool never hard-fails.
"""
from typing import Any, Dict, List, Optional


def _numeric_path_id(endpoint: str) -> Optional[str]:
    import re

    match = re.search(r"/([^/]+?)(?:[/?#]|$)", endpoint)
    if match:
        token = match.group(1)
        if token.isdigit():
            return token
    return None


def _replace_path_id(endpoint: str, old_id: str, new_id: str) -> str:
    import re

    return re.sub(rf"/{re.escape(old_id)}(?=[/?#]|$)", f"/{new_id}", endpoint, count=1)


def _top_level_fields(sample: Any) -> List[str]:
    """Return the top-level keys of a dict sample, or the first list element's keys."""
    if isinstance(sample, dict):
        return list(sample.keys())
    if isinstance(sample, list) and sample and isinstance(sample[0], dict):
        return list(sample[0].keys())
    return []


def _derive_assertions(cases: List[Dict[str, Any]], sample: Any) -> List[Dict[str, Any]]:
    """Build baseline assertions from generated cases + sample response keys."""
    assertions: List[Dict[str, Any]] = []
    seen_codes = set()
    for case in cases:
        if not isinstance(case, dict):
            continue
        expected = case.get("expected") or {}
        code = expected.get("status_code")
        if code and code not in seen_codes:
            seen_codes.add(code)
            assertions.append({
                "category": "status_code",
                "rule": f"Response status code is {code} for scenario: {case.get('title') or case.get('id')}",
                "severity": "high" if str(code).startswith("2") else "medium",
                "source": "deterministic",
            })
        for rule in expected.get("validation_rules") or []:
            assertions.append({
                "category": "schema",
                "rule": str(rule),
                "severity": "medium",
                "source": "deterministic",
            })

    # Field-existence assertions grounded in the real sample response
    for field in _top_level_fields(sample):
        assertions.append({
            "category": "schema",
            "rule": f"body has field '{field}'",
            "severity": "medium",
            "source": "deterministic",
        })

    assertions.append({
        "category": "schema",
        "rule": "Response body is valid JSON",
        "severity": "high",
        "source": "deterministic",
    })
    assertions.append({
        "category": "performance",
        "rule": "Response time is under 2000ms",
        "severity": "medium",
        "source": "deterministic",
    })
    assertions.append({
        "category": "security",
        "rule": "Error responses do not leak stack traces or internal details",
        "severity": "high",
        "source": "deterministic",
    })

    unique: List[Dict[str, Any]] = []
    seen_rules = set()
    for assertion in assertions:
        if assertion["rule"] not in seen_rules:
            seen_rules.add(assertion["rule"])
            unique.append(assertion)
    return unique[:12]


def generate_deterministic_cases(
    endpoint: str,
    method: str = "GET",
    sample_response: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Return a deterministic, schema-conformant generation result.

    The output has the same keys as the LLM path so the rest of the pipeline
    (grounding, persistence, rendering) can consume it unchanged.
    """
    method = (method or "GET").upper()
    object_id = _numeric_path_id(endpoint)

    positive: List[Dict[str, Any]] = []
    negative: List[Dict[str, Any]] = []
    edge: List[Dict[str, Any]] = []

    # --- Positive case ---
    positive_expected = {"status_code": 200}
    positive_validation = []
    if sample_response is not None:
        positive_validation.append("Response body is valid JSON")
        for field in _top_level_fields(sample_response):
            positive_validation.append(f"Field '{field}' is present")
    positive.append({
        "id": "TC-POS-01",
        "title": f"Valid {method} request returns success",
        "description": "Send a well-formed request with valid data and assert the expected success behavior.",
        "category": "positive",
        "request": {"endpoint": endpoint, "method": method},
        "expected": {
            "status_code": 200,
            "validation_rules": positive_validation,
        },
        "source": "deterministic",
    })

    # --- Negative cases ---
    negative.append({
        "id": "TC-NEG-01",
        "title": "Request without authentication",
        "description": "Remove the Authorization header; the API must reject unauthenticated access.",
        "category": "negative",
        "request": {"endpoint": endpoint, "method": method, "headers": {"Authorization": ""}},
        "expected": {"status_code": 401, "validation_rules": []},
        "source": "deterministic",
    })

    negative.append({
        "id": "TC-NEG-02",
        "title": "Request with invalid authentication token",
        "description": "Send an invalid bearer token; the API must reject it.",
        "category": "negative",
        "request": {"endpoint": endpoint, "method": method, "headers": {"Authorization": "Bearer invalid-token"}},
        "expected": {"status_code": 401, "validation_rules": []},
        "source": "deterministic",
    })

    if object_id:
        missing_endpoint = _replace_path_id(endpoint, object_id, "99999")
    else:
        missing_endpoint = endpoint.rstrip("/") + "/99999"
    negative.append({
        "id": "TC-NEG-03",
        "title": "Request for a non-existent resource",
        "description": "Request a resource that does not exist; the API must return 404.",
        "category": "negative",
        "request": {"endpoint": missing_endpoint, "method": method},
        "expected": {"status_code": 404, "validation_rules": []},
        "source": "deterministic",
    })

    if method == "GET":
        negative.append({
            "id": "TC-NEG-04",
            "title": "DELETE to a read-only endpoint",
            "description": "Send DELETE to a GET endpoint; the API must reject the unsupported method.",
            "category": "negative",
            "request": {"endpoint": endpoint, "method": "DELETE"},
            "expected": {"status_code": 405, "validation_rules": []},
            "source": "deterministic",
        })
    else:
        negative.append({
            "id": "TC-NEG-04",
            "title": "Malformed JSON body",
            "description": "Send invalid JSON in the request body; the API must return 400.",
            "category": "negative",
            "request": {"endpoint": endpoint, "method": method, "body": "{invalid json"},
            "expected": {"status_code": 400, "validation_rules": []},
            "source": "deterministic",
        })

    # --- Edge cases ---
    if method in ("POST", "PUT", "PATCH"):
        edge.append({
            "id": "TC-EDGE-01",
            "title": "Empty request body",
            "description": "Send an empty body to a write endpoint and verify graceful handling.",
            "category": "edge",
            "request": {"endpoint": endpoint, "method": method, "body": {}},
            "expected": {"status_code": 400, "validation_rules": []},
            "source": "deterministic",
        })
        edge.append({
            "id": "TC-EDGE-02",
            "title": "Extra unexpected field in body",
            "description": "Include an extra field not expected by the schema.",
            "category": "edge",
            "request": {"endpoint": endpoint, "method": method, "body": {"__unexpected_field__": "x"}},
            "expected": {"status_code": 200, "validation_rules": []},
            "source": "deterministic",
        })
    else:
        edge.append({
            "id": "TC-EDGE-01",
            "title": "Boundary value in query parameter",
            "description": "Send a very long query value to test input length handling.",
            "category": "edge",
            "request": {"endpoint": endpoint + "?q=" + "A" * 5000, "method": method},
            "expected": {"status_code": 200, "validation_rules": []},
            "source": "deterministic",
        })
        edge.append({
            "id": "TC-EDGE-02",
            "title": "Unsupported Accept header",
            "description": "Request a non-JSON representation and verify the API rejects or defaults correctly.",
            "category": "edge",
            "request": {"endpoint": endpoint, "method": method, "headers": {"Accept": "text/html"}},
            "expected": {"status_code": 406, "validation_rules": []},
            "source": "deterministic",
        })

    cases = positive + negative + edge
    assertions = _derive_assertions(cases, sample_response)

    return {
        "positive_test_cases": positive,
        "negative_test_cases": negative,
        "edge_cases": edge,
        "assertions": assertions,
        "_degraded": True,
        "_degraded_reason": "AI providers unavailable; generated baseline cases deterministically.",
    }
