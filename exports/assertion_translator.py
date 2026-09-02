"""
Schema-driven assertion generation for the pytest and Postman exporters.

Assertions are derived from the ACTUAL fetched JSON response — we only assert
on fields that exist in it, with the type read from the real value. A rule can
refine a real field; a rule that maps to no real field is skipped (commented),
never turned into an assertion. This mirrors the grounding system: no field in
the response -> no assertion.
"""
import re
from typing import Any, Dict, List, Optional, Tuple

# path segments; each element is (key, is_list) so we can build correct accessors
FieldPath = Tuple[Tuple[str, bool], ...]


def _flatten(obj: Any, prefix: FieldPath = ()) -> Dict[FieldPath, Any]:
    """Map real field paths -> sample value. Lists descend into the first item."""
    out: Dict[FieldPath, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = prefix + ((k, False),)
            out[path] = v
            if isinstance(v, dict):
                out.update(_flatten(v, path))
            elif isinstance(v, list) and v and isinstance(v[0], (dict, list)):
                # mark this leaf as a list, descend into element shape
                list_path = prefix + ((k, True),)
                out.update(_flatten(v[0], list_path))
    return out


def _accessor(path: FieldPath, prefix: str = "data") -> str:
    """Build a Python accessor: (('data', True),('id', False)) -> data["data"][0]["id"]"""
    acc = prefix
    for key, is_list in path:
        acc += f'["{key}"]'
        if is_list:
            acc += "[0]"
    return acc


def _js_accessor(path: FieldPath, prefix: str = "jsonData") -> str:
    acc = prefix
    for key, is_list in path:
        acc += f'["{key}"]'
        if is_list:
            acc += "[0]"
    return acc


def _pytype(v: Any) -> Optional[str]:
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "(int, float)"
    if isinstance(v, str):
        return "str"
    if isinstance(v, list):
        return "list"
    if isinstance(v, dict):
        return "dict"
    return None


def _jstype(v: Any) -> Optional[str]:
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return None


def _match_rule(rule: str, fields: Dict[FieldPath, Any]) -> Optional[FieldPath]:
    """Return a real field path the rule refers to (longest leaf match wins)."""
    low = rule.lower()
    for path in sorted(fields, key=lambda p: len(p[-1][0]), reverse=True):
        leaf = path[-1][0].lower()
        if re.search(rf"\b{re.escape(leaf)}\b", low):
            return path
    return None


def pytest_assertions(response: Any, rules: List[str]) -> List[str]:
    """Real pytest asserts from the fetched response + rules. Never asserts a
    field that isn't in the response."""
    if response is None or not isinstance(response, (dict, list)):
        return []  # no schema -> caller falls back to status/structural only

    root_is_list = isinstance(response, list)
    lines = ["    data = response.json()"]
    if root_is_list:
        lines.append("    assert isinstance(data, list) and len(data) > 0")
        sample = response[0] if response else {}
        fields = _flatten(sample)
        lines.append("    item = data[0]")
        prefix_acc = "item"
    else:
        fields = _flatten(response)
        prefix_acc = "data"

    used: set = set()

    def acc_for(path: FieldPath) -> str:
        return _accessor(path, prefix_acc)

    # 1) rule-guided refinement — only for real fields
    for rule in rules or []:
        path = _match_rule(rule, fields)
        if not path or path in used:
            lines.append(f"    # (no matching response field) {rule}")
            continue
        used.add(path)
        a = acc_for(path)
        t = _pytype(fields[path])
        lines.append(f"    # Validation: {rule}")
        lines.append(f"    assert {a} is not None")
        if t:
            lines.append(f"    assert isinstance({a}, {t})")

    # 2) schema baseline for top-level real fields not already covered
    for path, v in fields.items():
        if len(path) != 1 or path in used:
            continue
        t = _pytype(v)
        if t:
            a = acc_for(path)
            lines.append(f"    assert isinstance({a}, {t})")

    return lines


def postman_assertions(response: Any, rules: List[str]) -> List[str]:
    """Real Postman pm.expect checks from the fetched response + rules."""
    if response is None or not isinstance(response, (dict, list)):
        return ["    pm.expect(pm.response.code).to.be.below(500);"]

    lines = ["    var jsonData = pm.response.json();"]
    if isinstance(response, list):
        lines.append("    pm.expect(jsonData).to.be.an('array');")
        sample = response[0] if response else {}
        fields = _flatten(sample)
        prefix = "jsonData[0]"
    else:
        fields = _flatten(response)
        prefix = "jsonData"

    def acc_for(path: FieldPath) -> str:
        return _js_accessor(path, prefix)

    used: set = set()
    for rule in rules or []:
        path = _match_rule(rule, fields)
        if not path or path in used:
            continue
        used.add(path)
        a = acc_for(path)
        t = _jstype(fields[path])
        if t:
            if t in ("array", "object"):
                lines.append(f"    pm.expect({a}).to.be.an('{t}');")
            else:
                lines.append(f"    pm.expect({a}).to.be.a('{t}');")

    for path, v in fields.items():
        if len(path) != 1 or path in used:
            continue
        t = _jstype(v)
        if t:
            lines.append(f'    pm.expect(jsonData).to.have.property("{path[0][0]}");')
    return lines
