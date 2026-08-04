"""
OpenAPI 3.0/3.1 specification parser.

Auto-detects JSON vs YAML, resolves local $ref pointers, and extracts a
flat list of endpoints with their parameters and schemas. Deterministic —
no LLM calls.
"""
import json
from typing import Any, Dict, List, Optional

import yaml

_HTTP_METHODS = ("get", "put", "post", "delete", "patch", "options", "head", "trace")
_MAX_REF_DEPTH = 20


def _resolve_refs(node: Any, spec: Dict[str, Any], depth: int = 0) -> Any:
    """Resolve local #/... $ref pointers recursively (depth-limited)."""
    if depth > _MAX_REF_DEPTH:
        return node
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/"):
            target: Any = spec
            try:
                for part in ref[2:].split("/"):
                    target = target[part]
            except (KeyError, TypeError):
                return node
            merged = {k: v for k, v in node.items() if k != "$ref"}
            resolved = _resolve_refs(target, spec, depth + 1)
            if merged and isinstance(resolved, dict):
                return {**resolved, **merged}
            return resolved
        return {k: _resolve_refs(v, spec, depth + 1) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve_refs(item, spec, depth + 1) for item in node]
    return node


def _detect_and_load(spec_text: str) -> Dict[str, Any]:
    """Parse spec text as JSON first, then YAML. Raises ValueError on failure."""
    text = spec_text.strip()
    if not text:
        raise ValueError("Spec content is empty.")

    try:
        return json.loads(text)
    except json.JSONDecodeError as json_err:
        try:
            loaded = yaml.safe_load(text)
        except yaml.YAMLError as yaml_err:
            raise ValueError(
                f"Spec is neither valid JSON nor valid YAML. "
                f"JSON error: {json_err}. YAML error: {yaml_err}"
            )
        if not isinstance(loaded, dict):
            raise ValueError("Spec parsed as YAML but is not a mapping/object.")
        return loaded


def _extract_json_schema(media_content: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Pull a JSON schema out of a content block (application/json preferred)."""
    if not isinstance(media_content, dict):
        return None
    for mime, media in media_content.items():
        if "json" in mime.lower() and isinstance(media, dict) and media.get("schema"):
            return media["schema"]
    return None


def _parse_operation(
    path: str,
    method: str,
    operation: Dict[str, Any],
    path_parameters: List[Dict[str, Any]],
    global_security: List[Any],
) -> Dict[str, Any]:
    parameters = []
    for param in [*path_parameters, *(operation.get("parameters") or [])]:
        if not isinstance(param, dict):
            continue
        parameters.append({
            "name": param.get("name", ""),
            "in": param.get("in", "query"),
            "required": bool(param.get("required", False)),
            "schema": param.get("schema"),
            "description": param.get("description", ""),
        })

    request_body = operation.get("requestBody") or {}
    request_body_schema = _extract_json_schema(request_body.get("content") or {})

    response_schemas: Dict[str, Any] = {}
    for status, response in (operation.get("responses") or {}).items():
        if isinstance(response, dict):
            schema = _extract_json_schema(response.get("content") or {})
            if schema:
                response_schemas[str(status)] = schema

    return {
        "path": path,
        "method": method.upper(),
        "summary": operation.get("summary") or operation.get("operationId") or "",
        "parameters": parameters,
        "request_body_schema": request_body_schema,
        "response_schemas": response_schemas,
        "security": operation.get("security", global_security),
    }


def parse_openapi(spec_text: str) -> Dict[str, Any]:
    """
    Parse an OpenAPI 3.0/3.1 spec (JSON or YAML) into a flat endpoint list.

    Returns a dict with valid/error/info/base_url/endpoints — never raises.
    """
    result: Dict[str, Any] = {
        "valid": False,
        "error": None,
        "info": {"title": "", "version": ""},
        "base_url": "",
        "endpoints": [],
    }

    try:
        spec = _detect_and_load(spec_text)
    except ValueError as e:
        result["error"] = str(e)
        return result

    if not isinstance(spec, dict):
        result["error"] = "Spec is not a JSON/YAML object."
        return result

    version = str(spec.get("openapi", ""))
    if not version:
        result["error"] = (
            "Missing 'openapi' version field. Note: Swagger 2.0 specs are not "
            "supported — convert to OpenAPI 3.x first."
        )
        return result
    if not version.startswith(("3.0", "3.1")):
        result["error"] = f"Unsupported OpenAPI version '{version}'. Only 3.0 and 3.1 are supported."
        return result

    info = spec.get("info") or {}
    result["info"] = {
        "title": str(info.get("title", "")),
        "version": str(info.get("version", "")),
    }

    servers = spec.get("servers") or []
    if servers and isinstance(servers[0], dict):
        result["base_url"] = str(servers[0].get("url", ""))

    resolved = _resolve_refs(spec.get("paths") or {}, spec)
    global_security = spec.get("security") or []

    endpoints: List[Dict[str, Any]] = []
    for path, path_item in resolved.items():
        if not isinstance(path_item, dict):
            continue
        path_parameters = path_item.get("parameters") or []
        for method in _HTTP_METHODS:
            operation = path_item.get(method)
            if isinstance(operation, dict):
                endpoints.append(
                    _parse_operation(path, method, operation, path_parameters, global_security)
                )

    if not endpoints:
        result["error"] = "Spec is valid OpenAPI but contains no endpoints under 'paths'."
        return result

    result["valid"] = True
    result["endpoints"] = endpoints
    return result


def extract_endpoint_schemas(parsed: Dict[str, Any], path: str, method: str) -> Dict[str, Any]:
    """Return the schema details for one endpoint from a parsed spec."""
    if not parsed.get("valid"):
        return {"found": False, "error": parsed.get("error") or "Spec is not valid."}

    method = method.upper()
    for endpoint in parsed.get("endpoints", []):
        if endpoint["path"] == path and endpoint["method"] == method:
            return {
                "found": True,
                "path": path,
                "method": method,
                "parameters": endpoint["parameters"],
                "request_body_schema": endpoint["request_body_schema"],
                "response_schemas": endpoint["response_schemas"],
                "security": endpoint["security"],
            }

    return {"found": False, "error": f"Endpoint {method} {path} not found in spec."}
