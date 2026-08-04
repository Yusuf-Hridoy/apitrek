"""
Structured prompt templates for API test generation.
Enforces strict JSON-only output from the AI.
"""
import json
from typing import Any, Dict, Optional


SYSTEM_PROMPT = """You are a Staff QA Engineer with 10 years of breaking production APIs. You do not write generic test cases — you write scenarios that find real bugs.

You think in failure modes: race conditions, auth edge cases, payload corruption, schema drift, injection vectors, and client retry storms. You know the difference between a naive "invalid input" test and a realistic "token expires mid-request during a retry storm" test.

CRITICAL RULES:
1. Output ONLY valid JSON. No markdown, no code blocks, no explanations outside the JSON.
2. Never wrap output in ```json or ``` fences.
3. Every test case must describe a concrete, realistic failure — not a category label.
4. Prioritize depth and realism over volume. Five brilliant cases beat fifteen generic ones.
5. Avoid template language like "test invalid X" or "check edge case Y". Describe the exact condition.
6. Consider the endpoint category: auth endpoints need token abuse cases, commerce endpoints need price manipulation cases, file endpoints need size/boundary cases.
"""


def build_user_prompt(
    endpoint: str,
    method: str = "GET",
    sample_response: Optional[Dict[str, Any]] = None,
    response_metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Build the user prompt for test generation with advanced QA intelligence.

    Args:
        endpoint: The API endpoint URL.
        method: HTTP method (GET, POST, PUT, DELETE, PATCH).
        sample_response: Optional sample response body.
        response_metadata: Optional metadata from the analyzer (field types, patterns, etc.).

    Returns:
        A formatted prompt string.
    """
    sample_json = json.dumps(sample_response, indent=2, ensure_ascii=False) if sample_response else "None provided"
    metadata_json = json.dumps(response_metadata, indent=2, ensure_ascii=False) if response_metadata else "None"

    endpoint_analysis = (
        response_metadata.get("endpoint_analysis", {}) if isinstance(response_metadata, dict) else {}
    )
    category = endpoint_analysis.get("endpoint_category", "general")
    param_type = endpoint_analysis.get("path_param_type") or "none"
    resource = endpoint_analysis.get("inferred_resource", "unknown")
    method_hints = endpoint_analysis.get("method_hints", [])

    endpoint_context = f"""ENDPOINT CONTEXT:
- Domain: {category}
- Resource: {resource}
- Path parameter type: {param_type}
- Supported methods: {', '.join(method_hints) if method_hints else 'inferred from URL'}"""

    prompt = f"""Analyze this API like a production QA engineer hunting real bugs.

{endpoint_context}

API ENDPOINT: {endpoint}
HTTP METHOD: {method}

SAMPLE RESPONSE:
{sample_json}

RESPONSE METADATA:
{metadata_json}

QA INTELLIGENCE FRAMEWORK — generate cases across these dimensions:

1. AUTHENTICATION & AUTHORIZATION
   Generate concrete cases for: missing token, expired token, malformed Authorization header, token with insufficient scope, wrong auth scheme (Basic instead of Bearer), revoked session.

2. DATA VALIDATION & BOUNDARIES
   Generate concrete cases for: null in required fields, empty strings where data is expected, wrong types (string sent as integer), oversized values beyond max length, invalid enum values, negative numbers in quantity fields, extreme numeric boundaries.

3. RATE LIMITING & STABILITY
   Generate concrete cases for: rapid sequential requests (burst), concurrent duplicate requests testing idempotency, retry behavior after a 5xx failure, slow client timeout simulation.

4. SCHEMA RESILIENCE & BREAKING CHANGES
   Generate concrete cases for: missing required fields in request body, extra fields not defined in schema (forward compatibility), deeply nested object corruption, malformed JSON body (trailing commas, unclosed braces), wrong Content-Type header.

5. SECURITY & INJECTION SAFETY
   Generate concrete cases for: SQL injection attempts in string fields, script injection payloads in text inputs, invalid or unexpected headers, path traversal in file/path parameters, CRLF injection in headers.
   Keep payloads high-level and safe — describe the attack vector, do not generate executable exploit code.

OUTPUT FORMAT — return ONLY this JSON structure:
{{
  "positive_test_cases": [
    {{
      "id": "TC-POS-01",
      "title": "Descriptive title",
      "description": "What is being tested",
      "request": {{
        "endpoint": "url",
        "method": "GET",
        "headers": {{}},
        "body": {{}}
      }},
      "expected": {{
        "status_code": 200,
        "validation_rules": ["rule1", "rule2"]
      }}
    }}
  ],
  "negative_test_cases": [
    {{
      "id": "TC-NEG-01",
      "title": "Descriptive title",
      "description": "What invalid input is tested",
      "request": {{...}},
      "expected": {{
        "status_code": 400,
        "error_message_pattern": "optional regex or description"
      }}
    }}
  ],
  "edge_cases": [
    {{
      "id": "TC-EDGE-01",
      "title": "Descriptive title",
      "description": "Boundary or extreme condition",
      "request": {{...}},
      "expected": {{...}}
    }}
  ],
  "assertions": [
    {{
      "category": "status_code|schema|field|header|performance|security",
      "rule": "Specific assertion rule",
      "severity": "critical|high|medium|low"
    }}
  ]
}}

INSTRUCTIONS:
- Positive (2-3): realistic success paths that vary state — first call, cached call, different valid IDs. Not "valid request".
- Negative (4-5): concrete auth failures, validation violations, and security probes from the framework above. Each must name the exact bad input.
- Edge (4-5): stability and schema-breaking scenarios. Include at least one rate-limit case and one schema-resilience case.
- Assertions (5-6): status code, schema structure, field-level rules, header expectations, data integrity, security baseline.
- Endpoint-aware: A {category} endpoint needs {category}-specific cases. A path param of type {param_type} needs param-specific corruption (e.g., invalid UUID format, negative numeric ID).
- NEVER use generic descriptions. "Send negative price in checkout" is good; "Test invalid data" is unacceptable.
- Use realistic values, not placeholders.
- Ensure unique IDs across all case types.
- IMPORTANT: Do NOT include request.body details unless the method is POST, PUT, or PATCH. For GET/DELETE, omit the body field entirely to save space.
"""
    return prompt
