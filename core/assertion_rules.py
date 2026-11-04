"""
Shared interpretation of free-text validation rules.

Both the live runner (``core.test_executor``) and the export translator
(``exports.assertion_translator``) turn a rule such as
``response.args.name equals 'alice'`` into a check. One parser lives here so
the two cannot drift apart:

- quoted strings are collected first and may hold ANY content — an empty
  string, spaces, slashes, a whole URL;
- the FIELD is the first dotted path in the rule (``response.args.foo``,
  ``headers['User-Agent']`` after bracket normalization), or else the first
  quoted token that looks like a name. URLs, ``e.g.``-style abbreviations and
  the expected value are removed before the search, so a host name or an
  abbreviation is never mistaken for a field;
- the expected VALUE is the first quoted string after an equals-style phrase
  that isn't the field itself;
- a ``response.``/``body.``/``json.`` wrapper is tried as written first and
  then stripped, so both a real ``response`` key and the common wrapper work;
- negation words ("No 'X' header", "must not be present", "without 'x'")
  mean the field/header must be ABSENT;
- a rule about the STATUS CODE is recognized even without the word "status"
  ("must return HTTP 200"), and a rule describing several cases at once
  ("200 on success; 401 on auth failure") is not mechanically verifiable.
"""
import re
from typing import Any, Dict, List, Optional, Tuple

# Any quoted run, including empty values and values with spaces or slashes.
_QUOTED_RE = re.compile(r"['\"]([^'\"]*)['\"]")

# A quoted token that can name a field or header.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][\w.\-]*$")

# Equals-style phrases. A quoted token after one of these is the expected
# value, never another field name.
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
# quotes. Segments after the first may contain hyphens so a normalized header
# path ("headers.User-Agent") survives.
_DOTTED_PATH_RE = re.compile(r"\b[A-Za-z_]\w*(?:\.[A-Za-z_][\w-]*)+")

# Bracket notation: headers['User-Agent'] / headers["User-Agent"].
_BRACKET_RE = re.compile(r"\[\s*['\"]([^'\"]+)['\"]\s*\]")

# A whole URL is never a field path (and its host is never a field name).
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

# Abbreviations that look like dotted paths.
_ABBREVIATION_RE = re.compile(r"\b(?:e\.g\.?|i\.e\.?|etc\.?|vs\.?)", re.IGNORECASE)
_ABBREVIATIONS = ("e.g", "i.e", "etc", "vs")

# A dotted token ending in one of these is a host name, not a field path.
_DOMAIN_ENDINGS = (
    "com", "org", "net", "io", "dev", "app", "co", "gov", "edu", "herokuapp",
)

# Wrappers the AI writes around a real response path. "data" is deliberately
# NOT here: many APIs really do return a top-level "data" object.
_WRAPPER_PREFIXES = ("response", "resp", "res", "body", "json", "responsebody")


def quoted_tokens(rule: str) -> List[str]:
    """Every quoted string in the rule, in order of appearance.

    Content is taken as written: empty strings, spaces, slashes and URLs all
    come back intact. Use :func:`rule_field_and_value` to tell a field name
    from an expected value.
    """
    return _QUOTED_RE.findall(str(rule))


def _blank_spans(text: str, spans) -> str:
    """Replace the given (start, end) ranges with spaces, keeping positions."""
    chars = list(text)
    for start, end in spans:
        for i in range(max(start, 0), min(end, len(chars))):
            chars[i] = " "
    return "".join(chars)


def _blank_pattern(text: str, pattern) -> str:
    return pattern.sub(lambda m: " " * (m.end() - m.start()), text)


def normalize_bracket_paths(text: str) -> str:
    """``x['k']`` and ``x["k"]`` become ``x.k`` so one path regex sees both."""
    return _BRACKET_RE.sub(lambda m: "." + m.group(1), str(text))


def _equality_span(text: str) -> Optional[Tuple[int, int]]:
    """(start, end) of the earliest equals-style phrase, or None."""
    padded = " " + str(text).lower() + " "
    best = None
    for phrase in _EQUALITY_PHRASES:
        idx = padded.find(phrase)
        if idx == -1:
            continue
        start = max(idx - 1, 0)
        end = max(idx + len(phrase) - 1, 0)
        if best is None or start < best[0]:
            best = (start, end)
    return best


def _is_not_a_field(token: str) -> bool:
    """True for host names and abbreviations that only look like paths."""
    low = str(token).lower().strip().rstrip(".")
    if low in _ABBREVIATIONS:
        return True
    return low.rsplit(".", 1)[-1] in _DOMAIN_ENDINGS


def rule_field_and_value(rule: str) -> Tuple[Optional[str], Optional[str]]:
    """Split a rule into (field, expected_value).

    The field is the first dotted path that isn't a host name, an
    abbreviation or the expected value; failing that, the first quoted token
    that looks like a name. The expected value is the first quoted string
    after an equals-style phrase that isn't the field itself — any content,
    including an empty string. Returns (None, None) when the rule names no
    field at all.
    """
    text = str(rule)
    spans = [(m.group(1), m.start(), m.end()) for m in _QUOTED_RE.finditer(text)]
    equality = _equality_span(text)
    value_spans = [s for s in spans if equality and s[1] >= equality[1]]
    field_spans = [s for s in spans if s not in value_spans]

    # Field search text: the expected value, URLs and abbreviations removed,
    # bracket notation turned into dots.
    field_text = _blank_spans(text, [(s, e) for _, s, e in value_spans])
    field_text = _blank_pattern(field_text, _URL_RE)
    field_text = _blank_pattern(field_text, _ABBREVIATION_RE)
    field_text = normalize_bracket_paths(field_text)

    target = None
    for match in _DOTTED_PATH_RE.finditer(field_text):
        token = match.group(0).rstrip("-.")
        if "." not in token or _is_not_a_field(token):
            continue
        target = token
        break

    if target is None:
        candidates = [c for c, _, _ in field_spans] or [c for c, _, _ in spans]
        for content in candidates:
            token = content.strip()
            if _IDENTIFIER_RE.match(token) and not _is_not_a_field(token):
                target = token
                break

    if target is None:
        return None, None

    value = None
    if rule_implies_equality(text):
        low_target = target.lower()
        last_segment = low_target.rsplit(".", 1)[-1]
        for content, _start, _end in value_spans:
            compact = content.strip().lower()
            if compact and compact in (low_target, last_segment):
                continue
            value = content
            break
    return target, value


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


