"""
Schema-driven assertion generation for the pytest and Postman exporters.

Assertions are derived from the ACTUAL fetched JSON response — we only assert
on fields that exist in it, with the type read from the real value. A rule can
refine a real field; a rule that maps to no real field is skipped (commented),
never turned into an assertion. This mirrors the grounding system: no field in
the response -> no assertion.

Stability awareness (optional ``stability`` argument, the result of
core.stability_check.compare_responses): only fields observed as FLAKY get
tolerant code — a field that is missing sometimes is checked only when
present, a field whose type changes accepts every observed type, and a list
that is empty sometimes guards its item access. Stable and dynamic fields
generate exactly the same code as before, and exports never compare values.
"""
import json
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


# --- stability integration -------------------------------------------------

# JSON type name -> Python type for isinstance tuples (observed types only).
_PYTYPE_FROM_JSON = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "array": "list",
    "object": "dict",
}

# JSON type name -> JS typeof name.
_JSTYPEOF_FROM_JSON = {
    "string": "string",
    "integer": "number",
    "number": "number",
    "boolean": "boolean",
    "array": "object",
    "object": "object",
}


def _flaky_for(dotted: str, stability: Optional[dict]) -> Optional[dict]:
    """Stability entry for a dotted path, only when it is flaky."""
    if not stability or not stability.get("ran"):
        return None
    info = (stability.get("fields") or {}).get(dotted)
    if info and info.get("status") == "flaky":
        return info
    return None


def _root_list_info(stability: Optional[dict]) -> Optional[dict]:
    """Flakiness entry for a top-level list response, when observed."""
    if not stability or not stability.get("ran"):
        return None
    info = stability.get("root_list")
    if info and info.get("status") == "flaky":
        return info
    return None


def _dotted(path: FieldPath) -> str:
    return ".".join(k for k, _ in path)


def _nullable_object_segments(
    path: FieldPath, stability: Optional[dict]
) -> List[int]:
    """Indices i such that path[:i] is an ancestor that was null in some
    calls (object type_changes with null observed) — children must be
    guarded on it."""
    nullable: List[int] = []
    for i in range(1, len(path)):
        seg_info = _flaky_for(_dotted(path[:i]), stability)
        if (
            seg_info
            and seg_info.get("kind") == "type_changes"
            and "null" in (seg_info.get("types") or [])
        ):
            nullable.append(i)
    return nullable


def _py_guard_segments(
    path: FieldPath, prefix_acc: str, info: Optional[dict], stability: Optional[dict]
) -> List[Tuple[str, str]]:
    """(comment, if-line) guards for a path, outermost first. Ancestor lists
    that were empty in some calls guard their item access; an ancestor that
    was null in some calls guards its children; a missing-sometimes field is
    checked only when present (and every missing ancestor is present too)."""
    guards: List[Tuple[str, str]] = []
    for i, (key, is_list) in enumerate(path):
        if not is_list:
            continue
        seg_info = _flaky_for(_dotted(path[: i + 1]), stability)
        if seg_info and seg_info.get("kind") == "empty_sometimes":
            acc = _list_accessor(path[: i + 1], prefix_acc)
            guards.append(("# list empty in some calls", f"if {acc}:"))
    for i in _nullable_object_segments(path, stability):
        acc = _accessor(path[:i], prefix_acc)
        guards.append(("# null in some calls (stability check)", f"if {acc} is not None:"))
    if info and info.get("kind") == "missing_sometimes":
        conds = []
        for i in range(1, len(path)):
            seg_info = _flaky_for(_dotted(path[:i]), stability)
            if seg_info and seg_info.get("kind") == "missing_sometimes":
                seg_parent = _accessor(path[: i - 1], prefix_acc) if i > 1 else prefix_acc
                conds.append(f"{json.dumps(path[i - 1][0])} in {seg_parent}")
        parent = _accessor(path[:-1], prefix_acc) if len(path) > 1 else prefix_acc
        conds.append(f"{json.dumps(path[-1][0])} in {parent}")
        guards.append(
            (
                "# optional: missing in some calls (stability check)",
                f"if {' and '.join(conds)}:",
            )
        )
    return guards


