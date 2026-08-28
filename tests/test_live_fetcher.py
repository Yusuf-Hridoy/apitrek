"""
Unit tests for the core.live_fetcher module.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
import requests
from core.live_fetcher import fetch_api_response, LiveFetchError, _is_valid_url


def test_is_valid_url():
    assert _is_valid_url("https://api.example.com/items") is True
    assert _is_valid_url("http://localhost:8000/test") is True
    assert _is_valid_url("ftp://invalid.com") is False
    assert _is_valid_url("not-a-url") is False
    assert _is_valid_url("") is False


def test_fetch_invalid_url():
    with pytest.raises(LiveFetchError, match="Invalid URL"):
        fetch_api_response("not-a-url")


def test_fetch_unsupported_method():
    with pytest.raises(LiveFetchError, match="Unsupported HTTP method"):
        fetch_api_response("https://api.example.com", method="CUSTOM")


@patch("core.live_fetcher.safe_request")
def test_fetch_success(mock_request):
    mock_response = MagicMock()
    mock_response.headers = {"Content-Type": "application/json"}
    mock_response.status_code = 200
    mock_response.text = '{"id": 1}'
    mock_response.json.return_value = {"id": 1}
    mock_response.content = b'{"id": 1}'
    mock_request.return_value = mock_response

    result = fetch_api_response("https://api.example.com/items")
    assert result == {"id": 1}
    mock_request.assert_called_once()


@patch("core.live_fetcher.safe_request")
def test_fetch_timeout(mock_request):
    mock_request.side_effect = requests.exceptions.Timeout()
    with pytest.raises(LiveFetchError, match="timed out"):
        fetch_api_response("https://api.example.com/items")


@patch("core.live_fetcher.safe_request")
def test_fetch_connection_error(mock_request):
    mock_request.side_effect = requests.exceptions.ConnectionError("DNS failed")
    with pytest.raises(LiveFetchError, match="Could not connect"):
        fetch_api_response("https://api.example.com/items")


@patch("core.live_fetcher.safe_request")
def test_fetch_html_response(mock_request):
    mock_response = MagicMock()
    mock_response.headers = {"Content-Type": "text/html"}
    mock_response.status_code = 200
    mock_response.text = "<html><body>Error</body></html>"
    mock_response.content = b"<html><body>Error</body></html>"
    mock_request.return_value = mock_response

    with pytest.raises(LiveFetchError, match="HTML instead of JSON"):
        fetch_api_response("https://api.example.com/items")


@patch("core.live_fetcher.safe_request")
def test_fetch_empty_response(mock_request):
    mock_response = MagicMock()
    mock_response.headers = {"Content-Type": "application/json"}
    mock_response.status_code = 200
    mock_response.text = ""
    mock_response.content = b""
    mock_request.return_value = mock_response

    with pytest.raises(LiveFetchError, match="empty response"):
        fetch_api_response("https://api.example.com/items")


@patch("core.live_fetcher.safe_request")
def test_fetch_non_json_response(mock_request):
    mock_response = MagicMock()
    mock_response.headers = {"Content-Type": "text/plain"}
    mock_response.status_code = 200
    mock_response.text = "plain text"
    mock_response.content = b"plain text"
    mock_response.json.side_effect = ValueError("No JSON")
    mock_request.return_value = mock_response

    with pytest.raises(LiveFetchError, match="not valid JSON"):
        fetch_api_response("https://api.example.com/items")


@patch("core.live_fetcher.safe_request")
def test_fetch_500_error(mock_request):
    mock_response = MagicMock()
    mock_response.headers = {"Content-Type": "application/json"}
    mock_response.status_code = 500
    mock_response.text = '{"error": "server error"}'
    mock_response.content = b'{"error": "server error"}'
    mock_request.return_value = mock_response

    with pytest.raises(LiveFetchError, match="server error"):
        fetch_api_response("https://api.example.com/items")


@patch("core.live_fetcher.safe_request")
def test_fetch_401_error(mock_request):
    mock_response = MagicMock()
    mock_response.headers = {"Content-Type": "application/json"}
    mock_response.status_code = 401
    mock_response.text = '{"error": "unauthorized"}'
    mock_response.content = b'{"error": "unauthorized"}'
    mock_request.return_value = mock_response

    with pytest.raises(LiveFetchError, match="401 Unauthorized"):
        fetch_api_response("https://api.example.com/items")


@patch("core.live_fetcher.safe_request")
def test_fetch_post_with_body(mock_request):
    mock_response = MagicMock()
    mock_response.headers = {"Content-Type": "application/json"}
    mock_response.status_code = 200
    mock_response.text = '{"success": true}'
    mock_response.json.return_value = {"success": True}
    mock_response.content = b'{"success": true}'
    mock_request.return_value = mock_response

    result = fetch_api_response(
        "https://api.example.com/items",
        method="POST",
        headers={"Authorization": "Bearer token"},
        request_body={"name": "test"},
    )

    assert result == {"success": True}
    kwargs = mock_request.call_args.kwargs
    assert kwargs["method"] == "POST"
    assert kwargs["url"] == "https://api.example.com/items"
    assert kwargs["json"] == {"name": "test"}
    assert kwargs["headers"]["Authorization"] == "Bearer token"
    assert kwargs["headers"]["Content-Type"] == "application/json"


@patch("core.live_fetcher.safe_request")
def test_fetch_response_too_large(mock_request):
    mock_response = MagicMock()
    mock_response.headers = {"Content-Length": str(2 * 1024 * 1024)}
    mock_response.status_code = 200
    mock_response.content = b"x" * (2 * 1024 * 1024)
    mock_request.return_value = mock_response

    with pytest.raises(LiveFetchError, match="Response too large"):
        fetch_api_response("https://api.example.com/items")


@patch("core.live_fetcher.safe_request")
def test_fetch_list_json(mock_request):
    mock_response = MagicMock()
    mock_response.headers = {"Content-Type": "application/json"}
    mock_response.status_code = 200
    mock_response.text = '[{"id": 1}]'
    mock_response.json.return_value = [{"id": 1}]
    mock_response.content = b'[{"id": 1}]'
    mock_request.return_value = mock_response

    result = fetch_api_response("https://api.example.com/items")
    assert result == [{"id": 1}]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
