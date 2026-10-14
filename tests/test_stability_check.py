"""Tests for the stability check: classification, assertion tagging, route
behavior, History persistence, and export guards.

Honesty rules under test: value changes are dynamic (informational), shape
changes are flaky; a rule that pins one exact value of a changing field is
flaky even though Apitrek's own checks never compare values.
"""
import sys
import json
import ast
import copy
import textwrap
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from core.stability_check import (
    _RAW_FIRST,
    apply_stability_to_assertions,
    compare_responses,
    stability_skipped,
)
from core.generator import annotate_grounding


# --- compare_responses: classification -------------------------------------

def test_identical_responses_all_stable():
    responses = [
        {"id": 1, "name": "widget", "price": 9.99},
        {"id": 1, "name": "widget", "price": 9.99},
        {"id": 1, "name": "widget", "price": 9.99},
    ]
    out = compare_responses(responses)
    assert out["ran"] is True
    assert out["calls"] == 3
    assert set(out["fields"]) == {"id", "name", "price"}
    assert all(f["status"] == "stable" and f["kind"] == "same" for f in out["fields"].values())
    assert out["summary"] == {"stable": 3, "dynamic": 0, "flaky": 0}


def test_value_changes_are_dynamic_not_flaky():
    responses = [
        {"created_at": "2026-10-12T09:14:00Z"},
        {"created_at": "2026-10-12T09:14:01Z"},
        {"created_at": "2026-10-12T09:14:02Z"},
    ]
    out = compare_responses(responses)
    entry = out["fields"]["created_at"]
    assert entry["status"] == "dynamic"
    assert entry["kind"] == "value_changes"
    assert len(set(entry["values"])) == 3
    assert out["summary"]["flaky"] == 0


def test_list_order_changes_are_dynamic():
    # First element stays put: only the tail reorders, so per grounding
    # convention items.sku (first item's sku) is stable while the list
    # itself is dynamic / order_changes.
    responses = [
        {"items": [{"sku": "a"}, {"sku": "b"}, {"sku": "c"}]},
        {"items": [{"sku": "a"}, {"sku": "c"}, {"sku": "b"}]},
        {"items": [{"sku": "a"}, {"sku": "b"}, {"sku": "c"}]},
    ]
    out = compare_responses(responses)
    assert out["fields"]["items"]["status"] == "dynamic"
    assert out["fields"]["items"]["kind"] == "order_changes"
    assert out["fields"]["items.sku"]["status"] == "stable"


def test_missing_sometimes_is_flaky():
    responses = [
        {"id": 1, "discount": 5},
        {"id": 1},
        {"id": 1, "discount": 5},
    ]
    out = compare_responses(responses)
    entry = out["fields"]["discount"]
    assert entry["status"] == "flaky"
    assert entry["kind"] == "missing_sometimes"
    assert entry["values"][1] == "(missing)"
    assert entry["types"][1] == "missing"


def test_type_changes_is_flaky():
    responses = [
        {"discount": 0.1},
        {"discount": None},
        {"discount": 0.2},
    ]
    out = compare_responses(responses)
    entry = out["fields"]["discount"]
    assert entry["status"] == "flaky"
    assert entry["kind"] == "type_changes"
    assert entry["types"] == ["number", "null", "number"]


def test_empty_sometimes_is_flaky():
    responses = [
        {"items": [{"sku": "a"}]},
        {"items": []},
        {"items": [{"sku": "a"}]},
    ]
    out = compare_responses(responses)
    entry = out["fields"]["items"]
    assert entry["status"] == "flaky"
    assert entry["kind"] == "empty_sometimes"


def test_nested_and_list_paths_match_grounding_convention():
    responses = [
        {"address": {"geo": {"lat": 1.0}}, "items": [{"sku": "a", "qty": 1}]},
        {"address": {"geo": {"lat": 2.0}}, "items": [{"sku": "a", "qty": 1}]},
        {"address": {"geo": {"lat": 3.0}}, "items": [{"sku": "a", "qty": 1}]},
    ]
    out = compare_responses(responses)
    assert "address.geo.lat" in out["fields"]
    assert out["fields"]["address.geo.lat"]["kind"] == "value_changes"
    assert "items.sku" in out["fields"]
    assert out["fields"]["items.sku"]["status"] == "stable"
    # Object parents are recorded (presence/type tracked) but a child-driven
    # change never makes the parent itself dynamic — the child carries it.
    assert out["fields"]["address"]["status"] == "stable"
    assert out["fields"]["address"]["kind"] == "same"
    assert out["fields"]["address.geo"]["status"] == "stable"