def _list_accessor(path: FieldPath, prefix: str) -> str:
    """Accessor for a list itself (no trailing [0] on the last segment):
    (('items', True),) -> item["items"]; (('a',False),('items',True)) -> data["a"]["items"]."""
    acc = prefix
    last = len(path) - 1
    for i, (key, is_list) in enumerate(path):
        acc += f'["{key}"]'
        if is_list and i != last:
            acc += "[0]"
    return acc


def _py_checks(acc: str, py_type: Optional[str], info: Optional[dict]) -> List[str]:
    """The assertion lines for one field, tolerant when the field was flaky."""
    if info and info.get("kind") == "type_changes":
        observed = [
            t for t in (info.get("types") or []) if t not in ("null", "missing")
        ]
        py_types = list(dict.fromkeys(_PYTYPE_FROM_JSON[t] for t in observed if t in _PYTYPE_FROM_JSON))
        if not py_types:
            return [f"assert {acc} is not None"]
        tup = py_types[0] if len(py_types) == 1 else "(" + ", ".join(py_types) + ")"
        if "null" in (info.get("types") or []):
            return [f"assert {acc} is None or isinstance({acc}, {tup})"]
        return [f"assert isinstance({acc}, {tup})"]
    checks = [f"assert {acc} is not None"]
    if py_type:
        checks.append(f"assert isinstance({acc}, {py_type})")
    return checks


def _emit_guarded(
    lines: List[str], guards: List[Tuple[str, str]], checks: List[str], indent: str
) -> None:
    for comment, if_line in guards:
        lines.append(f"{indent}{comment}")
        lines.append(f"{indent}{if_line}")
        indent += "    "
    for check in checks:
        lines.append(f"{indent}{check}")


def pytest_assertions(response: Any, rules: List[str], stability: Optional[dict] = None) -> List[str]:
    """Real pytest asserts from the fetched response + rules. Never asserts a
    field that isn't in the response."""
    if response is None or not isinstance(response, (dict, list)):
        return []  # no schema -> caller falls back to status/structural only

    root_is_list = isinstance(response, list)
    lines = ["    data = response.json()"]
    if root_is_list:
        root_info = _root_list_info(stability)
        root_guard = bool(root_info and root_info.get("kind") == "empty_sometimes")
        if root_guard:
            # The list was empty in some calls: don't require an item.
            lines.append("    assert isinstance(data, list)")
            lines.append("    if data:")
            base = "        "
        else:
            lines.append("    assert isinstance(data, list) and len(data) > 0")
            base = "    "
        sample = response[0] if response else {}
        fields = _flatten(sample)
        lines.append(base + "item = data[0]")
        prefix_acc = "item"
    else:
        root_guard = False
        base = "    "
        fields = _flatten(response)
        prefix_acc = "data"

    used: set = set()

    def acc_for(path: FieldPath) -> str:
        return _accessor(path, prefix_acc)

    # 1) rule-guided refinement — only for real fields
    for rule in rules or []:
        path = _match_rule(rule, fields)
        # Comments run to end-of-line only: collapse newlines so a multi-line
        # rule can't inject a line of code into the generated test function.
        rule_comment = str(rule).replace("\r", " ").replace("\n", " ")
        if not path or path in used:
            lines.append(f"{base}# (no matching response field) {rule_comment}")
            continue
        used.add(path)
        a = acc_for(path)
        t = _pytype(fields[path])
        info = _flaky_for(_dotted(path), stability)
        lines.append(f"{base}# Validation: {rule_comment}")
        guards = _py_guard_segments(path, prefix_acc, info, stability)
        checks = _py_checks(a, t, info)
        _emit_guarded(lines, guards, checks, base)

    # 2) schema baseline for top-level real fields not already covered
    for path, v in fields.items():
        if len(path) != 1 or path in used:
            continue
        t = _pytype(v)
        if t:
            a = acc_for(path)
            info = _flaky_for(_dotted(path), stability)
            guards = _py_guard_segments(path, prefix_acc, info, stability)
            # No stability data for this field: emit exactly what we always
            # have — just the isinstance check (baseline never asserted
            # `is not None`; that stays exclusive to rule-guided checks).
            if info:
                checks = _py_checks(a, t, info)
            else:
                checks = [f"assert isinstance({a}, {t})"]
            _emit_guarded(lines, guards, checks, base)

    return lines


