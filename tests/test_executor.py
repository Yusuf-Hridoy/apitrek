"""
Unit tests for the core.test_executor module.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
import requests
from core.test_executor import execute_test_case, execute_test_suite

ENDPOINT = "https://api.example.com/items/1"


def _mock_response(status=200, payload=None, text=None):
    response = MagicMock()
    response.status_code = status
    body = payload if payload is not None else {}
    response.json.return_value = body
    response.text = text if text is not None else '{"id": 1}'
    return response


@patch("core.test_executor.safe_request")
def test_positive_case_passes_on_expected_status(mock_request):
    mock_request.return_value = _mock_response(status=200, payload={"id": 1, "title": "x"})
    result = execute_test_case(
        {"id": "TC-POS-01", "title": "Valid fetch", "expected": {"status_code": 200}},
        ENDPOINT,
        "GET",
    )
    assert result["passed"] is True
    assert result["actual_status"] == 200
    assert result["expected_status"] == 200
    assert result["error_message"] is None
    assert result["category"] == "positive"
    assert isinstance(result["duration_ms"], int)


@patch("core.test_executor.safe_request")
def test_status_mismatch_fails(mock_request):
    mock_request.return_value = _mock_response(status=404)
    result = execute_test_case(
        {"id": "TC-1", "title": "t", "expected": {"status_code": 200}},
        ENDPOINT,
        "GET",
    )
    assert result["passed"] is False
    status_assertions = [a for a in result["assertion_results"] if "Status code" in a["assertion"]]
    assert status_assertions and status_assertions[0]["passed"] is False


@patch("core.test_executor.safe_request")
def test_network_error_returns_structured_result(mock_request):
    mock_request.side_effect = requests.exceptions.ConnectionError("boom")
    result = execute_test_case({"id": "TC-1", "title": "t"}, ENDPOINT, "GET")
    assert result["passed"] is False
    assert result["actual_status"] == 0
    assert "boom" in result["error_message"]


@patch("core.test_executor.safe_request")
def test_timeout_returns_structured_result(mock_request):
    mock_request.side_effect = requests.exceptions.Timeout()
    result = execute_test_case({"id": "TC-1", "title": "t"}, ENDPOINT, "GET")
    assert result["passed"] is False
    assert "timed out" in result["error_message"]


@patch("core.test_executor.safe_request")
def test_negative_case_without_request_falls_back_to_generic_mutation(mock_request):
    # No "request" object in the case: the generic negative mutation applies
    # (fallback only — cases WITH a request send exactly what it describes).
    mock_request.return_value = _mock_response(status=400)
    result = execute_test_case(
        {"id": "TC-NEG-01", "title": "t", "category": "negative",
         "expected": {"status_code": 400}},
        ENDPOINT,
        "POST",
        headers={"Authorization": "Bearer real-token"},
        body={"name": "x", "price": 10},
    )
    _, kwargs = mock_request.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer invalid-token-000"
    # First body field removed
    assert "name" not in kwargs["json"]
    assert kwargs["json"] == {"price": 10}
    # The fallback is disclosed as info, not counted as a passed check.
    info = [a for a in result["assertion_results"] if a.get("kind") == "info"]
    assert any("generic negative change" in a["detail"] for a in info)
    assert not any(a["assertion"].startswith("Request mutation")
                   for a in result["assertion_results"])


@patch("core.test_executor.safe_request")
def test_edge_case_without_request_falls_back_to_boundary_values(mock_request):
    mock_request.return_value = _mock_response(status=200)
    execute_test_case(
        {"id": "TC-EDGE-01", "title": "t", "category": "edge"},
        ENDPOINT,
        "POST",
        body={"name": "x", "description": "y", "note": "z"},
    )
    _, kwargs = mock_request.call_args
    assert kwargs["json"]["name"] == ""
    assert kwargs["json"]["description"] == "A" * 10000
    assert kwargs["json"]["note"] is None


@patch("core.test_executor.safe_request")
def test_validation_rules_checked(mock_request):
    mock_request.return_value = _mock_response(status=200, payload={"id": 1, "price": 9.99})
    result = execute_test_case(
        {
            "id": "TC-1",
            "title": "t",
            "expected": {
                "status_code": 200,
                "validation_rules": ["Field 'price' must be a number", "Field 'missing' is present"],
            },
        },
        ENDPOINT,
        "GET",
    )
    rule_results = [a for a in result["assertion_results"] if a.get("kind") != "info"]
    assert rule_results[0]["passed"] is True  # status code
    assert rule_results[1]["passed"] is True  # price is a number
    assert rule_results[2]["passed"] is False  # missing field
    assert result["passed"] is False


@patch("core.test_executor.safe_request")
def test_assertion_category_runs_rule(mock_request):
    mock_request.return_value = _mock_response(status=200, payload=[{"id": 1}])
    result = execute_test_case(
        {"id": "", "rule": "Response status code is 200", "category": "assertion"},
        ENDPOINT,
        "GET",
    )
    assert result["passed"] is True
    assert any("status code" in a["assertion"].lower() for a in result["assertion_results"])


@patch("core.test_executor.safe_request")
def test_response_preview_truncated(mock_request):
    mock_request.return_value = _mock_response(status=200, text="x" * 1000)
    result = execute_test_case({"id": "TC-1", "title": "t"}, ENDPOINT, "GET")
    assert len(result["actual_response_preview"]) == 500


@patch("core.test_executor.safe_request")
def test_uses_15s_timeout(mock_request):
    mock_request.return_value = _mock_response()
    execute_test_case({"id": "TC-1", "title": "t"}, ENDPOINT, "GET")
    _, kwargs = mock_request.call_args
    assert kwargs["timeout"] == 15


@patch("core.test_executor.safe_request")
def test_execute_test_suite_runs_all_sequentially(mock_request):
    mock_request.return_value = _mock_response(status=200)
    cases = [
        {"id": "TC-1", "title": "a", "expected": {"status_code": 200}},
        {"id": "TC-2", "title": "b", "expected": {"status_code": 200}},
        {"id": "TC-3", "title": "c", "expected": {"status_code": 200}},
    ]
    suite = execute_test_suite(cases, ENDPOINT, "GET")
    assert mock_request.call_count == 3
    assert [r["test_case_id"] for r in suite["results"]] == ["TC-1", "TC-2", "TC-3"]
    assert suite["passed"] == 3
    assert suite["failed"] == suite["needs_review"] == suite["not_run"] == 0
    assert suite["executed_count"] == 3
    assert suite["pass_rate"] == "100%"


# --- Request fidelity: the runner sends each case's own request -------------

def _case_with_request(category, headers=None, body="__unset__", **extra):
    request = {
        "endpoint": ENDPOINT,
        "method": "POST",
        "headers": headers or {},
    }
    if body != "__unset__":
        request["body"] = body
    case = {"id": "TC-X", "title": "t", "category": category, "request": request}
    case.update(extra)
    return case


@patch("core.test_executor.safe_request")
def test_negative_missing_auth_sends_no_auth(mock_request):
    mock_request.return_value = _mock_response(status=401)
    case = _case_with_request("negative", headers={"Accept": "application/json"})
    execute_test_case(case, ENDPOINT, "POST", headers={"Authorization": "Bearer real"})
    _, kwargs = mock_request.call_args
    assert "Authorization" not in kwargs["headers"]
    assert kwargs["headers"]["Accept"] == "application/json"


@patch("core.test_executor.safe_request")
def test_negative_case_auth_header_is_authoritative(mock_request):
    mock_request.return_value = _mock_response(status=401)
    case = _case_with_request(
        "negative", headers={"Authorization": "Bearer expired"})
    execute_test_case(case, ENDPOINT, "POST", headers={"Authorization": "Bearer real"})
    _, kwargs = mock_request.call_args
    # The case's token wins — no fallback "Bearer invalid-token-000".
    assert kwargs["headers"]["Authorization"] == "Bearer expired"


@patch("core.test_executor.safe_request")
def test_positive_case_keeps_form_auth(mock_request):
    mock_request.return_value = _mock_response(status=200)
    case = _case_with_request("positive", headers={"Accept": "application/json"})
    execute_test_case(case, ENDPOINT, "POST", headers={"Authorization": "Bearer real"})
    _, kwargs = mock_request.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer real"


@patch("core.test_executor.safe_request")
def test_empty_case_header_value_omits_header(mock_request):
    mock_request.return_value = _mock_response(status=401)
    case = _case_with_request("negative", headers={"Authorization": ""})
    execute_test_case(case, ENDPOINT, "POST", headers={"Authorization": "Bearer real"})
    _, kwargs = mock_request.call_args
    assert "Authorization" not in kwargs["headers"]


@patch("core.test_executor.safe_request")
def test_case_body_wins_over_form_body(mock_request):
    mock_request.return_value = _mock_response(status=200)
    case = _case_with_request("positive", body={"case": 1})
    execute_test_case(case, ENDPOINT, "POST", body={"form": 2})
    _, kwargs = mock_request.call_args
    assert kwargs["json"] == {"case": 1}


@patch("core.test_executor.safe_request")
def test_empty_dict_case_body_is_sent_as_empty_dict(mock_request):
    mock_request.return_value = _mock_response(status=200)
    case = _case_with_request("edge", body={})
    execute_test_case(case, ENDPOINT, "POST", body={"form": 2})
    _, kwargs = mock_request.call_args
    assert kwargs["json"] == {}


@patch("core.test_executor.safe_request")
def test_case_with_request_gets_no_blind_mutation(mock_request):
    mock_request.return_value = _mock_response(status=400)
    case = _case_with_request(
        "negative", headers={"Authorization": "Basic dXNlcjpwYXNz"},
        body={"name": "x"}, expected={"status_code": 400})
    execute_test_case(case, ENDPOINT, "POST", body={"form": 1})
    _, kwargs = mock_request.call_args
    # "Basic instead of Bearer" sends Basic — the body is untouched too.
    assert kwargs["headers"]["Authorization"] == "Basic dXNlcjpwYXNz"
    assert kwargs["json"] == {"name": "x"}


# --- Expected status can be a list ------------------------------------------

@patch("core.test_executor.safe_request")
def test_list_of_expected_status_codes(mock_request):
    case = {"id": "TC-X", "title": "t", "expected": {"status_code": [200, 429]}}
    mock_request.return_value = _mock_response(status=200)
    result = execute_test_case(case, ENDPOINT, "GET")
    assert result["passed"] is True
    assert result["outcome"] == "passed"
    assert result["expected_status"] == [200, 429]  # original value kept
    status = [a for a in result["assertion_results"] if a.get("kind") != "info"][0]
    assert status["assertion"] == "Status code is 200 or 429"
    assert status["detail"] == "Expected HTTP 200 or 429, got 200"

    mock_request.return_value = _mock_response(status=500)
    result = execute_test_case(case, ENDPOINT, "GET")
    assert result["passed"] is False
    assert result["outcome"] == "failed"


@patch("core.test_executor.safe_request")
def test_single_expected_status_keeps_today_wording(mock_request):
    mock_request.return_value = _mock_response(status=200)
    result = execute_test_case(
        {"id": "TC-X", "title": "t", "expected": {"status_code": 200}},
        ENDPOINT, "GET")
    status = [a for a in result["assertion_results"] if a.get("kind") != "info"][0]
    assert status["assertion"] == "Status code is 200"
    assert status["detail"] == "Expected HTTP 200, got 200"


# --- Four outcomes -----------------------------------------------------------

@patch("core.test_executor.safe_request")
def test_unverifiable_only_is_needs_review(mock_request):
    mock_request.return_value = _mock_response(status=200, payload={"id": 1})
    result = execute_test_case(
        {"id": "TC-X", "title": "t", "rule": "Response time is under 500ms"},
        ENDPOINT, "GET")
    assert result["outcome"] == "needs_review"
    assert result["passed"] is False
    rule = [a for a in result["assertion_results"] if a.get("kind") != "info"][0]
    assert rule["verifiable"] is False
    assert rule["passed"] is False  # no fake green check


@patch("core.test_executor.safe_request")
def test_invalid_header_is_not_run(mock_request):
    mock_request.side_effect = requests.exceptions.InvalidHeader("bad header")
    result = execute_test_case(
        {"id": "TC-X", "title": "CRLF injection", "expected": {"status_code": 400}},
        ENDPOINT, "GET")
    assert result["outcome"] == "not_run"
    assert result["passed"] is False
    assert result["actual_status"] == 0
    assert result["error_message"].startswith("Not sent:")


@patch("core.test_executor.safe_request")
def test_ssrf_block_is_not_run(mock_request):
    from core.url_guard import BlockedURLError
    mock_request.side_effect = BlockedURLError("private host")
    result = execute_test_case(
        {"id": "TC-X", "title": "t", "expected": {"status_code": 200}},
        ENDPOINT, "GET")
    assert result["outcome"] == "not_run"
    assert result["passed"] is False
    assert "Not sent:" in result["error_message"]


@patch("core.test_executor.safe_request")
def test_timeout_is_failed_not_not_run(mock_request):
    mock_request.side_effect = requests.exceptions.Timeout()
    result = execute_test_case({"id": "TC-X", "title": "t"}, ENDPOINT, "GET")
    assert result["outcome"] == "failed"
    assert result["passed"] is False


@patch("core.test_executor.safe_request")
def test_connection_error_is_failed(mock_request):
    mock_request.side_effect = requests.exceptions.ConnectionError("boom")
    result = execute_test_case({"id": "TC-X", "title": "t"}, ENDPOINT, "GET")
    assert result["outcome"] == "failed"


# --- Info entries are not checks ---------------------------------------------

@patch("core.test_executor.safe_request")
def test_info_entry_describes_request_without_leaking_values(mock_request):
    mock_request.return_value = _mock_response(status=200)
    result = execute_test_case(
        {"id": "TC-X", "title": "t", "expected": {"status_code": 200}},
        ENDPOINT, "GET", headers={"Authorization": "Bearer super-secret", "Accept": "application/json"})
    info = [a for a in result["assertion_results"] if a.get("kind") == "info"]
    assert len(info) == 1  # the case has a request via the form only — no fallback note
    detail = info[0]["detail"]
    assert info[0]["assertion"] == "Request sent"
    assert "Accept" in detail
    assert "Authorization (masked)" in detail
    assert "super-secret" not in detail
    assert "no body" in detail
    # The info line never affects the outcome.
    assert result["outcome"] == "passed"


# --- Fix 1 (review): one case must never corrupt the body of the next -------

@patch("core.test_executor.safe_request")
def test_edge_fallback_does_not_corrupt_form_body_or_later_cases(mock_request):
    """The reproduce case: an edge fallback mutates its body in place. The
    mutation must land on a copy — the next case and the form body itself
    stay intact."""
    mock_request.return_value = _mock_response(status=200)
    cases = [
        {"title": "edge without request", "category": "edge",
         "expected": {"status_code": 200}},
        {"title": "positive after it", "category": "positive",
         "request": {"headers": {}}, "expected": {"status_code": 200}},
    ]
    form_body = {"name": "morpheus", "job": "leader"}
    execute_test_suite(cases, ENDPOINT, "POST", {}, form_body)

    sent = [call.kwargs["json"] for call in mock_request.call_args_list]
    # The edge case mutated its own copy; the positive case got the real body.
    assert sent[0] == {"name": "", "job": "A" * 10000}
    assert sent[1] == {"name": "morpheus", "job": "leader"}
    assert form_body == {"name": "morpheus", "job": "leader"}


@patch("core.test_executor.safe_request")
def test_edge_fallback_does_not_modify_the_shared_body(mock_request):
    """Re-running the same edge fallback must mutate the same way every time
    — the shared form body (and any stored case) is never modified."""
    mock_request.return_value = _mock_response(status=200)
    case = {"id": "TC-E", "title": "edge", "category": "edge"}
    body = {"a": 1, "b": 2}
    execute_test_case(case, ENDPOINT, "POST", body=body)
    execute_test_case(case, ENDPOINT, "POST", body=body)
    sent = [call.kwargs["json"] for call in mock_request.call_args_list]
    assert sent[0] == sent[1] == {"a": "", "b": "A" * 10000}
    assert body == {"a": 1, "b": 2}


@patch("core.test_executor.safe_request")
def test_case_request_body_is_never_modified(mock_request):
    """A case's own request body is deep-copied too — re-runs send the
    original body every time."""
    mock_request.return_value = _mock_response(status=200)
    case = {"id": "TC-E", "title": "edge", "category": "edge",
            "request": {"body": {"a": 1, "b": 2}}}
    execute_test_case(case, ENDPOINT, "POST")
    execute_test_case(case, ENDPOINT, "POST")
    sent = [call.kwargs["json"] for call in mock_request.call_args_list]
    assert sent[0] == sent[1] == {"a": 1, "b": 2}
    assert case["request"]["body"] == {"a": 1, "b": 2}


@patch("core.test_executor.safe_request")
def test_negative_fallback_then_positive_body_is_intact(mock_request):
    mock_request.return_value = _mock_response(status=400)
    negative = {"id": "TC-N", "title": "neg", "category": "negative",
                "expected": {"status_code": 400}}
    positive = {"id": "TC-P", "title": "pos", "category": "positive",
                "request": {"headers": {}}}
    form_body = {"name": "x", "price": 10}
    execute_test_suite([negative, positive], ENDPOINT, "POST", {}, form_body)
    sent = [call.kwargs["json"] for call in mock_request.call_args_list]
    # Fallback removed 'name' from its own copy only.
    assert sent[0] == {"price": 10}
    assert sent[1] == {"name": "x", "price": 10}
    assert form_body == {"name": "x", "price": 10}


@patch("core.test_executor.safe_request")
def test_suite_summary_excludes_needs_review_and_not_run(mock_request):
    cases = [
        {"id": "TC-1", "title": "ok", "expected": {"status_code": 200}},
        {"id": "TC-2", "title": "rate", "request": {"endpoint": ENDPOINT},
         "expected": {"status_code": [200, 429]}},
        {"id": "TC-3", "title": "unverifiable", "rule": "Response time is fast"},
        {"id": "TC-4", "title": "broken", "expected": {"status_code": 200}},
    ]
    mock_request.side_effect = [
        _mock_response(status=200),   # TC-1 passes
        _mock_response(status=429),   # TC-2 in [200, 429]
        _mock_response(status=200),   # TC-3 needs_review
        requests.exceptions.InvalidHeader("bad"),  # TC-4 not_run
    ]
    suite = execute_test_suite(cases, ENDPOINT, "GET")
    assert suite["passed"] == 2
    assert suite["needs_review"] == 1
    assert suite["not_run"] == 1
    assert suite["failed"] == 0
    assert suite["executed_count"] == 3
    # pass_rate = passed / (passed + failed)
    assert suite["pass_rate"] == "100%"


# --- skipped cases -----------------------------------------------------------

@patch("core.test_executor.safe_request")
def test_skipped_case_never_sends_request(mock_request):
    case = {
        "id": "TC-NEG-01", "title": "Missing token is rejected",
        "category": "negative",
        "expected": {"status_code": 401},
        "skip": {"reason": "No login needed here, so a test expecting 401 or 403 can't pass."},
    }
    result = execute_test_case(case, ENDPOINT, "GET")
    assert result["outcome"] == "skipped"
    assert result["passed"] is False
    assert result["actual_status"] is None
    assert result["assertion_results"] == []
    assert result["error_message"] == (
        "No login needed here, so a test expecting 401 or 403 can't pass."
    )
    mock_request.assert_not_called()


@patch("core.test_executor.safe_request")
def test_skipped_case_without_reason_defaults(mock_request):
    result = execute_test_case(
        {"id": "TC-1", "title": "t", "skip": {"reason": ""}}, ENDPOINT, "GET"
    )
    assert result["outcome"] == "skipped"
    assert result["error_message"] == "This test was skipped."
    mock_request.assert_not_called()


@patch("core.test_executor.safe_request")
def test_suite_summary_counts_skipped_separately(mock_request):
    cases = [
        {"id": "TC-1", "title": "ok", "expected": {"status_code": 200}},
        {"id": "TC-2", "title": "fails", "expected": {"status_code": 404}},
        {"id": "TC-3", "title": "login test", "expected": {"status_code": 401},
         "skip": {"reason": "No login needed here."}},
    ]
    mock_request.side_effect = [
        _mock_response(status=200),  # TC-1 passes
        _mock_response(status=200),  # TC-2 expected 404 -> fails
        # TC-3 is skipped: never sent, so only two requests total
    ]
    suite = execute_test_suite(cases, ENDPOINT, "GET")
    assert suite["passed"] == 1
    assert suite["failed"] == 1
    assert suite["skipped_count"] == 1
    assert suite["total"] == 3
    # Skipped is excluded from the pass rate: 1 / (1 + 1)
    assert suite["pass_rate"] == "50%"
    assert mock_request.call_count == 2


# --- credentials: the user's real ones win on positive cases ------------------

@patch("core.test_executor.safe_request")
def test_positive_case_keeps_form_credentials_over_case_fake(mock_request):
    """The AI often writes a fake token into the case request. On a positive
    case the form's real credential must win; other case headers still apply."""
    mock_request.return_value = _mock_response(status=200)
    case = {
        "id": "TC-POS-01", "title": "ok", "category": "positive",
        "request": {"endpoint": ENDPOINT, "method": "GET",
                    "headers": {"Cookie": "token=FAKE", "X-Trace": "1"}},
        "expected": {"status_code": 200},
    }
    execute_test_case(case, ENDPOINT, "GET", headers={"Cookie": "token=REAL"})
    _, kwargs = mock_request.call_args
    assert kwargs["headers"]["Cookie"] == "token=REAL"
    assert kwargs["headers"]["X-Trace"] == "1"