def test_top_level_list_response():
    responses = [
        [{"id": 1}, {"id": 2}],
        [{"id": 1}, {"id": 2}],
        [{"id": 1}, {"id": 2}],
    ]
    out = compare_responses(responses)
    assert "id" in out["fields"]
    assert out["fields"]["id"]["status"] == "stable"


def test_top_level_list_empty_sometimes_recorded_for_exports():
    responses = [
        [{"id": 1}],
        [],
        [{"id": 1}],
    ]
    out = compare_responses(responses)
    assert out["root_list"]["status"] == "flaky"
    assert out["root_list"]["kind"] == "empty_sometimes"


def test_masking_applies_to_all_previews():
    responses = [
        {"access_token": "SECRETAAA"},
        {"access_token": "SECRETBBB"},
        {"access_token": "SECRETCCC"},
    ]
    out = compare_responses(responses)
    entry = out["fields"]["access_token"]
    assert entry["status"] == "dynamic"
    assert all(v == "***masked***" for v in entry["values"])
    assert "SECRET" not in json.dumps(out["fields"])


def test_raw_first_values_are_internal_only():
    responses = [{"token": "abc"}, {"token": "abd"}, {"token": "abe"}]
    out = compare_responses(responses)
    assert out[_RAW_FIRST]["token"] == "abc"
    assertions = [{"rule": 'has "token"', "grounding": {"path": "token"}}]
    apply_stability_to_assertions(assertions, out)
    assert _RAW_FIRST not in out  # consumed so raw values can't leak to the client


def test_stability_skipped_shape():
    assert stability_skipped("why") == {"ran": False, "reason": "why"}


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Fresh initialized database in a temp dir for each test."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    from core import database

    database.init_db()
    return database


# --- apply_stability_to_assertions ------------------------------------------

def _tagged(rule, responses, path):
    assertions = [{"rule": rule}]
    annotate_grounding(assertions, responses[0])
    assert assertions[0]["grounding"]["path"] == path
    stability = compare_responses(responses)
    apply_stability_to_assertions(assertions, stability)
    return assertions[0]["stability"]


def test_tag_stable():
    r = [{"id": 1}, {"id": 1}, {"id": 1}]
    assert _tagged("has an id", r, "id") == {"status": "stable"}


def test_tag_dynamic():
    r = [{"ts": "a"}, {"ts": "b"}, {"ts": "c"}]
    tag = _tagged("response has ts", r, "ts")
    assert tag["status"] == "dynamic"
    assert tag["kind"] == "value_changes"
    assert len(tag["values"]) == 3


def test_tag_flaky():
    r = [{"id": 1}, {}, {"id": 1}]
    tag = _tagged("has an id", r, "id")
    assert tag["status"] == "flaky"
    assert tag["kind"] == "missing_sometimes"


def test_pins_changing_value_upgrades_dynamic_to_flaky():
    responses = [
        {"created_at": "2026-10-12T09:14:02Z"},
        {"created_at": "2026-10-12T09:15:47Z"},
        {"created_at": "2026-10-12T09:17:11Z"},
    ]
    rule = '"created_at" equals "2026-10-12T09:14:02Z"'
    tag = _tagged(rule, responses, "created_at")
    assert tag["status"] == "flaky"
    assert tag["kind"] == "pins_changing_value"


def test_dynamic_value_in_rule_without_pin_stays_dynamic():
    responses = [{"count": 10}, {"count": 11}, {"count": 12}]
    tag = _tagged("count is a number", responses, "count")
    assert tag["status"] == "dynamic"


