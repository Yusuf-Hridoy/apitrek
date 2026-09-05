"""
Unit tests for the llm.mistral_client module.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
import requests
from llm.mistral_client import MistralClient, MistralClientError, MistralTruncationError


def test_mistral_client_missing_key():
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(MistralClientError, match="API key is required"):
            MistralClient(api_key=None)


def test_mistral_client_uses_env_key():
    with patch.dict("os.environ", {"MISTRAL_API_KEY": "test-key"}, clear=True):
        client = MistralClient()
        assert client.api_key == "test-key"
        assert client.model == "mistral-small-latest"


def test_mistral_client_uses_custom_model():
    with patch.dict("os.environ", {"MISTRAL_API_KEY": "test-key", "MISTRAL_MODEL": "custom-model"}, clear=True):
        client = MistralClient()
        assert client.model == "custom-model"


@patch("llm.mistral_client.requests.Session")
def test_send_prompt_success(mock_session_class):
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "choices": [
            {"message": {"content": '{"result": "ok"}'}}
        ]
    }
    mock_session.post.return_value = mock_response
    mock_session_class.return_value = mock_session

    client = MistralClient(api_key="test-key")
    result = client.send_prompt("system", "user")
    assert result == '{"result": "ok"}'


@patch("llm.mistral_client.time.sleep")
@patch("llm.mistral_client.requests.Session")
def test_send_prompt_retries_then_fails(mock_session_class, mock_sleep):
    mock_session = MagicMock()
    mock_session.post.side_effect = requests.exceptions.ConnectionError("Network error")
    mock_session_class.return_value = mock_session

    client = MistralClient(api_key="test-key")
    with pytest.raises(MistralClientError, match="Failed to get response"):
        client.send_prompt("system", "user")

    assert mock_session.post.call_count == 2


@patch("llm.mistral_client.requests.Session")
def test_send_prompt_empty_choices(mock_session_class):
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {"choices": []}
    mock_session.post.return_value = mock_response
    mock_session_class.return_value = mock_session

    client = MistralClient(api_key="test-key")
    with pytest.raises(MistralClientError, match="Empty response choices"):
        client.send_prompt("system", "user")


@patch("llm.mistral_client.requests.Session")
def test_send_prompt_truncated(mock_session_class):
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "choices": [
            {
                "message": {"content": '{"a": '},
                "finish_reason": "length",
            }
        ]
    }
    mock_session.post.return_value = mock_response
    mock_session_class.return_value = mock_session

    client = MistralClient(api_key="test-key")
    with pytest.raises(MistralTruncationError, match="truncated"):
        client.send_prompt("system", "user")


@patch("llm.mistral_client.requests.Session")
def test_send_prompt_fatal_auth_error_fails_fast(mock_session_class):
    """401/403 should not retry; the router needs to fail over immediately."""
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
        "403 Forbidden", response=mock_response
    )
    mock_session.post.return_value = mock_response
    mock_session_class.return_value = mock_session

    client = MistralClient(api_key="test-key")
    with pytest.raises(MistralClientError, match="403"):
        client.send_prompt("system", "user")

    assert mock_session.post.call_count == 1


@patch("llm.mistral_client.time.sleep")
@patch("llm.mistral_client.requests.Session")
def test_send_prompt_timeout_then_success(mock_session_class, mock_sleep):
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "retry success"}}]
    }
    mock_session.post.side_effect = [
        requests.exceptions.Timeout("Network timeout"),
        mock_response,
    ]
    mock_session_class.return_value = mock_session

    client = MistralClient(api_key="test-key")
    result = client.send_prompt("system", "user")
    assert result == "retry success"
    assert mock_session.post.call_count == 2


def _http_error(status):
    resp = MagicMock()
    resp.status_code = status
    return requests.exceptions.HTTPError(f"{status} error", response=resp)


@patch("llm.mistral_client.time.sleep")
@patch("llm.mistral_client.requests.Session")
def test_send_prompt_502_then_success_retries(mock_session_class, mock_sleep):
    """A single transient 502 must retry and succeed — no fallback needed."""
    mock_session = MagicMock()
    ok = MagicMock()
    ok.raise_for_status.return_value = None
    ok.json.return_value = {"choices": [{"message": {"content": "recovered"}}]}
    mock_session.post.side_effect = [_http_error(502), ok]
    mock_session_class.return_value = mock_session

    client = MistralClient(api_key="test-key")
    result = client.send_prompt("system", "user")
    assert result == "recovered"
    assert mock_session.post.call_count == 2
    # Exponential backoff happened (one sleep between the two attempts).
    assert mock_sleep.call_count == 1


@patch("llm.mistral_client.time.sleep")
@patch("llm.mistral_client.requests.Session")
def test_send_prompt_502_every_attempt_raises_after_max_retries(mock_session_class, mock_sleep):
    """Persistent transient failure raises so the router can fail over."""
    mock_session = MagicMock()
    mock_session.post.side_effect = [_http_error(502), _http_error(502)]
    mock_session_class.return_value = mock_session

    client = MistralClient(api_key="test-key")
    with pytest.raises(MistralClientError, match="Failed to get response"):
        client.send_prompt("system", "user")

    assert mock_session.post.call_count == 2  # MAX_RETRIES, then give up


@patch("llm.mistral_client.time.sleep")
@patch("llm.mistral_client.requests.Session")
def test_send_prompt_403_fails_fast_no_retry(mock_session_class, mock_sleep):
    """Phase 5 behavior preserved: fatal auth errors never retry."""
    mock_session = MagicMock()
    mock_session.post.side_effect = [_http_error(403)]
    mock_session_class.return_value = mock_session

    client = MistralClient(api_key="test-key")
    with pytest.raises(MistralClientError, match="403"):
        client.send_prompt("system", "user")

    assert mock_session.post.call_count == 1
    assert mock_sleep.call_count == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
