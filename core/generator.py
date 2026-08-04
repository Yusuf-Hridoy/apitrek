"""
Core generator module.
Orchestrates prompt formatting, LLM calls, and JSON validation.
"""
import json
import re
from typing import Any, Dict, List, Optional

from llm.mistral_client import MistralClient, MistralClientError, MistralTruncationError
from llm.ai_router import AIRouter, AllProvidersFailedError
from llm.prompt_templates import SYSTEM_PROMPT, build_user_prompt
from core.analyzer import build_analysis_metadata


EXPECTED_TOP_KEYS = {
    "positive_test_cases",
    "negative_test_cases",
    "edge_cases",
    "assertions",
}


def _extract_json(text: str) -> Optional[str]:
    """
    Extract JSON payload from text that may contain markdown or extra whitespace.

    Tries to find the outermost JSON object using brace matching.
    """
    text = text.strip()

    # Remove markdown code fences if present
    if text.startswith("```"):
        lines = text.splitlines()
        # Drop first fence
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        # Drop last fence
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # Try to find the first '{' and last '}'
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start:end + 1]

    return None


def _repair_truncated_json(text: str) -> Optional[str]:
    """
    Attempt to repair JSON that was cut off mid-generation.

    Tries progressively aggressive strategies:
    1. Simple fixes (unterminated strings, trailing commas, missing closes)
    2. Truncate to the last complete JSON element and close all open structures
    """
    repaired = text.strip()

    # --- Strategy 1: Simple fixes ---

    # Close unterminated strings
    quote_count = 0
    escaped = False
    for ch in repaired:
        if ch == "\\" and not escaped:
            escaped = True
        elif ch == '"' and not escaped:
            quote_count += 1
            escaped = False
        else:
            escaped = False

    if quote_count % 2 != 0:
        repaired = repaired + '"'

    # Remove trailing commas before closing braces/brackets
    repaired = re.sub(r',(\s*[}\]])', r'\1', repaired)

    # Remove trailing comma at the very end
    while repaired and repaired[-1] == ",":
        repaired = repaired[:-1]

    # Balance braces and brackets
    open_braces = repaired.count("{") - repaired.count("}")
    open_brackets = repaired.count("[") - repaired.count("]")

    for _ in range(open_brackets):
        repaired += "]"
    for _ in range(open_braces):
        repaired += "}"

    try:
        json.loads(repaired)
        return repaired
    except json.JSONDecodeError:
        pass

    # --- Strategy 2: Truncate to last complete element ---
    # Walk backward looking for a safe truncation point after a complete value
    for i in range(len(repaired) - 1, -1, -1):
        ch = repaired[i]
        # Safe truncation points: after }, ], or a complete string/number/bool/null
        if ch in ('}', ']', '"'):
            candidate = repaired[: i + 1]
            # Strip any trailing comma before our truncation point
            candidate = candidate.rstrip()
            if candidate.endswith(','):
                candidate = candidate[:-1].rstrip()

            open_braces = candidate.count("{") - candidate.count("}")
            open_brackets = candidate.count("[") - candidate.count("]")

            for _ in range(open_brackets):
                candidate += "]"
            for _ in range(open_braces):
                candidate += "}"

            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                continue

    return None


def _validate_structure(data: Dict[str, Any]) -> bool:
    """Check that the generated payload contains all required top-level keys."""
    return EXPECTED_TOP_KEYS.issubset(set(data.keys()))