def test_child_under_emptied_list_is_missing_sometimes():
    responses = [
        {"items": []},
        {"items": [{"sku": "a"}]},
        {"items": [{"sku": "a"}]},
    ]
    assertions = [{"rule": "items.sku should be a string"}]
    annotate_grounding(assertions, responses[1])
    assert assertions[0]["grounding"]["path"] == "items.sku"
    stability = compare_responses(responses)
    apply_stability_to_assertions(assertions, stability)
    # The first-item path is absent whenever the list was empty.
    assert assertions[0]["stability"]["status"] == "flaky"
    assert assertions[0]["stability"]["kind"] == "missing_sometimes"


def test_parent_list_kind_applies_when_child_not_a_field():
    # items.sku never appears as a collected field (e.g. the child sat behind
    # a list the check couldn't descend into), so the parent's kind applies.
    stability = {
        "ran": True,
        "calls": 3,
        "fields": {
            "items": {
                "status": "dynamic",
                "kind": "order_changes",
                "types": ["array", "array", "array"],
                "values": ["array (2 items)"] * 3,
            },
        },
    }
    assertions = [{"rule": "items.sku is present", "grounding": {"path": "items.sku"}}]
    apply_stability_to_assertions(assertions, stability)
    assert assertions[0]["stability"]["status"] == "dynamic"
    assert assertions[0]["stability"]["kind"] == "order_changes"


def test_untagged_when_no_grounding_path():
    stability = compare_responses([{"id": 1}, {"id": 2}, {"id": 3}])
    assertions = [{"rule": "response time is fast", "grounding": {"path": None}}]
    apply_stability_to_assertions(assertions, stability)
    assert assertions[0]["stability"] is None


def test_untagged_when_path_not_in_fields():
    stability = compare_responses([{"id": 1}, {"id": 2}, {"id": 3}])
    assertions = [{"rule": "has ghost", "grounding": {"path": "ghost"}}]
    apply_stability_to_assertions(assertions, stability)
    assert assertions[0]["stability"] is None


# --- Route behavior ----------------------------------------------------------

from fastapi.testclient import TestClient  # noqa: E402
from web.app import app  # noqa: E402
from core.live_fetcher import LiveFetchError  # noqa: E402


@pytest.fixture
def client():
    app.state.limiter._limiter.storage.reset()
    return TestClient(app)


def _fake_generate(**kwargs):
    # Deep copy: the route tags assertions and adds result["stability"] in
    # place, which must not leak between tests via the shared GENERATED dict.
    return copy.deepcopy(GENERATED)


GENERATED = {
    "positive_test_cases": [],
    "negative_test_cases": [],
    "edge_cases": [],
    "assertions": [
        {"category": "schema", "rule": 'has "id"', "severity": "high",
         "grounded": True, "grounding": {"path": "id"}},
        {"category": "schema", "rule": 'has "ts"', "severity": "medium",
         "grounded": True, "grounding": {"path": "ts"}},
    ],
}


def test_route_flag_on_get_makes_three_calls_and_tags(client):
    payloads = [
        {"id": 1, "ts": "a"},
        {"id": 1, "ts": "b"},
        {"id": 1, "ts": "c"},
    ]
    with patch("web.routes.generate.fetch_api_response", side_effect=payloads) as fetch_mock, \
         patch("web.routes.generate.generate_test_cases", side_effect=_fake_generate), \
         patch("web.routes.generate.save_session", return_value=1), \
         patch("web.routes.generate.save_test_cases"):
        res = client.post("/generate-tests", json={
            "endpoint": "https://api.example.com/x",
            "method": "GET",
            "auto_fetch": True,
            "stability_check": True,
        })
    assert res.status_code == 200
    assert fetch_mock.call_count == 3
    body = res.json()
    assert body["stability"]["ran"] is True
    assert body["stability"]["calls"] == 3
    tags = {a["rule"]: a["stability"]["status"] for a in body["assertions"]}
    assert tags['has "id"'] == "stable"
    assert tags['has "ts"'] == "dynamic"
    # Raw call values never reach the client.
    assert "_raw_first" not in body["stability"]


