"""Tests for grounding evidence (explain_grounding) and its history round-trip.

Covers: parity with the old _is_grounded verdicts, evidence content for each
reason, value typing/preview/truncation, secret masking, and persistence of
the evidence through save/restore (the History fix).
"""
import sys
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from core.generator import _is_grounded, explain_grounding, annotate_grounding


RESP = {"id": 1, "title": "x", "price": 9.99, "rating": {"count": 5, "rate": 4.1}}

# (rule, response) pairs mirroring tests/test_grounding.py plus edge cases.
PARITY_CASES = [
    ('body has field "price"', RESP),
    ('body has field "currency"', RESP),
    ("rating.count >= 0", RESP),
    ("rating.median >= 0", RESP),
    ("price is a number", RESP),
    ('body has field "price"', None),
    (None, RESP),
    (123, RESP),
    ("Response body is valid JSON", RESP),
    ("Response status code is 200", RESP),
    ("Response time is under 2000ms", RESP),
    ('body has field "title"', [{"title": "a"}]),
    ("Response status == 200", RESP),
    ("", RESP),
    ("must be valid json within", RESP),
]


@pytest.mark.parametrize("rule,response", PARITY_CASES)
def test_explain_grounding_matches_legacy_verdict(rule, response):
    """grounded must equal the legacy _is_grounded outcome for every input."""
    assert explain_grounding(rule, response)["grounded"] == _is_grounded(rule, response)


def test_found_quoted_field_evidence():
    ev = explain_grounding('body has field "price"', RESP)
    assert ev["reason"] == "found"
    assert ev["grounded"] is True
    assert ev["path"] == "price"
    assert ev["value_preview"] == "9.99"
    assert ev["value_type"] == "number"
    assert ev["from_list"] is False
    assert ev["detail"] == "Found in the fetched response."


def test_found_dotted_nested_path():
    ev = explain_grounding("rating.count >= 0", RESP)
    assert ev["reason"] == "found"
    assert ev["path"] == "rating.count"
    assert ev["value_preview"] == "5"
    assert ev["value_type"] == "integer"


def test_found_bare_token_match():
    ev = explain_grounding("title is a string", RESP)
    assert ev["reason"] == "found"
    assert ev["path"] == "title"
    assert ev["value_preview"] == '"x"'
    assert ev["value_type"] == "string"


def test_found_in_list_response_uses_first_item():
    ev = explain_grounding('body has field "id"', [{"id": 7, "name": "a"}])
    assert ev["reason"] == "found"
    assert ev["path"] == "id"
    assert ev["from_list"] is True
    assert ev["value_preview"] == "7"
    assert "first item of the list" in ev["detail"]


def test_not_in_response_names_the_field():
    ev = explain_grounding('body has field "currency"', RESP)
    assert ev["reason"] == "not_in_response"
    assert ev["grounded"] is False
    assert ev["path"] == "currency"
    assert '"currency"' in ev["detail"]


def test_not_in_response_dotted_path():
    ev = explain_grounding("rating.median >= 0", RESP)
    assert ev["reason"] == "not_in_response"
    assert ev["path"] == "rating.median"


@pytest.mark.parametrize("rule", [
    "Response status code is 200",
    "response time under 500 ms",
    "status == 404",
])
def test_not_checkable_rules(rule):
    ev = explain_grounding(rule, RESP)
    assert ev["reason"] == "not_checkable"
    assert ev["grounded"] is False
    assert "one sample" in ev["detail"]


def test_valid_json_grounded():
    ev = explain_grounding("Response body is valid JSON", RESP)
    assert ev["reason"] == "valid_json"
    assert ev["grounded"] is True
    assert "valid JSON" in ev["detail"]


def test_no_field_detected_for_stopword_only_rule():
    ev = explain_grounding("must be present and not null", RESP)
    assert ev["reason"] == "no_field_detected"
    assert ev["grounded"] is False


@pytest.mark.parametrize("rule", ['body has field "price"', "price is a number", 42])
def test_no_response_when_response_is_none(rule):
    ev = explain_grounding(rule, None)
    assert ev["reason"] == "no_response"
    assert ev["grounded"] is False
    assert "no response to check against" in ev["detail"].lower()


def test_type_names():
    response = {
        "n": None, "b": True, "i": 3, "f": 1.5,
        "s": "hi", "a": [1, 2, 3], "o": {"x": 1, "y": 2},
    }
    cases = {
        "n": "null", "b": "boolean", "i": "integer", "f": "number",
        "s": "string", "a": "array", "o": "object",
    }
    for key, expected_type in cases.items():
        ev = explain_grounding(f'has "{key}"', response)
        assert ev["reason"] == "found"
        assert ev["value_type"] == expected_type
    assert explain_grounding('has "a"', response)["value_preview"] == "array (3 items)"
    assert explain_grounding('has "o"', response)["value_preview"] == "object (2 keys)"


