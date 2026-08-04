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
        assert client.model == "mistral-large-latest"


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


@patch("llm.mistral_client.requests.Session")
def test_send_prompt_retries_then_fails(mock_session_class):
    mock_session = MagicMock()
    mock_session.post.side_effect = requests.exceptions.ConnectionError("Network error")
    mock_session_class.return_value = mock_session

    client = MistralClient(api_key="test-key")
    with pytest.raises(MistralClientError, match="Failed to get response"):
        client.send_prompt("system", "user")

    assert mock_session.post.call_count == 3


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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
