"""
Shared auth-state classifier — the single source of truth for "does this
endpoint need a login". Pure: no network, no side effects.

Used by test generation (web/routes/generate.py), the security scanner
(core/security_scanner.py), the runner (core/test_executor.py) and exports.
Credential detection reuses ``_SENSITIVE_HEADER_HINTS`` from core.database;
only header NAMES are ever used here — values are never logged, shown, or
sent to the AI.
"""
from typing import Any, Dict, List, Optional

from core.database import _SENSITIVE_HEADER_HINTS

AUTH_STATES = ("public", "authenticated", "rejected", "required", "unknown")


def credential_header_names(headers: Optional[Dict[str, Any]]) -> List[str]:
    """Names of headers that carry credentials (uses _SENSITIVE_HEADER_HINTS).

    Names only, never values. Non-dict input yields an empty list.
    """
    if not isinstance(headers, dict):
        return []
    names = []
    for key in headers:
        lowered = str(key).lower()
        if any(hint in lowered for hint in _SENSITIVE_HEADER_HINTS):
            names.append(str(key))
    return names


def classify_auth(
    headers: Optional[Dict[str, Any]], status: Optional[int]
) -> Dict[str, Any]:
    """
    Classify the endpoint's auth state from the user's own request.

    Returns {"state": <one of AUTH_STATES>,
             "status": <int or None>,
             "credential_headers": [<names only>]}

    401 and 403 are treated the same — APIs use them inconsistently.
    Anything that isn't 2xx/401/403 (or no response at all) is "unknown".
    """
    creds = credential_header_names(headers)
    if isinstance(status, bool) or not isinstance(status, int):
        state = "unknown"
        status = None
    elif 200 <= status < 300:
        state = "authenticated" if creds else "public"
    elif status in (401, 403):
        state = "rejected" if creds else "required"
    else:
        state = "unknown"
    return {"state": state, "status": status, "credential_headers": creds}


def _normalize_expected_status(expected: Any) -> Optional[List[int]]:
    """Expected status can be an int, a numeric string, or a list of them."""
    if expected is None:
        return None
    values = expected if isinstance(expected, (list, tuple)) else [expected]
    codes: List[int] = []
    for value in values:
        try:
            codes.append(int(value))
        except (TypeError, ValueError):
            continue
    return codes or None


def is_auth_test(case: Dict[str, Any]) -> bool:
    """True if the case expects only 401/403 (int, numeric string, or a list
    containing only 401/403). Such a test is a login test: it can only pass
    on an endpoint that actually rejects unauthenticated requests."""
    if not isinstance(case, dict):
        return False
    expected = case.get("expected_status")
    if expected is None:
        expected = (case.get("expected") or {}).get("status_code")
    codes = _normalize_expected_status(expected)
    return bool(codes) and all(c in (401, 403) for c in codes)
