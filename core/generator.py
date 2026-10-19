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
from core.database import redact_body, redact_response_text, _is_sensitive_body_key
from core.deterministic_generator import generate_deterministic_cases


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


_FIELD_QUOTED = re.compile(r'[\"\']([A-Za-z_][\w.]*)[\"\']')
_FIELD_DOTTED = re.compile(r'\b([A-Za-z_]\w*(?:\.\w+)+)\b')

_GROUNDING_STOPWORDS = {
    "response", "body", "field", "is", "a", "the", "of", "number", "string",
    "array", "object", "must", "should", "be", "not", "null", "present",
    "returns", "contains", "has", "and", "or", "valid", "json", "within",
}


def _resolve_path(obj: Any, path: str) -> bool:
    """True if a dotted path resolves in obj. For lists, check the first element."""
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            if part not in cur:
                return False
            cur = cur[part]
        elif isinstance(cur, list):
            if not cur or not isinstance(cur[0], dict) or part not in cur[0]:
                return False
            cur = cur[0][part]
        else:
            return False
    return True


def _resolve_value(obj: Any, path: str) -> tuple[bool, Any, bool]:
    """Return (found, value, from_list) for a dotted path. Same traversal rules
    as _resolve_path: lists use their first element."""
    cur = obj
    from_list = False
    for part in path.split("."):
        if isinstance(cur, dict):
            if part not in cur:
                return False, None, from_list
            cur = cur[part]
        elif isinstance(cur, list):
            from_list = True
            if not cur or not isinstance(cur[0], dict) or part not in cur[0]:
                return False, None, from_list
            cur = cur[0][part]
        else:
            return False, None, from_list
    return True, cur, from_list


def _field_present(response: Any, field: str) -> bool:
    if response is None:
        return False
    try:
        if "." in field:
            return _resolve_path(response, field)
        if isinstance(response, dict):
            return field in response
        if isinstance(response, list) and response and isinstance(response[0], dict):
            return field in response[0]
    except Exception:
        return False
    return False


def _field_value(response: Any, field: str) -> tuple[bool, Any, bool]:
    """Return (found, value, from_list) for a plain or dotted field name.
    Mirrors _field_present: dict key, or key on the first element of a
    top-level list; dotted names go through _resolve_value."""
    try:
        if "." in field:
            return _resolve_value(response, field)
        if isinstance(response, dict):
            if field in response:
                return True, response[field], False
            return False, None, False
        if isinstance(response, list) and response and isinstance(response[0], dict):
            if field in response[0]:
                return True, response[0][field], True
            return False, None, True
    except Exception:
        return False, None, False
    return False, None, False


_JSON_TYPE_NAMES = {
    type(None): "null",
    bool: "boolean",
    int: "integer",
    float: "number",
    str: "string",
    list: "array",
    dict: "object",
}


def _value_type(value: Any) -> str:
    return _JSON_TYPE_NAMES.get(type(value), type(value).__name__)


def _value_preview(value: Any) -> str:
    if isinstance(value, list):
        return f"array ({len(value)} items)"
    if isinstance(value, dict):
        return f"object ({len(value)} keys)"
    try:
        text = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(value)
    if len(text) > 60:
        text = text[:57] + "…"
    return text


_GROUNDING_MASKED = "***masked***"


def _mask_preview(preview: str, path: str) -> str:
    """Mask the preview if the path or the value itself looks sensitive."""
    segments = [s for s in str(path).split(".") if s]
    if any(
        _is_sensitive_body_key(seg) or "cookie" in seg.lower() or "session" in seg.lower()
        for seg in segments
    ):
        return _GROUNDING_MASKED
    return redact_response_text(preview)