@patch("core.test_executor.safe_request")
def test_assertion_case_keeps_form_credentials_too(mock_request):
    mock_request.return_value = _mock_response(status=200)
    case = {
        "id": "TC-ASSERT-01", "title": "a", "category": "assertion",
        "request": {"endpoint": ENDPOINT, "method": "GET",
                    "headers": {"Authorization": "Bearer fake-ai-token"}},
        "expected": {"status_code": 200},
    }
    execute_test_case(case, ENDPOINT, "GET",
                      headers={"Authorization": "Bearer real-user-token"})
    _, kwargs = mock_request.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer real-user-token"


@patch("core.test_executor.safe_request")
def test_case_header_applies_when_form_lacks_it(mock_request):
    mock_request.return_value = _mock_response(status=200)
    case = {
        "id": "TC-POS-01", "title": "ok", "category": "positive",
        "request": {"endpoint": ENDPOINT, "method": "GET",
                    "headers": {"X-Custom": "yes"}},
        "expected": {"status_code": 200},
    }
    execute_test_case(case, ENDPOINT, "GET")
    _, kwargs = mock_request.call_args
    assert kwargs["headers"]["X-Custom"] == "yes"


@patch("core.test_executor.safe_request")
def test_positive_case_can_still_omit_a_header(mock_request):
    """The empty-string 'omit this header' rule still works on positives."""
    mock_request.return_value = _mock_response(status=200)
    case = {
        "id": "TC-POS-01", "title": "ok", "category": "positive",
        "request": {"endpoint": ENDPOINT, "method": "GET",
                    "headers": {"X-Extra": ""}},
        "expected": {"status_code": 200},
    }
    execute_test_case(case, ENDPOINT, "GET", headers={"X-Extra": "drop-me"})
    _, kwargs = mock_request.call_args
    assert "X-Extra" not in kwargs["headers"]


