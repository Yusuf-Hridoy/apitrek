"""
Analyzer module for extracting metadata from API inputs.
Enriches prompts with structural insights about endpoints and responses.
"""
import re
from typing import Any, Dict, List, Optional


def infer_type(value: Any) -> str:
    """Infer a human-readable type string from a Python value."""
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
        if value:
            item_type = infer_type(value[0])
            return f"array<{item_type}>"
        return "array<unknown>"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def analyze_fields(data: Any, parent_path: str = "") -> List[Dict[str, Any]]:
    """
    Recursively analyze fields in a nested structure.

    Returns a flat list of field descriptors with path, type, and sample value.
    """
    fields = []

    if isinstance(data, dict):
        for key, value in data.items():
            current_path = f"{parent_path}.{key}" if parent_path else key
            field_info = {
                "path": current_path,
                "type": infer_type(value),
                "sample": value if not isinstance(value, (dict, list)) else "...",
                "is_nested": isinstance(value, (dict, list)),
            }
            fields.append(field_info)

            if isinstance(value, (dict, list)):
                fields.extend(analyze_fields(value, current_path))

    elif isinstance(data, list) and data:
        # Analyze first item as representative
        fields.extend(analyze_fields(data[0], parent_path))

    return fields


def analyze_sample_response(response: Optional[Any]) -> Dict[str, Any]:
    """
    Analyze a sample API response and return metadata.

    Supports both JSON objects and JSON arrays.

    Returns:
        Dict with field_count, fields, top_level_keys, data_types, and patterns.
    """
    if not response:
        return {
            "field_count": 0,
            "fields": [],
            "top_level_keys": [],
            "data_types": [],
            "patterns": [],
            "note": "No sample response provided",
        }

    is_array = isinstance(response, list)
    target = response[0] if is_array and response else response

    fields = analyze_fields(target)
    top_level_keys = list(target.keys()) if isinstance(target, dict) else []
    if is_array:
        top_level_keys.insert(0, "[array]")

    data_types = sorted(set(f["type"] for f in fields))

    patterns = []
    for f in fields:
        if f["type"] == "string" and f["sample"] != "...":
            val = str(f["sample"])
            if re.match(r"^\d{4}-\d{2}-\d{2}", val):
                patterns.append({"path": f["path"], "pattern": "ISO date format"})
            elif "@" in val and "." in val:
                patterns.append({"path": f["path"], "pattern": "email-like format"})
            elif val.startswith("http://") or val.startswith("https://"):
                patterns.append({"path": f["path"], "pattern": "URL format"})
            elif val.isdigit():
                patterns.append({"path": f["path"], "pattern": "numeric string"})

    return {
        "field_count": len(fields),
        "fields": fields,
        "top_level_keys": top_level_keys,
        "data_types": data_types,
        "patterns": patterns,
    }


def _infer_endpoint_category(segments: List[str]) -> str:
    """Infer the functional domain of an endpoint from its path segments."""
    path_lower = " ".join(segments).lower()
    if any(k in path_lower for k in ("auth", "login", "token", "oauth", "session", "signin", "signup")):
        return "authentication"
    if any(k in path_lower for k in ("user", "account", "profile", "member")):
        return "user_management"
    if any(k in path_lower for k in ("upload", "file", "media", "image", "document")):
        return "file_media"
    if any(k in path_lower for k in ("payment", "checkout", "order", "cart", "billing", "price", "subscription")):
        return "commerce"
    if any(k in path_lower for k in ("admin", "role", "permission", "acl", "config")):
        return "administration"
    if any(k in path_lower for k in ("search", "filter", "query")):
        return "search"
    if any(k in path_lower for k in ("webhook", "callback", "event", "hook")):
        return "webhooks"
    return "general"


def _infer_param_type(segment: str) -> Optional[str]:
    """Classify a path parameter as UUID, numeric ID, or string slug."""
    if re.match(r"^[0-9a-fA-F-]{36}$", segment):
        return "uuid"
    if segment.isdigit():
        return "numeric_id"
    if re.match(r"^[a-z0-9_-]+$", segment, re.IGNORECASE):
        return "slug"
    return None


def analyze_endpoint(endpoint: str) -> Dict[str, Any]:
    """
    Analyze an endpoint URL and extract structural metadata.

    Returns:
        Dict with path_segments, has_path_param, inferred_resource, method_hints,
        endpoint_category, and path_param_type.
    """
    if not endpoint:
        return {"error": "Empty endpoint"}

    parsed = re.sub(r"https?://", "", endpoint)
    parts = parsed.split("/")
    segments = [p for p in parts if p]

    # Detect path parameters: numeric IDs, UUIDs, or slug-like last segments
    last_seg = segments[-1] if segments else ""
    has_path_param = (
        last_seg.isdigit()
        or bool(re.match(r"^[0-9a-fA-F-]{36}$", last_seg))
        or bool(re.match(r"^[a-z0-9]+[_-][a-z0-9_-]+$", last_seg, re.IGNORECASE))
    )

    inferred_resource = segments[-2] if len(segments) > 1 and has_path_param else segments[-1] if segments else "unknown"

    method_hints = []
    if has_path_param:
        method_hints.extend(["GET (retrieve one)", "PUT (update)", "DELETE (remove)", "PATCH (partial update)"])
    else:
        method_hints.extend(["GET (list)", "POST (create)"])

    category = _infer_endpoint_category(segments)

    param_type = _infer_param_type(last_seg) if has_path_param else None

    return {
        "path_segments": segments,
        "has_path_param": has_path_param,
        "inferred_resource": inferred_resource,
        "method_hints": method_hints,
        "endpoint_category": category,
        "path_param_type": param_type,
    }


def build_analysis_metadata(
    endpoint: str,
    method: str = "GET",
    sample_response: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Combine endpoint and response analysis into a single metadata dict.

    This is used to enrich the LLM prompt with structural context.
    """
    return {
        "endpoint_analysis": analyze_endpoint(endpoint),
        "response_analysis": analyze_sample_response(sample_response),
        "provided_method": method,
    }
