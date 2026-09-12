"""
Unit tests for exports.python_test_generator.
"""
import ast
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from exports.python_test_generator import generate_pytest_script


def test_generate_minimal_script():
    script = generate_pytest_script("https://api.example.com/items", "GET", {})
    assert "import requests" in script
    assert "ENDPOINT = 'https://api.example.com/items'" in script
    assert "def test_endpoint_is_reachable" in script
    assert "assert response.status_code in (200, 201, 204)" in script


def test_generate_with_positive_case():
    data = {
        "positive_test_cases": [
            {
                "id": "TC-POS-01",
                "title": "Get item 200",
                "description": "Valid request",
                "expected": {"status_code": 200},
            }
        ]
    }
    script = generate_pytest_script("https://api.example.com/items/1", "GET", data)
    assert "def test_positive_get_item_200" in script
    assert "assert response.status_code == 200" in script
    assert "TC-POS-01" in script


def test_generate_with_negative_case():
    data = {
        "negative_test_cases": [
            {
                "id": "TC-NEG-01",
                "title": "Not found 404",
                "expected": {"status_code": 404},
            }
        ]
    }
    script = generate_pytest_script("https://api.example.com/items/999", "GET", data)
    assert "def test_negative_not_found_404" in script
    assert "assert response.status_code == 404" in script


def test_generate_with_edge_case():
    data = {
        "edge_cases": [
            {
                "id": "TC-EDGE-01",
                "title": "Empty body",
                "expected": {"status_code": 200},
            }
        ]
    }
    script = generate_pytest_script("https://api.example.com/items", "GET", data)
    assert "def test_edge_empty_body" in script


def test_generate_with_assertions():
    data = {
        "assertions": [
            {
                "rule": "Status should be 200",
                "category": "status_code",
                "severity": "critical",
            }
        ]
    }
    script = generate_pytest_script("https://api.example.com/items", "GET", data)
    assert "def test_assertion_status_should_be_200" in script
    assert "[critical] status_code: Status should be 200" in script


def test_generate_with_validation_rules():
    data = {
        "positive_test_cases": [
            {
                "id": "TC-POS-01",
                "title": "Valid response",
                "expected": {
                    "status_code": 200,
                    "validation_rules": [
                        "id should be integer",
                        "name should be string",
                        "active should be boolean",
                        "score should be number",
                        "email should be present",
                    ],
                },
            }
        ],
        "sample_response": {
            "id": 1,
            "name": "Widget",
            "active": True,
            "score": 99.5,
            "email": "test@example.com",
        },
    }
    script = generate_pytest_script("https://api.example.com/items", "GET", data)
    assert "assert response.status_code == 200" in script
    assert "data = response.json()" in script
    assert 'assert isinstance(data["id"], int)' in script
    assert 'assert isinstance(data["name"], str)' in script
    assert 'assert isinstance(data["active"], bool)' in script
    assert 'assert isinstance(data["score"], (int, float))' in script
    assert 'assert data["email"] is not None' in script


def test_generate_with_post_method_and_body():
    data = {
        "positive_test_cases": [
            {
                "id": "TC-POS-01",
                "title": "Create item",
                "expected": {"status_code": 201},
                "request": {"method": "POST", "body": {"name": "test"}},
            }
        ]
    }
    script = generate_pytest_script("https://api.example.com/items", "POST", data)
    assert "HTTP_METHOD = 'POST'" in script
    assert "_make_request" in script
    assert "_make_request('POST', ENDPOINT" in script


def test_unique_function_names_for_duplicates():
    data = {
        "positive_test_cases": [
            {"id": "TC-1", "title": "Same name", "expected": {"status_code": 200}},
            {"id": "TC-2", "title": "Same name", "expected": {"status_code": 201}},
        ]
    }
    script = generate_pytest_script("https://api.example.com/items", "GET", data)
    assert "def test_positive_same_name" in script
    assert "def test_positive_same_name_1" in script


def test_runnable_syntax():
    data = {
        "positive_test_cases": [{"title": "OK", "expected": {"status_code": 200}}],
        "negative_test_cases": [{"title": "Fail", "expected": {"status_code": 404}}],
        "assertions": [
            {"rule": "Status is 200", "category": "status", "severity": "high"}
        ],
    }
    script = generate_pytest_script("https://api.example.com/items", "GET", data)
    tree = ast.parse(script)
    assert isinstance(tree, ast.Module)


def test_malformed_assertions_fallback():
    data = {
        "assertions": [
            {},  # empty assertion
            {"rule": "", "category": "", "severity": ""},
        ]
    }
    script = generate_pytest_script("https://api.example.com/items", "GET", data)
    assert "def test_assertion_assertion_0" in script
    assert "def test_assertion_" in script
    tree = ast.parse(script)
    assert isinstance(tree, ast.Module)


def test_missing_endpoint_fallback():
    script = generate_pytest_script("", "GET", {})
    assert "ENDPOINT = 'https://example.com/api'" in script


# --- hostile-input regression guards (quotes/backslashes/newlines must not
# produce invalid Python) ---