def _js_guard_segments(
    path: FieldPath, prefix: str, info: Optional[dict], stability: Optional[dict]
) -> List[str]:
    """if (...) guard lines for a JS path (see _py_guard_segments)."""
    guards: List[str] = []
    for i, (key, is_list) in enumerate(path):
        if not is_list:
            continue
        seg_info = _flaky_for(_dotted(path[: i + 1]), stability)
        if seg_info and seg_info.get("kind") == "empty_sometimes":
            guards.append(f"if ({_list_accessor(path[: i + 1], prefix)}.length) {{")
    for i in _nullable_object_segments(path, stability):
        acc = _js_accessor(path[:i], prefix)
        guards.append(f"if ({acc} !== null && {acc} !== undefined) {{")
    if info and info.get("kind") == "missing_sometimes":
        conds = []
        for i in range(1, len(path)):
            seg_info = _flaky_for(_dotted(path[:i]), stability)
            if seg_info and seg_info.get("kind") == "missing_sometimes":
                seg_parent = _js_accessor(path[: i - 1], prefix) if i > 1 else prefix
                conds.append(f"{seg_parent}.hasOwnProperty({json.dumps(path[i - 1][0])})")
        parent = _js_accessor(path[:-1], prefix) if len(path) > 1 else prefix
        conds.append(f"{parent}.hasOwnProperty({json.dumps(path[-1][0])})")
        guards.append(f"if ({' && '.join(conds)}) {{")
    return guards


def _js_checks(acc: str, js_type: Optional[str], info: Optional[dict]) -> List[str]:
    """The pm.expect lines for one field, tolerant when the field was flaky."""
    if info and info.get("kind") == "type_changes":
        observed = [
            t for t in (info.get("types") or []) if t not in ("null", "missing")
        ]
        js_types = list(
            dict.fromkeys(_JSTYPEOF_FROM_JSON[t] for t in observed if t in _JSTYPEOF_FROM_JSON)
        )
        if not js_types:
            return [f"pm.expect({acc}).to.exist;"]
        type_list = ", ".join(json.dumps(t) for t in js_types)
        nullable = "null" in (info.get("types") or [])
        cond = f"[{type_list}].includes(typeof {acc})"
        if nullable:
            return [f"pm.expect({acc} === null || {cond}).to.be.true;"]
        return [f"pm.expect({cond}).to.be.true;"]
    if not js_type:
        return []
    if js_type in ("array", "object"):
        return [f"pm.expect({acc}).to.be.an('{js_type}');"]
    return [f"pm.expect({acc}).to.be.a('{js_type}');"]


def _emit_js_guarded(
    lines: List[str], guards: List[str], checks: List[str], indent: str
) -> None:
    for guard in guards:
        lines.append(f"{indent}{guard}")
        indent += "    "
    for check in checks:
        lines.append(f"{indent}{check}")
    for _ in guards:
        indent = indent[:-4]
        lines.append(f"{indent}}}")


def postman_assertions(response: Any, rules: List[str], stability: Optional[dict] = None) -> List[str]:
    """Real Postman pm.expect checks from the fetched response + rules."""
    if response is None or not isinstance(response, (dict, list)):
        return ["    pm.expect(pm.response.code).to.be.below(500);"]

    lines = ["    var jsonData = pm.response.json();"]
    root_guard = False
    if isinstance(response, list):
        lines.append("    pm.expect(jsonData).to.be.an('array');")
        root_info = _root_list_info(stability)
        if root_info and root_info.get("kind") == "empty_sometimes":
            lines.append("    if (jsonData.length) {")
            base = "        "
            root_guard = True
        else:
            base = "    "
        sample = response[0] if response else {}
        fields = _flatten(sample)
        prefix = "jsonData[0]"
    else:
        base = "    "
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
        info = _flaky_for(_dotted(path), stability)
        checks = _js_checks(a, t, info)
        _emit_js_guarded(lines, _js_guard_segments(path, prefix, info, stability), checks, base)

    for path, v in fields.items():
        if len(path) != 1 or path in used:
            continue
        t = _jstype(v)
        if t:
            info = _flaky_for(_dotted(path), stability)
            checks = [f'pm.expect(jsonData).to.have.property("{path[0][0]}");']
            _emit_js_guarded(lines, _js_guard_segments(path, prefix, info, stability), checks, base)

    if root_guard:
        lines.append("    }")
    return lines