def test_route_flag_off_exactly_one_fetch(client):
    with patch("web.routes.generate.fetch_api_response", return_value={"id": 1, "ts": "a"}) as fetch_mock, \
         patch("web.routes.generate.generate_test_cases", side_effect=_fake_generate), \
         patch("web.routes.generate.save_session", return_value=1), \
         patch("web.routes.generate.save_test_cases"):
        res = client.post("/generate-tests", json={
            "endpoint": "https://api.example.com/x",
            "method": "GET",
            "auto_fetch": True,
        })
    assert res.status_code == 200
    assert "stability" not in res.json()
    assert fetch_mock.call_count == 1


def test_route_flag_on_post_skips_with_reason(client):
    with patch("web.routes.generate.fetch_api_response", return_value={"id": 1}) as fetch_mock, \
         patch("web.routes.generate.generate_test_cases", side_effect=_fake_generate), \
         patch("web.routes.generate.save_session", return_value=1), \
         patch("web.routes.generate.save_test_cases"):
        res = client.post("/generate-tests", json={
            "endpoint": "https://api.example.com/x",
            "method": "POST",
            "auto_fetch": True,
            "stability_check": True,
        })
    assert res.status_code == 200
    stab = res.json()["stability"]
    assert stab["ran"] is False
    assert "GET only" in stab["reason"]
    assert fetch_mock.call_count == 1


def test_route_flag_on_without_autofetch_skips_with_reason(client):
    with patch("web.routes.generate.fetch_api_response") as fetch_mock, \
         patch("web.routes.generate.generate_test_cases", side_effect=_fake_generate), \
         patch("web.routes.generate.save_session", return_value=1), \
         patch("web.routes.generate.save_test_cases"):
        res = client.post("/generate-tests", json={
            "endpoint": "https://api.example.com/x",
            "method": "GET",
            "stability_check": True,
        })
    assert res.status_code == 200
    stab = res.json()["stability"]
    assert stab["ran"] is False
    assert "auto-fetch" in stab["reason"]
    assert fetch_mock.call_count == 0


def test_route_repeat_call_failure_never_breaks_generation(client):
    def flaky_fetch(**kwargs):
        flaky_fetch.calls += 1
        if flaky_fetch.calls == 2:
            raise LiveFetchError("boom")
        return {"id": 1, "ts": "x"}
    flaky_fetch.calls = 0

    with patch("web.routes.generate.fetch_api_response", side_effect=flaky_fetch), \
         patch("web.routes.generate.generate_test_cases", side_effect=_fake_generate), \
         patch("web.routes.generate.save_session", return_value=1), \
         patch("web.routes.generate.save_test_cases"):
        res = client.post("/generate-tests", json={
            "endpoint": "https://api.example.com/x",
            "method": "GET",
            "auto_fetch": True,
            "stability_check": True,
        })
    assert res.status_code == 200
    stab = res.json()["stability"]
    assert stab["ran"] is False
    assert "repeat call failed" in stab["reason"]


def test_history_round_trip_preserves_stability_tag(db):
    from core import database
    from web.routes.generate import _cases_for_db

    result = {
        "positive_test_cases": [],
        "negative_test_cases": [],
        "edge_cases": [],
        "assertions": [{"category": "schema", "rule": 'has "discount"', "severity": "high"}],
    }
    responses = [{"discount": 0.1}, {"discount": None}, {"discount": 0.2}]
    annotate_grounding(result["assertions"], responses[0])
    stability = compare_responses(responses)
    apply_stability_to_assertions(result["assertions"], stability)
    assert result["assertions"][0]["stability"]["status"] == "flaky"

    session_id = database.save_session(
        endpoint="https://api.example.com/x", method="GET"
    )
    database.save_test_cases(session_id, _cases_for_db(result))
    restored = database.get_session(session_id)
    row = [tc for tc in restored["test_cases"] if tc["category"] == "assertion"][0]
    assert row["payload"]["stability"]["status"] == "flaky"
    assert row["payload"]["stability"]["kind"] == "type_changes"


# --- Export guards -----------------------------------------------------------

from exports.assertion_translator import pytest_assertions, postman_assertions  # noqa: E402
from exports.python_test_generator import generate_pytest_script  # noqa: E402
from exports.postman_generator import generate_postman_collection  # noqa: E402

