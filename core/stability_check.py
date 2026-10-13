"""
Stability check: compare repeated GET responses and classify each field.

Pure functions, no network — the route makes the repeat calls through the
existing SSRF-guarded fetch path and hands the parsed responses here.

Honesty principle: "stable" means "stable during the check" (3 calls, back to
back), not "guaranteed". Value changes are informational (Apitrek's checks
never compare exact values); shape changes (missing sometimes, type switching,
empty lists) are what make tests flaky, and those are what the exports learn
to tolerate.
"""
import json
from typing import Any, Dict, List, Optional

from core.generator import _value_preview, _mask_preview

MISSING = "(missing)"

# Internal key holding raw call-1 values, used to detect rules that pin one
# exact value. Never leaves the server: apply_stability_to_assertions() pops
# it, and only masked previews go into fields["values"].
_RAW_FIRST = "_raw_first"

# Sentinel for "path absent in this call" while classifying.
_ABSENT = object()

_JSON_TYPE_NAMES = {
    type(None): "null",
    bool: "boolean",
    int: "integer",
    float: "number",
    str: "string",
    list: "array",
    dict: "object",
}


def _json_type(value: Any) -> str:
    return _JSON_TYPE_NAMES.get(type(value), type(value).__name__)


def _collect(response: Any) -> Dict[str, Any]:
    """Map dotted field path -> value, using the grounding convention: nested
    objects join with '.', lists descend into the first element (so a list
    field keeps its own path, e.g. 'items', and its items appear as
    'items.sku'). Intermediate dicts are not entries — their leaf children
    carry any change. A top-level list response contributes its first
    element's fields (no named root entry)."""
    out: Dict[str, Any] = {}

    def walk(node: Any, prefix: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                p = f"{prefix}.{k}" if prefix else str(k)
                if isinstance(v, dict):
                    walk(v, p)
                elif isinstance(v, list):
                    out[p] = v
                    if v and isinstance(v[0], dict):
                        walk(v[0], p)
                else:
                    out[p] = v
        elif isinstance(node, list) and node and isinstance(node[0], dict):
            walk(node[0], prefix)

    walk(response, "")
    return out


def _same_elements_different_order(values: List[list]) -> bool:
    """True if every call has the same multiset of elements but at least one
    call orders them differently (sorted JSON dumps are all equal)."""
    dumped = [
        sorted(json.dumps(e, sort_keys=True, ensure_ascii=False) for e in v)
        for v in values
    ]
    return all(d == dumped[0] for d in dumped[1:])


def _entry_for(path: str, vals: List[Any]) -> Dict[str, Any]:
    """Classify one path. vals has one slot per call, _ABSENT where missing.

    Classification order:
      1. present in some calls, absent in others -> flaky / missing_sometimes
      2. JSON type differs across calls          -> flaky / type_changes
      3. list empty in some calls                -> flaky / empty_sometimes
      4. same list elements, different order     -> dynamic / order_changes
      5. same type, values differ                -> dynamic / value_changes
      6. identical in every call                 -> stable / same
    """
    calls = len(vals)
    types = ["missing" if v is _ABSENT else _json_type(v) for v in vals]
    values = [
        MISSING if v is _ABSENT else _mask_preview(_value_preview(v), path)
        for v in vals
    ]
    entry: Dict[str, Any] = {
        "status": "stable",
        "kind": "same",
        "types": types,
        "values": values,
    }
    present = [v for v in vals if v is not _ABSENT]

    if len(present) < calls:
        entry.update(status="flaky", kind="missing_sometimes")
    elif len(set(types)) > 1:
        entry.update(status="flaky", kind="type_changes")
    elif all(isinstance(v, list) for v in present):
        if any(len(v) == 0 for v in present) and any(len(v) > 0 for v in present):
            entry.update(status="flaky", kind="empty_sometimes")
        # empty in every call -> stable / same
        elif all(len(v) > 0 for v in present):
            if _same_elements_different_order(present):
                entry.update(status="dynamic", kind="order_changes")
            elif any(v != present[0] for v in present[1:]):
                entry.update(status="dynamic", kind="value_changes")
    elif any(v != present[0] for v in present[1:]):
        entry.update(status="dynamic", kind="value_changes")
    return entry


def compare_responses(responses: List[Any]) -> Dict[str, Any]:
    """Classify every field observed across the calls (see _entry_for).

    A top-level list response also gets a "root_list" entry so exports can
    guard data[0] access; it is not counted as a field in the summary.
    """
    calls = len(responses)
    per_call = [_collect(r) for r in responses]
    all_paths: Dict[str, Dict[int, Any]] = {}
    for i, mapping in enumerate(per_call):
        for p, v in mapping.items():
            all_paths.setdefault(p, {})[i] = v

    fields: Dict[str, Dict[str, Any]] = {}
    raw_first: Dict[str, Any] = {}
    for path, by_call in sorted(all_paths.items()):
        if 0 in by_call:
            raw_first[path] = by_call[0]
        fields[path] = _entry_for(path, [by_call.get(i, _ABSENT) for i in range(calls)])

    summary = {"stable": 0, "dynamic": 0, "flaky": 0}
    for entry in fields.values():
        summary[entry["status"]] += 1

    out: Dict[str, Any] = {
        "ran": True,
        "calls": calls,
        "fields": fields,
        "summary": summary,
        _RAW_FIRST: raw_first,
    }
    if responses and isinstance(responses[0], list):
        root_vals = [r if isinstance(r, list) else _ABSENT for r in responses]
        out["root_list"] = _entry_for("", root_vals)
    return out


def _rule_pins_value(rule: str, value: Any) -> bool:
    """True if the rule names this exact call-1 value (quotes for strings, the
    bare number/None-literal for scalars)."""
    if isinstance(rule, str) and isinstance(value, str):
        return json.dumps(value, ensure_ascii=False) in rule
    if isinstance(rule, str) and isinstance(value, bool):
        return repr(value) in rule.lower()
    if isinstance(rule, str) and isinstance(value, (int, float)):
        return str(value) in rule
    return False


# Parent-list kinds that also apply to child paths (e.g. items.sku when the
# items list itself changes order or is empty in some calls).
_PARENT_LIST_KINDS = ("order_changes", "empty_sometimes")


def apply_stability_to_assertions(assertions: List[Dict[str, Any]], stability: Dict[str, Any]) -> None:
    """Tag each assertion with a['stability'] based on the grounding path.

    Mutates assertions in place; pops the internal raw-values key so only
    masked data can leave the server.
    """
    if not stability or not stability.get("ran"):
        return
    fields = stability.get("fields") or {}
    raw_first = stability.pop(_RAW_FIRST, {}) or {}

    for a in assertions or []:
        if not isinstance(a, dict):
            continue
        path = (a.get("grounding") or {}).get("path")
        if not path or not isinstance(path, str):
            a["stability"] = None
            continue

        info = fields.get(path)
        if info is None and "." in path:
            parent_info = fields.get(path.split(".")[0])
            if parent_info and parent_info.get("kind") in _PARENT_LIST_KINDS:
                info = parent_info
        if info is None:
            a["stability"] = None
            continue

        if info["status"] == "stable":
            a["stability"] = {"status": "stable"}
            continue

        tag = {
            "status": info["status"],
            "kind": info["kind"],
            "values": info["values"],
            "types": info["types"],
        }
        # A rule that pins the call-1 value (e.g. '"created_at" equals "..."')
        # would fail on any run where the value differs -> flaky.
        if info["status"] == "dynamic" and _rule_pins_value(a.get("rule", ""), raw_first.get(path)):
            tag["status"] = "flaky"
            tag["kind"] = "pins_changing_value"
        a["stability"] = tag


def stability_skipped(reason: str) -> Dict[str, Any]:
    """Result marker when the check could not run (not a failure)."""
    return {"ran": False, "reason": reason}