def explain_grounding(rule: str, response: Any) -> Dict[str, Any]:
    """Explain why an assertion is (or isn't) verified against the response.

    Single source of truth for grounding: _is_grounded is a thin wrapper over
    the ``grounded`` key of this result, so the verdict can never drift from
    the evidence shown in the UI.
    """
    ev: Dict[str, Any] = {
        "grounded": False,
        "reason": "no_response",
        "path": None,
        "value_preview": None,
        "value_type": None,
        "from_list": False,
        "detail": "",
    }

    def _found(path: str, value: Any, from_list: bool) -> Dict[str, Any]:
        ev.update(
            reason="found",
            grounded=True,
            path=path,
            from_list=from_list,
            value_type=_value_type(value),
            value_preview=_mask_preview(_value_preview(value), path),
            detail="Found in the fetched response."
            + (" Checked against the first item of the list." if from_list else ""),
        )
        return ev

    if response is None or not isinstance(rule, str):
        ev["detail"] = (
            "There was no response to check against. "
            "Turn on auto-fetch or paste a sample response."
        )
        return ev

    text = rule.strip()
    low = text.lower()

    if "valid json" in low:
        ev.update(
            reason="valid_json",
            grounded=True,
            detail="The fetched response parsed as valid JSON.",
        )
        return ev
    if "response time" in low or "status code" in low or "status ==" in low:
        ev.update(
            reason="not_checkable",
            detail=(
                "Status codes and response times change per run, so one sample "
                "response can't prove them. They're checked when you run the tests."
            ),
        )
        return ev

    m = _FIELD_QUOTED.search(text)
    if m:
        path = m.group(1)
        found, value, from_list = _field_value(response, path)
        if found:
            return _found(path, value, from_list)
        ev.update(
            reason="not_in_response",
            path=path,
            detail=(
                f'The AI expected "{path}", but the fetched response has no such '
                "field. This test would fail or check nothing."
            ),
        )
        return ev
    m = _FIELD_DOTTED.search(text)
    if m:
        path = m.group(1)
        found, value, from_list = _field_value(response, path)
        if found:
            return _found(path, value, from_list)
        ev.update(
            reason="not_in_response",
            path=path,
            detail=(
                f'The AI expected "{path}", but the fetched response has no such '
                "field. This test would fail or check nothing."
            ),
        )
        return ev

    for tok in re.findall(r"\b[A-Za-z_]\w*\b", text):
        if tok.lower() in _GROUNDING_STOPWORDS:
            continue
        found, value, from_list = _field_value(response, tok)
        if found:
            return _found(tok, value, from_list)

    ev.update(
        reason="no_field_detected",
        detail=(
            "Couldn't tell which field this assertion refers to, so it can't be "
            "checked automatically. Review it manually."
        ),
    )
    return ev


def _is_grounded(rule: str, response: Any) -> bool:
    """Deterministic grounding: is this assertion observably true of `response`?"""
    return explain_grounding(rule, response)["grounded"]


def annotate_grounding(assertions: List[Dict[str, Any]], response: Any) -> List[Dict[str, Any]]:
    """Add 'grounded' (bool) and 'grounding' (evidence) to each assertion."""
    for a in assertions or []:
        if not isinstance(a, dict):
            continue
        try:
            ev = explain_grounding(a.get("rule", ""), response)
            a["grounded"] = ev["grounded"]
            a["grounding"] = ev
        except Exception:
            a["grounded"] = False
            a["grounding"] = None
    return assertions


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
    auth_state: Optional[Dict[str, Any]] = None,
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
        auth_state: Optional auth classification (classify_auth result), told
            to the AI as context. Header names only — never values.

    Returns:
        Dict conforming to the strict output JSON schema, or an error-safe variant.
    """
    if not endpoint or not isinstance(endpoint, str):
        return _safe_error_response("Invalid or missing 'endpoint' parameter.")

    try:
        # The analyzer copies sample values into the metadata and prompt, so a
        # token inside the response (e.g. a login endpoint returning an
        # access_token) would otherwise reach the AI provider. Mask a copy for
        # the prompt only — grounding below keeps the real response.
        prompt_sample = redact_body(sample_response) if sample_response is not None else None
        metadata = build_analysis_metadata(endpoint, method, prompt_sample)
    except Exception as e:
        return _safe_error_response(f"Analysis failed: {e}")

    try:
        user_prompt = build_user_prompt(
            endpoint=endpoint,
            method=method,
            sample_response=prompt_sample,
            response_metadata=metadata,
            auth_state=auth_state,
        )
    except Exception as e:
        return _safe_error_response(f"Prompt building failed: {e}")

    try:
        client = mistral_client or AIRouter()
    except Exception as e:
        # No providers available at all — degrade to deterministic floor rather than hard-failing.
        parsed = generate_deterministic_cases(endpoint, method, sample_response)
        parsed["_degraded_reason"] = f"AI providers unavailable ({e}); generated baseline cases deterministically."
        parsed["_degraded"] = True
        # Skip the LLM path entirely and proceed through the shared finalization below.
        client = None

    if client is not None:
        parsed, error = _request_and_parse(client, user_prompt)
        if error is not None:
            # LLM path failed entirely — degrade to the deterministic floor.
            parsed = generate_deterministic_cases(endpoint, method, sample_response)
            parsed["_degraded_reason"] = f"AI providers unavailable ({error.get('_error', 'unknown')}); generated baseline cases deterministically."
            parsed["_degraded"] = True

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

    # Grounding: mark which assertions are backed by the real fetched response.
    try:
        parsed["assertions"] = annotate_grounding(parsed.get("assertions") or [], sample_response)
    except Exception:
        pass  # never break generation over grounding

    # Record which AI provider produced the result (for UI badge)
    provider = getattr(client, "last_provider", None)
    if provider:
        parsed["_provider"] = provider

    return parsed