SAMPLE = {"id": 1, "discount": 0.1, "items": [{"sku": "a"}], "name": "x"}


def _stability_for(responses):
    return compare_responses(responses)


def test_pytest_missing_sometimes_guarded():
    responses = [
        {"id": 1, "discount": 0.1},
        {"id": 1},
        {"id": 1, "discount": 0.1},
    ]
    sample = responses[0]
    stability = _stability_for(responses)
    lines = pytest_assertions(sample, ['body has field "discount"'], stability)
    text = "\n".join(lines)
    assert 'if "discount" in data:' in text
    assert "optional: missing in some calls" in text
    # The checks live inside the guard.
    assert '        assert data["discount"] is not None' in text
    ast.parse(textwrap.dedent(text))


def test_postman_missing_sometimes_guarded():
    responses = [
        {"id": 1, "discount": 0.1},
        {"id": 1},
        {"id": 1, "discount": 0.1},
    ]
    stability = _stability_for(responses)
    lines = postman_assertions(responses[0], ['has "discount"'], stability)
    text = "\n".join(lines)
    assert 'if (jsonData.hasOwnProperty("discount")) {' in text
    assert 'pm.expect(jsonData["discount"]).to.be.a' in text


def test_pytest_type_changes_accepts_observed_types():
    responses = [{"discount": 0.1}, {"discount": None}, {"discount": 0.2}]
    stability = _stability_for(responses)
    lines = pytest_assertions(responses[0], ['has "discount"'], stability)
    text = "\n".join(lines)
    assert 'assert data["discount"] is None or isinstance(data["discount"], float)' in text
    ast.parse(textwrap.dedent(text))


def test_postman_type_changes_accepts_observed_types():
    responses = [{"discount": 0.1}, {"discount": None}, {"discount": 0.2}]
    stability = _stability_for(responses)
    lines = postman_assertions(responses[0], ['has "discount"'], stability)
    text = "\n".join(lines)
    assert 'pm.expect(jsonData["discount"] === null || ["number"].includes(typeof jsonData["discount"])).to.be.true;' in text


def test_pytest_empty_sometimes_guards_item_access():
    responses = [
        {"items": [{"sku": "a"}]},
        {"items": []},
        {"items": [{"sku": "a"}]},
    ]
    stability = _stability_for(responses)
    lines = pytest_assertions(responses[0], ['has "sku"'], stability)
    text = "\n".join(lines)
    assert 'if data["items"]:' in text
    assert "list empty in some calls" in text
    assert 'assert data["items"][0]["sku"] is not None' in text
    ast.parse(textwrap.dedent(text))


def test_pytest_top_level_list_empty_sometimes():
    responses = [[{"id": 1}], [], [{"id": 1}]]
    stability = _stability_for(responses)
    lines = pytest_assertions(responses[0], ['has "id"'], stability)
    text = "\n".join(lines)
    assert "assert isinstance(data, list)" in text
    assert "len(data) > 0" not in text
    assert "if data:" in text
    ast.parse(textwrap.dedent(text))


def test_no_stability_output_unchanged():
    rules = ['body has field "discount"', "id is a number"]
    base_py = pytest_assertions(SAMPLE, rules)
    base_pm = postman_assertions(SAMPLE, rules)
    assert pytest_assertions(SAMPLE, rules, None) == base_py
    assert pytest_assertions(SAMPLE, rules, {"ran": False, "reason": "x"}) == base_py
    assert postman_assertions(SAMPLE, rules, None) == base_pm
    assert postman_assertions(SAMPLE, rules, {"ran": False}) == base_pm


