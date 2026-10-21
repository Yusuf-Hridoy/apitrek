"""
Shared interpretation of free-text validation rules.

Both the live runner (``core.test_executor``) and the export translator
(``exports.assertion_translator``) turn a rule such as
``args.name equals 'alice'`` into a check. One parser lives here so the two
cannot drift apart:

- the FIRST quoted token (or dotted path) is the field or header name; the
  rest of the rule is not a field — a second quoted token after an
  equals-style phrase is the expected VALUE;
- negation words ("No 'X' header", "must not be present", "without 'x'")
  mean the field/header must be ABSENT;
- a dotted path ("data.id") resolves through nested objects.
"""
import re
from typing import Any, List, Optional, Tuple

_QUOTED_TOKEN_RE = re.compile(r"['\"]([A-Za-z_][\w.\-]*)['\"]")

# Equals-style phrases. A second quoted token after one of these is the
# expected value, never another field name.
_EQUALITY_PHRASES = (
    "equals", "must be", "should be", "==", " is ", "was", "matches",
)

# Negation means absence — but only for presence-ish verbs. "must not be
# null" is a value rule, not an absence rule, so bare "not" is not enough.
_ABSENCE_PATTERNS = (
    r"\bno\s+['\"]",
    r"\bwithout\s+['\"]",
    r"\babsent\b",
    r"\bnot\s+(?:be\s+)?(?:present|included|returned|exposed|set|sent|shown|appear|leak)",
    r"\bmust\s+not\s+(?:be\s+)?(?:present|included|returned|exposed|set|sent|shown|appear|leak|exist)",
    r"\bshould\s+not\s+(?:be\s+)?(?:present|included|returned|exposed|set|sent|shown|appear|leak|exist)",
    r"\bomits?\b",
    r"\bexclud(?:e|es|ed|ing)\b",
)
_ABSENCE_RE = re.compile("|".join(_ABSENCE_PATTERNS), re.IGNORECASE)

# Unquoted dotted paths: "args.name equals 'alice'" writes the field without
# quotes. A path is word.word(.word) — plain dotted identifiers only.
_DOTTED_PATH_RE = re.compile(r"\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)\b")


def quoted_tokens(rule: str) -> List[str]:
    """Quoted field/header names and values, in order of appearance."""
    return _QUOTED_TOKEN_RE.findall(str(rule))


def rule_field_and_value(rule: str) -> Tuple[Optional[str], Optional[str]]:
    """Split a rule into (field, expected_value).

    The field is the first dotted path when one appears (quoted or not),
    otherwise the first quoted token. A quoted token after an equals-style
    phrase is the expected VALUE — never a second field name. Returns
    (None, None) when the rule names no field at all.
    """
    text = str(rule)
    tokens = quoted_tokens(text)
    dotted = _DOTTED_PATH_RE.search(text)
    if dotted:
        target = dotted.group(1)
        rest = [t for t in tokens if t.lower() != target.lower()]
        value = rest[0] if (rest and rule_implies_equality(text)) else None
        return target, value
    if not tokens:
        return None, None
    value = tokens[1] if (len(tokens) > 1 and rule_implies_equality(text)) else None
    return tokens[0], value


def rule_implies_equality(rule: str) -> bool:
    """True when the rule compares the field/header to an expected value."""
    low = f" {str(rule).lower()} "
    return any(phrase in low for phrase in _EQUALITY_PHRASES)


# Value-comparison phrases. A rule with one of these (and no presence verb)
# checks the field's VALUE — it cannot be judged when the field is absent.
_VALUE_PHRASES = ("equals", "must be", "should be", "==")

# Presence verbs: a mere existence rule stays a hard check even when one of
# the value phrases appears ("'id' must be present" is about presence).
_PRESENCE_WORDS = ("present", "exist", "has field")


def rule_checks_value(rule: str, expected_value: Optional[str] = None) -> bool:
    """True when the rule checks the field's VALUE rather than its presence.

    A value comparison ("args.name equals 'alice'", "id == 1") has nothing to
    compare when the field is absent, so the caller should treat a missing
    field as needs-review, not a failure — the rule may assume a field the
    request never sent. A presence rule ("is present", "must exist") stays a
    hard check on a missing field. An explicit ``expected_value`` (from
    rule_field_and_value) marks the rule as a value comparison on its own.
    """
    if expected_value is not None:
        return True
    low = f" {str(rule).lower()} "
    if any(word in low for word in _PRESENCE_WORDS):
        return False
    return any(phrase in low for phrase in _VALUE_PHRASES)


def rule_implies_absence(rule: str) -> bool:
    """True when the rule wants the field/header to be ABSENT."""
    return bool(_ABSENCE_RE.search(str(rule)))


def resolve_field_path(data: Any, dotted: str) -> Tuple[bool, Any]:
    """Walk a dotted path through nested dicts (lists descend into item 0).

    Returns (found, value). A path is found only when every segment exists.
    """
    current = data
    for segment in str(dotted).split("."):
        if isinstance(current, list):
            current = current[0] if current and isinstance(current[0], dict) else None
        if not isinstance(current, dict) or segment not in current:
            return False, None
        current = current[segment]
    return True, current