@patch("core.test_executor.safe_request")
def test_negative_case_headers_stay_authoritative(mock_request):
    """Regression guard: negative cases still send the case's own (fake)
    credential so the 'missing/invalid token' scenario is what gets tested."""
    mock_request.return_value = _mock_response(status=403)
    case = {
        "id": "TC-NEG-01", "title": "bad token", "category": "negative",
        "request": {"endpoint": ENDPOINT, "method": "GET",
                    "headers": {"Cookie": "token=FAKE"}},
        "expected": {"status_code": 403},
    }
    execute_test_case(case, ENDPOINT, "GET", headers={"Cookie": "token=REAL"})
    _, kwargs = mock_request.call_args
    assert kwargs["headers"]["Cookie"] == "token=FAKE"


# --- rule checks: headers are headers, not body fields ------------------------

def _mock_response_with_headers(status=200, payload=None, headers=None):
    response = _mock_response(status=status, payload=payload)
    response.headers = headers or {}
    return response


@patch("core.test_executor.safe_request")
def test_header_rule_checked_against_headers_not_body(mock_request):
    """A header-targeted rule fails as a header failure even when the status
    matched — never reported as a missing BODY field."""
    mock_request.return_value = _mock_response_with_headers(
        status=200, payload={"id": 1}, headers={"Content-Type": "application/json"}
    )
    case = {
        "id": "TC-1", "title": "encoding", "category": "edge",
        "request": {"endpoint": ENDPOINT, "method": "GET"},
        "expected": {"status_code": 200,
                     "validation_rules": ["header 'Content-Encoding' equals 'gzip'"]},
    }
    result = execute_test_case(case, ENDPOINT, "GET")
    assert result["outcome"] == "failed"
    rule_results = [a for a in result["assertion_results"] if a.get("kind") != "info"]
    # Status matched (200 == 200): the failure is the rule, not the status.
    status_entry = next(a for a in rule_results if a["assertion"].startswith("Status code is"))
    assert status_entry["passed"] is True
    header_entry = next(a for a in rule_results if "Content-Encoding" in a["assertion"])
    assert header_entry["passed"] is False
    assert "header 'Content-Encoding' MISSING" in header_entry["detail"]
    assert "field" not in header_entry["detail"]


@patch("core.test_executor.safe_request")
def test_header_rule_passes_when_header_present(mock_request):
    mock_request.return_value = _mock_response_with_headers(
        status=200, payload={"id": 1}, headers={"Content-Encoding": "gzip"}
    )
    case = {
        "id": "TC-1", "title": "encoding", "category": "positive",
        "request": {"endpoint": ENDPOINT, "method": "GET"},
        "expected": {"status_code": 200,
                     "validation_rules": ["header 'Content-Encoding' equals 'gzip'"]},
    }
    result = execute_test_case(case, ENDPOINT, "GET")
    assert result["outcome"] == "passed"
    header_entry = next(a for a in result["assertion_results"]
                        if "Content-Encoding" in a.get("assertion", ""))
    assert header_entry["passed"] is True
    assert "header 'Content-Encoding' present" in header_entry["detail"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
