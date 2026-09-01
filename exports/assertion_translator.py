"""
Translate a natural-language validation rule into an executable assertion,
for both the pytest and Postman exporters. Field/intent detection mirrors
core.generator._is_grounded so exported asserts line up with grounding.
"""
import json
import re
from typing import List, Optional

_FIELD_QUOTED = re.compile(r'["\']([A-Za-z_][\w.]*)["\']')
_FIELD_DOTTED = re.compile(r'\b([A-Za-z_]\w*(?:\.\w+)+)\b')
_STOP = {
    "response", "body", "field", "is", "a", "the", "of", "must", "should",
    "be", "not", "null", "present", "returns", "contains", "and", "or",
    "valid", "json", "within", "under", "each", "all", "an", "with", "value",
    "status", "code", "positive", "negative", "integer", "number", "string",
    "boolean", "array", "list", "matches", "equal", "equals", "exist", "exists",
}


def extract_field(rule: str) -> Optional[str]:
    m = _FIELD_QUOTED.search(rule) or _FIELD_DOTTED.search(rule)
    if m:
        return m.group(1)
    for tok in re.findall(r"\b[A-Za-z_]\w*\b", rule):
        if tok.lower() not in _STOP:
            return tok
    return None


def _literal(rule: str) -> Optional[str]:
    """Return a safely parseable literal value (number, bool, or quoted string)."""
    # Numeric or boolean after equality/comparison keywords.
    m = re.search(
        r'(?:==|equals?|matches|should be)\s+(-?\d+(?:\.\d+)?|true|false)',
        rule,
        re.I,
    )
    if m:
        val = m.group(1)
        if val.lower() in ("true", "false"):
            return val.capitalize()
        return val

    # Parenthesized numeric/boolean values such as "matches requested ID (1)".
    m = re.search(r'\(\s*(-?\d+(?:\.\d+)?|true|false)\s*\)', rule, re.I)
    if m:
        val = m.group(1)
        if val.lower() in ("true", "false"):
            return val.capitalize()
        return val

    # Quoted strings after equality/comparison keywords.
    m = re.search(
        r'(?:==|equals?|matches|should be)\s+([\'"])([A-Za-z0-9_.\- ]+)\1',
        rule,
        re.I,
    )
    if m:
        return f'"{m.group(2)}"'

    return None


def _pyexpr(field: str, rule: str) -> str:
    low = rule.lower()
    acc = f'data["{field}"]'
    if any(k in low for k in ("not null", "non-null", "not empty", "required")):
        return f"assert {acc} is not None"
    if "positive" in low:
        return f"assert {acc} > 0"
    if any(k in low for k in ("boolean", "bool", "true/false")):
        return f'assert isinstance({acc}, bool)'
    if any(k in low for k in ("array", "list")):
        return f'assert isinstance({acc}, list)'
    if any(k in low for k in ("float", "decimal", "numeric", "number")):
        return f'assert isinstance({acc}, (int, float))'
    if any(k in low for k in ("integer", " int", "int ")):
        return f'assert isinstance({acc}, int)'
    if any(k in low for k in ("string", "text", " str")):
        return f'assert isinstance({acc}, str)'
    # Presence intents must be checked before literal equality, otherwise
    # "should be present" is misread as equality to the string "present".
    if any(k in low for k in ("present", "exist", "has field", "contains", "included", "return")):
        return f'assert "{field}" in data'
    lit = _literal(rule)
    if lit is not None:
        return f"assert {acc} == {lit}"
    return f'assert "{field}" in data'  # safe default: presence


def to_pytest(rule: str) -> List[str]:
    """Return lines: the doc comment plus a real assert."""
    field = extract_field(rule)
    out = [f"    # Validation: {rule}"]
    if not field:
        out.append("    assert response is not None")
        return out
    out.append("    " + _pyexpr(field, rule))
    return out


def _pmexpr(field: str, rule: str) -> List[str]:
    low = rule.lower()
    acc = f"jsonData.{field}"
    lines = ["    var jsonData = pm.response.json();"]
    if any(k in low for k in ("not null", "non-null", "not empty", "required")):
        lines.append(f'    pm.expect({acc}).to.not.be.null;')
    elif "positive" in low:
        lines.append(f'    pm.expect({acc}).to.be.above(0);')
    elif any(k in low for k in ("boolean", "bool")):
        lines.append(f'    pm.expect({acc}).to.be.a("boolean");')
    elif any(k in low for k in ("array", "list")):
        lines.append(f'    pm.expect({acc}).to.be.an("array");')
    elif any(k in low for k in ("float", "decimal", "numeric", "number", "integer", " int")):
        lines.append(f'    pm.expect({acc}).to.be.a("number");')
    elif any(k in low for k in ("string", "text", " str")):
        lines.append(f'    pm.expect({acc}).to.be.a("string");')
    elif any(k in low for k in ("present", "exist", "has field", "contains", "included", "return")):
        lines.append(f'    pm.expect(jsonData).to.have.property("{field}");')
    else:
        lit = _literal(rule)
        if lit is not None:
            try:
                parsed = json.loads(lit)
                if isinstance(parsed, bool):
                    js_lit = lit.lower()
                else:
                    js_lit = lit
            except (ValueError, TypeError):
                js_lit = lit
            lines.append(f'    pm.expect({acc}).to.eql({js_lit});')
        else:
            lines.append(f'    pm.expect(jsonData).to.have.property("{field}");')
    return lines


def to_postman(rule: str) -> List[str]:
    field = extract_field(rule)
    if not field:
        return ["    pm.expect(pm.response.code).to.be.below(500);"]
    return _pmexpr(field, rule)