def test_long_string_preview_truncated():
    response = {"note": "x" * 100}
    ev = explain_grounding('has "note"', response)
    assert len(ev["value_preview"]) <= 60
    assert ev["value_preview"].endswith("…")


@pytest.mark.parametrize("key", [
    "password", "access_token", "user.session_id", "Cookie", "session",
])
def test_sensitive_path_segments_masked(key):
    response = {
        "password": "hunter2",
        "access_token": "SECRETTOK",
        "user": {"session_id": "abc"},
        "Cookie": "sid=1",
        "session": {"token": "xyz"},
    }
    ev = explain_grounding(f'has "{key}"', response)
    assert ev["reason"] == "found"
    assert ev["value_preview"] == "***masked***"
    # The type isn't secret.
    assert ev["value_type"] in ("string", "object")


def test_jwt_value_masked_by_redact_response_text():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJVadQssw5c"
    ev = explain_grounding('has "note"', {"note": jwt})
    assert ev["reason"] == "found"
    assert "eyJ" not in ev["value_preview"]
    assert jwt not in ev["value_preview"]


def test_annotate_grounding_attaches_evidence():
    out = annotate_grounding(
        [{"rule": 'has "price"'}, {"rule": 'has "nope"'}], RESP
    )
    assert out[0]["grounded"] is True
    assert out[0]["grounding"]["reason"] == "found"
    assert out[1]["grounded"] is False
    assert out[1]["grounding"]["reason"] == "not_in_response"


def test_annotate_grounding_no_response():
    out = annotate_grounding([{"rule": 'has "price"'}], None)
    assert out[0]["grounded"] is False
    assert out[0]["grounding"]["reason"] == "no_response"


# --- History round-trip: evidence survives save/restore --------------------

@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Fresh initialized database in a temp dir for each test."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    from core import database

    database.init_db()
    return database


def test_history_round_trip_preserves_grounding(db, monkeypatch):
    from web.routes.generate import _cases_for_db

    result = {
        "positive_test_cases": [],
        "negative_test_cases": [],
        "edge_cases": [],
        "assertions": [
            {"category": "schema", "rule": 'has "price"', "severity": "high"},
            {"category": "auth", "rule": 'has "access_token"', "severity": "critical"},
        ],
    }
    response = {"price": 9.99, "access_token": "SECRET123"}
    annotate_grounding(result["assertions"], response)

    session_id = db.save_session(
        endpoint="https://api.example.com/products/1", method="GET"
    )
    db.save_test_cases(session_id, _cases_for_db(result))

    restored = db.get_session(session_id)
    assertion_rows = [tc for tc in restored["test_cases"] if tc["category"] == "assertion"]
    assert len(assertion_rows) == 2

    by_rule = {tc["title"]: tc for tc in assertion_rows}
    price_row = by_rule['has "price"']
    assert price_row["payload"]["grounded"] is True
    assert price_row["payload"]["grounding"]["reason"] == "found"
    assert price_row["payload"]["grounding"]["path"] == "price"
    # app.js restore logic: grounded from payload or legacy row key.
    stored = price_row["payload"] or {}
    assert (stored["grounded"] is True) or (price_row["grounded"] is True)

    token_row = by_rule['has "access_token"']
    assert token_row["payload"]["grounded"] is True  # field exists in response
    assert token_row["payload"]["grounding"]["value_preview"] == "***masked***"
    # The raw secret must not appear anywhere in the stored row.
    stored_json = json.dumps(
        {k: v for k, v in token_row.items() if k != "payload"}, default=str
    ) + json.dumps(token_row["payload"], default=str)
    assert "SECRET123" not in stored_json


def test_legacy_session_without_payload_restores_unverified(db):
    """Rows saved before grounding payloads existed keep working."""
    session_id = db.save_session(endpoint="https://api.example.com/x", method="GET")
    db.save_test_cases(session_id, [{
        "category": "assertion",
        "title": 'has "price"',
        "description": "schema",
        "expected_status": None,
        "assertions": ['has "price"'],
        "payload": None,
        "severity": "medium",
        "grounded": False,
    }])
    restored = db.get_session(session_id)
    row = restored["test_cases"][0]
    assert row["payload"] is None
    stored = row["payload"] or {}
    # Legacy rows have no grounded column in the DB (the History bug): the
    # restore expression `stored.grounded === true || tc.grounded === true`
    # evaluates falsy here, so the badge shows unverified and the panel
    # reports no evidence — no crash.
    grounded_restored = (stored.get("grounded") is True) or (row.get("grounded") is True)
    assert grounded_restored is False
    assert stored.get("grounding") is None  # app.js falls back to null evidence


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