def test_full_generators_still_valid_with_stability():
    responses = [
        {"id": 1, "discount": 0.1, "items": [{"sku": "a"}], "name": "x"},
        {"id": 1, "items": [], "name": "x"},
        {"id": 1, "discount": 0.2, "items": [{"sku": "a"}], "name": "x"},
    ]
    stability = _stability_for(responses)
    test_data = {
        "positive_test_cases": [{
            "id": "TC-POS-01", "title": "Valid request",
            "expected": {"status_code": 200, "validation_rules": ['has "discount"']},
        }],
        "negative_test_cases": [],
        "edge_cases": [],
        "assertions": [
            {"category": "schema", "rule": 'has "sku"', "severity": "high", "grounded": True},
        ],
        "sample_response": responses[0],
        "stability": stability,
    }
    script = generate_pytest_script("https://api.example.com/items", "GET", test_data)
    ast.parse(script)
    assert 'if "discount" in data:' in script
    assert 'if data["items"]:' in script

    collection = generate_postman_collection("https://api.example.com/items", "GET", test_data)
    parsed = json.loads(collection)
    assert parsed["info"]["schema"].endswith("v2.1.0/collection.json")
    item0_script = "\n".join(parsed["item"][0]["event"][0]["script"]["exec"])
    assert 'hasOwnProperty("discount")' in item0_script


# --- Review fixes -----------------------------------------------------------

def test_sometimes_null_object_is_type_changes():
    """The Fix 1 reproduce case: an object that is null in some calls was
    present in every call, so it is a type change — not missing_sometimes."""
    out = compare_responses([{"u": {"x": 1}}, {"u": None}, {"u": {"x": 1}}])
    entry = out["fields"]["u"]
    assert entry["status"] == "flaky"
    assert entry["kind"] == "type_changes"
    assert entry["types"] == ["object", "null", "object"]


def test_child_of_sometimes_null_object_carries_parent_condition():
    out = compare_responses([{"u": {"x": 1}}, {"u": None}, {"u": {"x": 1}}])
    child = out["fields"]["u.x"]
    assert child["status"] == "flaky"
    # Not an independent missing_sometimes: it is missing only when the
    # parent is null, so exports guard on the parent.
    assert child["kind"] == "parent_sometimes_null"
    assert child["values"][1] == "(missing)"
    assert child["types"][1] == "missing"


def test_summary_counts_exclude_stable_object_parents():
    out = compare_responses(
        [{"u": {"x": 1}, "id": 1}, {"u": {"x": 1}, "id": 1}, {"u": {"x": 1}, "id": 1}]
    )
    # u is a container (object) -> excluded; u.x and id are leaves -> counted.
    assert out["summary"] == {"stable": 2, "dynamic": 0, "flaky": 0}

    flaky_out = compare_responses([{"u": {"x": 1}}, {"u": None}, {"u": {"x": 1}}])
    # The flaky object parent DOES count (its own shape changes)...
    assert flaky_out["summary"]["flaky"] == 2  # u + u.x
    stable_out = compare_responses(
        [{"u": {"x": 1}, "n": 5}, {"u": {"x": 2}, "n": 5}, {"u": {"x": 3}, "n": 5}]
    )
    # ...and a child-driven change shows up on the child only: u (container)
    # is excluded, u.x is dynamic, n is stable.
    assert stable_out["summary"] == {"stable": 1, "dynamic": 1, "flaky": 0}


class _FakeResponse:
    """Minimal stand-in for requests.Response: only .json() is used."""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _assert_generated_checks_pass_all_calls(calls, rules):
    """The feature's promise, tested directly: build the pytest assertion
    body from call 1 + the stability comparison, then execute it against
    EVERY observed response — none may fail. Returns the joined Postman
    script so callers can assert on it too (Node-free check)."""
    stability = compare_responses(calls)
    lines = pytest_assertions(calls[0], rules, stability)
    body = textwrap.dedent("\n".join(lines))
    src = "def _check(response):\n" + "\n".join(
        "    " + line for line in body.splitlines()
    ) + "\n"
    ast.parse(src)
    namespace = {}
    exec(compile(src, "<generated>", "exec"), namespace)
    for i, call in enumerate(calls):
        try:
            namespace["_check"](_FakeResponse(call))
        except AssertionError as exc:
            raise AssertionError(
                f"generated check failed on call {i + 1}: {exc}\n---\n{body}"
            )
    return "\n".join(postman_assertions(calls[0], rules, stability))