def _derive_default_assertions(parsed: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Build baseline assertions deterministically from the generated test cases.

    Used as a permanent fallback when the LLM runs out of output budget before
    reaching the assertions section — the section is always derivable from the
    cases that were generated, so it should never be left empty.
    """
    assertions: List[Dict[str, Any]] = []
    seen_codes = set()
    for key in ("positive_test_cases", "negative_test_cases", "edge_cases"):
        for case in parsed.get(key) or []:
            if not isinstance(case, dict):
                continue
            expected = case.get("expected") or {}
            code = expected.get("status_code")
            if code and code not in seen_codes:
                seen_codes.add(code)
                assertions.append({
                    "category": "status_code",
                    "rule": (
                        f"Response status code is {code} for scenario: "
                        f"{case.get('title') or case.get('id') or 'unknown'}"
                    ),
                    "severity": "high" if str(code).startswith("2") else "medium",
                })
            for rule in expected.get("validation_rules") or []:
                assertions.append({
                    "category": "schema",
                    "rule": str(rule),
                    "severity": "medium",
                })

    assertions.append({"category": "schema", "rule": "Response body is valid JSON", "severity": "high"})
    assertions.append({"category": "performance", "rule": "Response time is under 2000ms", "severity": "medium"})
    assertions.append({
        "category": "security",
        "rule": "Error responses do not leak stack traces or internal details",
        "severity": "high",
    })

    # De-duplicate by rule text and cap the list
    unique: List[Dict[str, Any]] = []
    seen_rules = set()
    for assertion in assertions:
        if assertion["rule"] not in seen_rules:
            seen_rules.add(assertion["rule"])
            unique.append(assertion)
    return unique[:8]


def _safe_error_response(error_message: str) -> Dict[str, Any]:
    """Return a structured error JSON that conforms to the expected schema."""
    return {
        "positive_test_cases": [],
        "negative_test_cases": [],
        "edge_cases": [],
        "assertions": [],
        "_error": error_message,
    }


def _request_and_parse(
    client: MistralClient, user_prompt: str
) -> "tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]":
    """
    Call the LLM and parse its JSON response.

    Returns (parsed, None) on success or (None, error_response) on failure.
    """
    try:
        raw_response = client.send_prompt(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.2,
            max_tokens=8192,
        )
    except MistralTruncationError as e:
        return None, _safe_error_response(
            f"AI response was too long and got cut off. {e} "
            f"Try providing a shorter sample response or a simpler endpoint."
        )
    except MistralClientError as e:
        return None, _safe_error_response(f"Mistral API error: {e}")
    except AllProvidersFailedError as e:
        return None, _safe_error_response(f"All AI providers failed: {e}")
    except Exception as e:
        return None, _safe_error_response(f"Unexpected LLM error: {e}")

    # Extract JSON from response
    json_text = _extract_json(raw_response)
    if not json_text:
        return None, _safe_error_response(
            f"Could not extract valid JSON from LLM response. Raw response: {raw_response[:500]}"
        )

    # Parse JSON
    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError as e:
        # Attempt repair for truncated responses
        repaired = _repair_truncated_json(json_text)
        if repaired:
            try:
                parsed = json.loads(repaired)
            except json.JSONDecodeError:
                return None, _safe_error_response(
                    f"Malformed JSON from LLM: {e}. Snippet: {json_text[:500]}"
                )
        else:
            return None, _safe_error_response(
                f"Malformed JSON from LLM: {e}. Snippet: {json_text[:500]}"
            )

    if not isinstance(parsed, dict):
        return None, _safe_error_response("Parsed LLM response is not a JSON object.")

    return parsed, None


def generate_test_cases(
    endpoint: str,
    method: str = "GET",
    sample_response: Optional[Any] = None,
    mistral_client: Optional[MistralClient] = None,
) -> Dict[str, Any]:
    """
    Generate structured API test cases for the given endpoint.

    Args:
        endpoint: The API endpoint URL.
        method: HTTP method.
        sample_response: Optional sample response body.
        mistral_client: Optional pre-configured LLM client instance (any client
            with a .send_prompt method). Defaults to AIRouter, which uses
            Mistral with automatic fallback to Groq / GitHub Models.

    Returns:
        Dict conforming to the strict output JSON schema, or an error-safe variant.
    """
    if not endpoint or not isinstance(endpoint, str):
        return _safe_error_response("Invalid or missing 'endpoint' parameter.")

    try:
        metadata = build_analysis_metadata(endpoint, method, sample_response)
    except Exception as e:
        return _safe_error_response(f"Analysis failed: {e}")

    try:
        user_prompt = build_user_prompt(
            endpoint=endpoint,
            method=method,
            sample_response=sample_response,
            response_metadata=metadata,
        )
    except Exception as e:
        return _safe_error_response(f"Prompt building failed: {e}")

    try:
        client = mistral_client or AIRouter()
    except Exception as e:
        return _safe_error_response(f"Unexpected LLM error: {e}")

    parsed, error = _request_and_parse(client, user_prompt)
    if error is not None:
        return error

    # Validate structure — if keys are missing (usually a truncated response
    # whose tail sections got cut off), retry asking ONLY for the missing
    # sections and merge them in. Asking for the full object again would just
    # truncate at the same point. Stop early when a retry makes no progress.
    missing_keys = EXPECTED_TOP_KEYS - set(parsed.keys())
    retries = 0
    while missing_keys and retries < 3:
        retries += 1
        retry_prompt = user_prompt + (
            f"\n\nIMPORTANT: Your previous response was missing these required "
            f"top-level keys: {sorted(missing_keys)}. Return ONLY a JSON object "
            f"containing JUST those missing keys, following the same schema for "
            f"each section. Do not repeat any keys you already returned."
        )
        retry_parsed, retry_error = _request_and_parse(client, retry_prompt)
        if retry_error is not None:
            break
        merged = False
        for key in sorted(missing_keys):
            value = retry_parsed.get(key)
            if isinstance(value, list) and value:
                parsed[key] = value
                missing_keys.discard(key)
                merged = True
        if not merged:
            break

    # Assertions are always derivable from the generated cases — never leave
    # the section empty just because the LLM ran out of output budget.
    if "assertions" in missing_keys:
        parsed["assertions"] = _derive_default_assertions(parsed)
        missing_keys.discard("assertions")

    if missing_keys:
        # Populate missing keys with empty lists so the user still gets usable results
        for key in missing_keys:
            parsed[key] = []
        parsed.setdefault("_error", "")
        parsed["_error"] += f" Note: LLM omitted keys {missing_keys}; filled with empty lists."

    # Ensure all values are lists (defensive)
    for key in EXPECTED_TOP_KEYS:
        if not isinstance(parsed.get(key), list):
            parsed[key] = []

    # Record which AI provider produced the result (for UI badge)
    provider = getattr(client, "last_provider", None)
    if provider:
        parsed["_provider"] = provider

    return parsed