def unwrap_field_path(dotted: str) -> Optional[str]:
    """The path without a ``response.``-style wrapper, or None if it has none.

    ``response.args.foo`` -> ``args.foo``. ``data.id`` comes back as None:
    "data" is a real key in plenty of APIs, so it is never stripped.
    """
    parts = str(dotted).split(".")
    if len(parts) > 1 and parts[0].lower() in _WRAPPER_PREFIXES:
        return ".".join(parts[1:])
    return None


def _walk(data: Any, dotted: str) -> Tuple[bool, Any]:
    current = data
    for segment in str(dotted).split("."):
        if isinstance(current, list):
            current = current[0] if current and isinstance(current[0], dict) else None
        if not isinstance(current, dict) or segment not in current:
            return False, None
        current = current[segment]
    return True, current


def resolve_field_path(data: Any, dotted: str) -> Tuple[bool, Any]:
    """Walk a dotted path through nested dicts (lists descend into item 0).

    The path is tried exactly as written first, so an API with a real
    top-level ``response`` key still resolves. Only if that misses is a
    ``response.``/``body.``/``json.`` wrapper stripped and the rest retried.

    Returns (found, value). A path is found only when every segment exists.
    """
    found, value = _walk(data, dotted)
    if found:
        return True, value
    stripped = unwrap_field_path(dotted)
    if stripped:
        return _walk(data, stripped)
    return False, None


# --- value comparison ---------------------------------------------------------

_BOOLEAN_WORDS = ("true", "false", "null", "none")


def _as_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    return str(value)


def values_equal(actual: Any, expected: Any) -> bool:
    """Compare a real response value with a rule's quoted expectation.

    Both sides are compared as trimmed text, so ``'10'`` matches ``10`` and
    ``'true'`` matches ``True``. Numbers also compare numerically, so
    ``'1.0'`` matches ``1``.
    """
    if expected is None:
        return False
    actual_text = _as_text(actual).strip()
    expected_text = str(expected).strip()
    if actual_text == expected_text:
        return True
    low_actual, low_expected = actual_text.lower(), expected_text.lower()
    if low_actual in _BOOLEAN_WORDS or low_expected in _BOOLEAN_WORDS:
        if low_actual == low_expected:
            return True
        return {low_actual, low_expected} == {"null", "none"}
    try:
        return float(actual_text) == float(expected_text)
    except (TypeError, ValueError):
        return False


# --- status-code rules --------------------------------------------------------

# A rule is about the status code when it says so ("status code"), names an
# HTTP code ("must return HTTP 200"), or describes what the API returns
# ("returns 404", "responds with 201"). URLs are removed first so a port or a
# path segment is never read as a status code.
_STATUS_SIGNAL_RES = (
    re.compile(r"\bstatus(?:[ _-]?code)?\b", re.IGNORECASE),
    re.compile(r"\bHTTP[\s/]*\d{3}\b", re.IGNORECASE),
    re.compile(
        r"\breturn(?:s|ed|ing)?\s+(?:a|an|the)?\s*(?:HTTP\s+)?\d{3}\b", re.IGNORECASE
    ),
    re.compile(
        r"\brespond(?:s|ed|ing)?\s+with\s+(?:a|an|the)?\s*(?:HTTP\s+)?\d{3}\b",
        re.IGNORECASE,
    ),
)

# Words that mean the rule covers more than one situation at once.
_CONDITION_RE = re.compile(
    r"\b(?:when|if|on|for|error\w*|fail\w*|invalid|missing|unauthorized|"
    r"forbidden|otherwise)\b",
    re.IGNORECASE,
)

_STATUS_CODE_RE = re.compile(r"\b([1-5]\d{2})\b")

MULTI_CASE_STATUS_REASON = (
    "This rule describes different results for different cases, "
    "so one run can't check it."
)


def status_code_rule(rule: str) -> Optional[Dict[str, Any]]:
    """Read a rule as a status-code expectation, or return None.

    Returns ``{"codes": [...], "multi_case": bool}``. ``multi_case`` is True
    when the rule lists several codes for different situations ("200 on
    success; 401 on auth failure") — one run can't decide such a rule, so the
    caller must treat it as needs-review rather than pass or fail. Several
    codes offered as alternatives ("200 or 201") are not multi-case: any of
    them is a pass.
    """
    text = _blank_pattern(str(rule), _URL_RE)
    if not any(signal.search(text) for signal in _STATUS_SIGNAL_RES):
        return None
    codes = []
    for found in _STATUS_CODE_RE.findall(text):
        code = int(found)
        if code not in codes:
            codes.append(code)
    if not codes:
        return None
    multi_case = len(codes) > 1 and bool(";" in text or _CONDITION_RE.search(text))
    return {"codes": codes, "multi_case": multi_case}