@pytest.mark.parametrize("calls,rules", [
    # missing top-level field
    ([{"id": 1, "note": "a"}, {"id": 1}, {"id": 1, "note": "a"}],
     ['has "note"']),
    # sometimes-null value
    ([{"d": 0.1}, {"d": None}, {"d": 0.2}],
     ['has "d"']),
    # sometimes-null object (the Fix 1 reproduce case); the child key is
    # longer than "u" so the rule matches the child path, exercising the
    # parent guard
    ([{"u": {"token": "a"}}, {"u": None}, {"u": {"token": "a"}}],
     ['u.token should be a string', 'has "u"']),
    # object parent missing entirely in one call
    ([{"u": {"token": "a"}}, {}, {"u": {"token": "a"}}],
     ['u.token should be a string']),
    # list empty in some calls
    ([{"items": [{"sku": "a"}]}, {"items": []}, {"items": [{"sku": "a"}]}],
     ['has "sku"']),
    # top-level list empty in some calls
    ([[{"id": 1}], [], [{"id": 1}]],
     ['has "id"']),
    # int/float mix across calls
    ([{"p": 1}, {"p": 1.5}, {"p": 2}],
     ['has "p"']),
])
def test_generated_checks_pass_for_every_observed_response(calls, rules):
    _assert_generated_checks_pass_all_calls(calls, rules)


def test_export_guards_for_nullable_object_parent():
    calls = [{"u": {"token": "a"}}, {"u": None}, {"u": {"token": "a"}}]
    stability = compare_responses(calls)
    lines = pytest_assertions(calls[0], ['u.token should be a string', 'has "u"'], stability)
    text = "\n".join(lines)
    # Parent check accepts null; child checks sit behind a null guard.
    assert 'assert data["u"] is None or isinstance(data["u"], dict)' in text
    assert 'if data["u"] is not None:' in text
    ast.parse(textwrap.dedent(text))

    js_lines = postman_assertions(calls[0], ['u.token should be a string', 'has "u"'], stability)
    js_text = "\n".join(js_lines)
    assert 'if (jsonData["u"] !== null && jsonData["u"] !== undefined) {' in js_text
    assert '["object"].includes(typeof jsonData["u"])' in js_text


# --- Round 2 -----------------------------------------------------------------

def test_missing_and_null_field_passes_all_calls():
    """Fix 1: a field that is missing in one call and null in another is
    missing_sometimes with null among the observed types — the exported
    checks must accept all three shapes."""
    calls = [{"s": "a"}, {"s": None}, {}]
    stability = compare_responses(calls)
    entry = stability["fields"]["s"]
    assert entry["status"] == "flaky"
    assert entry["kind"] == "missing_sometimes"
    assert entry["types"] == ["string", "null", "missing"]

    pm_text = _assert_generated_checks_pass_all_calls(calls, ['"s" is string'])

    py_text = "\n".join(pytest_assertions(calls[0], ['"s" is string'], stability))
    assert 'if "s" in data:' in py_text
    assert 'assert data["s"] is None or isinstance(data["s"], str)' in py_text

    assert 'if (jsonData.hasOwnProperty("s")) {' in pm_text
    assert 'pm.expect(jsonData["s"] === null || ["string"].includes(typeof jsonData["s"])).to.be.true;' in pm_text


def test_list_root_with_nullable_object_postman_targets_first_item():
    """Fix 2 case: top-level list where a nested object is null in some
    calls. Pytest must pass every call; Postman must check the first item
    of the array, never the array itself."""
    calls = [
        [{"id": 1, "m": {"z": 1}}],
        [{"id": 2, "m": None}],
        [{"id": 3, "m": {"z": 2}}],
    ]
    pm_text = _assert_generated_checks_pass_all_calls(calls, ['"m.z" present'])
    assert "jsonData[0]" in pm_text
    assert "pm.expect(jsonData).to.have.property" not in pm_text


def test_list_root_postman_without_stability_targets_first_item():
    """Fix 2 case with no stability data: the pre-existing bug emitted
    pm.expect(jsonData).to.have.property(...) against the array, which
    always fails. Baseline checks must target jsonData[0]."""
    sample = [{"id": 1, "name": "a"}]
    pm_text = "\n".join(postman_assertions(sample, ['has "id"'], None))
    assert "pm.expect(jsonData).to.be.an('array');" in pm_text
    assert 'pm.expect(jsonData[0]).to.have.property("name");' in pm_text
    assert "pm.expect(jsonData).to.have.property" not in pm_text