HOSTILE = {
    "positive_test_cases": [{"title": 'Get the "active" user', "expected": {"status_code": 200}}],
    "negative_test_cases": [],
    "edge_cases": [],
    "assertions": [
        {"rule": 'body has field "title"', "category": "schema", "severity": "medium"},
        {"rule": 'value must equal "O\'Brien"', "category": "schema", "severity": "low"},
        {"rule": "path uses a backslash \\ and newline\nhere", "category": "schema", "severity": "low"},
        {"rule": 'triple """quote""" payload', "category": "schema", "severity": "low"},
    ],
    "sample_response": {"title": "x"},
}


def test_export_is_valid_python_with_quotes_in_rules():
    script = generate_pytest_script('https://x.com/a?q="v"', "GET", HOSTILE)
    ast.parse(script)   # must not raise


def test_endpoint_with_quote_is_safe():
    script = generate_pytest_script('https://x.com/"; import os', "GET",
                                    {"positive_test_cases": [], "negative_test_cases": [],
                                     "edge_cases": [], "assertions": []})
    ast.parse(script)


def test_normal_output_unchanged_for_plain_text():
    """No special chars -> readable output, no ugly over-escaping."""
    script = generate_pytest_script("https://api.example.com/items", "GET", {
        "positive_test_cases": [
            {"id": "TC-1", "title": "Get item 200", "expected": {"status_code": 200}}
        ],
        "assertions": [
            {"rule": "body has field title", "category": "schema", "severity": "medium"}
        ],
    })
    assert '"""TC-1 - Get item 200"""' in script
    assert '"""[medium] schema: body has field title"""' in script
    ast.parse(script)


# --- corrective-overhaul regression guards ---

PER_CASE_REQUEST = {
    "positive_test_cases": [
        {
            "id": "TC-POS-01",
            "title": "Get item 200",
            "expected": {"status_code": 200},
        }
    ],
    "negative_test_cases": [
        {
            "id": "TC-NEG-01",
            "title": "Missing Authorization header",
            "expected": {"status_code": 401},
            "request": {"headers": {"Authorization": "Bearer expired"}},
        },
        {
            "id": "TC-NEG-04",
            "title": "Non-numeric path parameter",
            "expected": {"status_code": 400},
            "request": {"endpoint": "https://api.example.com/items/abc"},
        },
    ],
    "edge_cases": [
        {
            "id": "TC-EDGE-01",
            "title": "Rate limit burst",
            "expected": {"status_code": [200, 429]},
        }
    ],
}


def test_negative_cases_apply_their_own_request():
    """Negative/edge tests must send the altered URL/headers, not the baseline."""
    script = generate_pytest_script("https://api.example.com/items/1", "GET", PER_CASE_REQUEST)
    ast.parse(script)
    # altered headers are threaded through
    assert 'headers={"Authorization": "Bearer expired"}' in script
    # altered URL is used verbatim
    assert "_make_request(HTTP_METHOD, 'https://api.example.com/items/abc'" in script
    # positive still hits the baseline constant
    assert "test_positive_get_item_200" in script
    pos_section = script.split("# --- Negative Test Cases ---")[0]
    assert "_make_request(HTTP_METHOD, ENDPOINT)" in pos_section


def test_list_status_code_renders_in_check():
    script = generate_pytest_script("https://api.example.com/items", "GET", PER_CASE_REQUEST)
    assert "assert response.status_code in (200, 429)" in script
    assert "== [200, 429]" not in script


def test_non_numeric_status_code_never_emits_bare_identifier():
    """An LLM token like `client_timeout` must not become an undefined name."""
    data = {
        "positive_test_cases": [
            {"id": "TC-1", "title": "Timeout", "expected": {"status_code": "client_timeout"}},
        ]
    }
    script = generate_pytest_script("https://api.example.com/items", "GET", data)
    ast.parse(script)
    assert "client_timeout" not in script.split('"""', 2)[-1]  # not in emitted code...
    assert "assert response.status_code < 500" in script       # ...degraded to safe check


# --- honest assertion-test regression guards ---

def test_ungroundable_assertion_is_skipped_not_faked():
    """Ungroundable rules must not emit a fake passing baseline assert."""
    td = {
        "positive_test_cases": [],
        "negative_test_cases": [],
        "edge_cases": [],
        "assertions": [
            {"rule": "Malformed Authorization header must not expose internal errors",
             "category": "security", "severity": "critical", "grounded": False},
            {"rule": 'body has field "id"', "category": "schema", "severity": "low", "grounded": True},
        ],
        "sample_response": {"id": 1, "title": "x"},
    }
    s = generate_pytest_script("https://x.com/a", "GET", td)
    ast.parse(s)                                    # valid python
    assert "@pytest.mark.skip" in s                 # ungroundable -> skipped
    assert "status_code == 200  # adjust" not in s  # no fake pass
    assert 'data["id"]' in s                        # groundable -> real check
    assert "import pytest" in s                     # skip decorator is valid


def test_skipped_assertion_test_collects():
    """A skipped test must still be collectable by pytest."""
    import subprocess
    import sys
    import tempfile
    import textwrap

    td = {
        "assertions": [
            {"rule": "Malformed Authorization header must not expose internal errors",
             "category": "security", "severity": "critical", "grounded": False},
        ],
    }
    s = generate_pytest_script("https://x.com/a", "GET", td)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "test_gen.py"
        path.write_text(textwrap.dedent(s))
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", str(path)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "test_assertion_malformed_authorization" in result.stdout


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
