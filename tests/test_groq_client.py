"""
Unit tests for the llm.groq_client module.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
import requests
from llm.groq_client import GroqClient, GroqClientError


def test_groq_client_missing_key():
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(GroqClientError, match="API key is required"):
            GroqClient(api_key=None)


def test_groq_client_uses_env_key():
    with patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}, clear=True):
        client = GroqClient()
        assert client.api_key == "test-key"
        assert client.model == "openai/gpt-oss-120b"


def test_groq_client_uses_custom_model():
    with patch.dict("os.environ", {"GROQ_API_KEY": "test-key", "GROQ_MODEL": "custom-model"}, clear=True):
        client = GroqClient()
        assert client.model == "custom-model"


@patch("llm.groq_client.requests.Session")
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

    client = GroqClient(api_key="test-key")
    result = client.send_prompt("system", "user")
    assert result == '{"result": "ok"}'

    # Verify OpenAI-compatible payload and endpoint
    args, kwargs = mock_session.post.call_args
    assert args[0] == "https://api.groq.com/openai/v1/chat/completions"
    payload = kwargs["json"]
    assert payload["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "user"},
    ]
    assert payload["temperature"] == 0.2
    assert payload["max_tokens"] == 8192


@patch("llm.groq_client.time.sleep")
@patch("llm.groq_client.requests.Session")
def test_send_prompt_retries_then_fails(mock_session_class, mock_sleep):
    mock_session = MagicMock()
    mock_session.post.side_effect = requests.exceptions.ConnectionError("Network error")
    mock_session_class.return_value = mock_session

    client = GroqClient(api_key="test-key")
    with pytest.raises(GroqClientError, match="Failed to get response"):
        client.send_prompt("system", "user")

    assert mock_session.post.call_count == 2


@patch("llm.groq_client.time.sleep")
@patch("llm.groq_client.requests.Session")
def test_send_prompt_exponential_backoff(mock_session_class, mock_sleep):
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "ok"}}]
    }
    mock_session.post.side_effect = [
        requests.exceptions.ConnectionError("fail 1"),
        mock_response,
    ]
    mock_session_class.return_value = mock_session

    client = GroqClient(api_key="test-key")
    result = client.send_prompt("system", "user")
    assert result == "ok"
    assert mock_session.post.call_count == 2
    # Exponential backoff on the first failure only: 2 * (2 ** 1) = 4
    assert [c.args[0] for c in mock_sleep.call_args_list] == [4]


@patch("llm.groq_client.requests.Session")
def test_send_prompt_empty_choices(mock_session_class):
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {"choices": []}
    mock_session.post.return_value = mock_response
    mock_session_class.return_value = mock_session

    client = GroqClient(api_key="test-key")
    with pytest.raises(GroqClientError, match="Empty response choices"):
        client.send_prompt("system", "user")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