# Byte-identity contract: with no stability data (None or ran:False), output
# must match commit 8f1f241 (before the stability feature) exactly. These
# expected outputs were generated from that version of assertion_translator.py
# and are frozen here so future changes can't silently drift.
# Exception: the list_root postman baseline was `pm.expect(jsonData).to.have.
# property("name")`, which always fails against an array — round 2 fixed it to
# check jsonData[0] (see test_list_root_postman_*), so that one line differs
# from 8f1f241 on purpose.
GOLDEN_8F1F241 = {
    "object_root": {
        "sample": {"id": 1, "name": "x", "price": 9.5, "active": True},
        "rules": ['body has field "price"', "id is a number"],
        "pytest": [
            '    data = response.json()',
            '    # Validation: body has field "price"',
            '    assert data["price"] is not None',
            '    assert isinstance(data["price"], (int, float))',
            '    # Validation: id is a number',
            '    assert data["id"] is not None',
            '    assert isinstance(data["id"], int)',
            '    assert isinstance(data["name"], str)',
            '    assert isinstance(data["active"], bool)',
        ],
        "postman": [
            '    var jsonData = pm.response.json();',
            "    pm.expect(jsonData[\"price\"]).to.be.a('number');",
            "    pm.expect(jsonData[\"id\"]).to.be.a('number');",
            '    pm.expect(jsonData).to.have.property("name");',
            '    pm.expect(jsonData).to.have.property("active");',
        ],
    },
    "list_root": {
        "sample": [{"id": 1, "name": "a"}],
        "rules": ['has "id"'],
        "pytest": [
            '    data = response.json()',
            '    assert isinstance(data, list) and len(data) > 0',
            '    item = data[0]',
            '    # Validation: has "id"',
            '    assert item["id"] is not None',
            '    assert isinstance(item["id"], int)',
            '    assert isinstance(item["name"], str)',
        ],
        "postman": [
            '    var jsonData = pm.response.json();',
            "    pm.expect(jsonData).to.be.an('array');",
            "    pm.expect(jsonData[0][\"id\"]).to.be.a('number');",
            '    pm.expect(jsonData[0]).to.have.property("name");',
        ],
    },
    "nested": {
        "sample": {"user": {"id": 1, "address": {"geo": {"lat": 1.0, "lng": 2.0}}}},
        "rules": ["address.geo.lat should be a float", 'has "user"'],
        "pytest": [
            '    data = response.json()',
            '    # Validation: address.geo.lat should be a float',
            '    assert data["user"]["address"] is not None',
            '    assert isinstance(data["user"]["address"], dict)',
            '    # Validation: has "user"',
            '    assert data["user"] is not None',
            '    assert isinstance(data["user"], dict)',
        ],
        "postman": [
            '    var jsonData = pm.response.json();',
            "    pm.expect(jsonData[\"user\"][\"address\"]).to.be.an('object');",
            "    pm.expect(jsonData[\"user\"]).to.be.an('object');",
        ],
    },
    "list_of_objects": {
        "sample": {"items": [{"sku": "a", "qty": 2}], "total": 3},
        "rules": ["items.sku should be a string"],
        "pytest": [
            '    data = response.json()',
            '    # Validation: items.sku should be a string',
            '    assert data["items"] is not None',
            '    assert isinstance(data["items"], list)',
            '    assert isinstance(data["total"], int)',
        ],
        "postman": [
            '    var jsonData = pm.response.json();',
            "    pm.expect(jsonData[\"items\"]).to.be.an('array');",
            '    pm.expect(jsonData).to.have.property("total");',
        ],
    },
}


@pytest.mark.parametrize("name", list(GOLDEN_8F1F241))
def test_exports_byte_identical_to_8f1f241_without_stability(name):
    golden = GOLDEN_8F1F241[name]
    sample, rules = golden["sample"], golden["rules"]
    for stability in (None, {"ran": False, "reason": "skipped for this test"}):
        assert pytest_assertions(sample, rules, stability) == golden["pytest"]
        assert postman_assertions(sample, rules, stability) == golden["postman"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
