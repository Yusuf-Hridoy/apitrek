"""
Unit tests for the llm.github_models_client module.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
import requests
from llm.github_models_client import GitHubModelsClient, GitHubModelsError


def test_github_models_client_missing_key():
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(GitHubModelsError, match="API key is required"):
            GitHubModelsClient(api_key=None)


def test_github_models_client_uses_env_key():
    with patch.dict("os.environ", {"GITHUB_MODELS_API_KEY": "test-key"}, clear=True):
        client = GitHubModelsClient()
        assert client.api_key == "test-key"
        assert client.model == "gpt-4o"


def test_github_models_client_uses_custom_model():
    with patch.dict(
        "os.environ",
        {"GITHUB_MODELS_API_KEY": "test-key", "GITHUB_MODEL": "custom-model"},
        clear=True,
    ):
        client = GitHubModelsClient()
        assert client.model == "custom-model"


@patch("llm.github_models_client.requests.Session")
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

    client = GitHubModelsClient(api_key="test-key")
    result = client.send_prompt("system", "user")
    assert result == '{"result": "ok"}'

    # Verify endpoint and OpenAI-compatible payload
    args, kwargs = mock_session.post.call_args
    assert args[0] == "https://models.inference.ai.azure.com/chat/completions"
    payload = kwargs["json"]
    assert payload["model"] == "gpt-4o"
    assert payload["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "user"},
    ]


@patch("llm.github_models_client.requests.Session")
def test_send_prompt_retries_then_fails(mock_session_class):
    mock_session = MagicMock()
    mock_session.post.side_effect = requests.exceptions.ConnectionError("Network error")
    mock_session_class.return_value = mock_session

    client = GitHubModelsClient(api_key="test-key")
    with pytest.raises(GitHubModelsError, match="Failed to get response"):
        client.send_prompt("system", "user")

    assert mock_session.post.call_count == 3


@patch("llm.github_models_client.requests.Session")
def test_send_prompt_none_content(mock_session_class):
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "choices": [{"message": {"content": None}}]
    }
    mock_session.post.return_value = mock_response
    mock_session_class.return_value = mock_session

    client = GitHubModelsClient(api_key="test-key")
    with pytest.raises(GitHubModelsError, match="None content"):
        client.send_prompt("system", "user")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
