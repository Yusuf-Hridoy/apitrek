"""
Unit tests for core.url_guard SSRF protection.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from core.url_guard import (
    validate_public_url,
    safe_request,
    BlockedURLError,
)


PUBLIC_IP = "93.184.216.34"


def _patch_resolve(ips):
    return patch("core.url_guard._resolve_all_ips", return_value=ips)


def test_blocks_loopback_ipv4():
    with pytest.raises(BlockedURLError, match="non-public address"):
        validate_public_url("http://127.0.0.1/")


def test_blocks_loopback_ipv6():
    with _patch_resolve(["::1"]):
        with pytest.raises(BlockedURLError, match="non-public address"):
            validate_public_url("http://[::1]/")


def test_blocks_ipv4_mapped_ipv6_loopback():
    with _patch_resolve(["::ffff:127.0.0.1"]):
        with pytest.raises(BlockedURLError, match="non-public address"):
            validate_public_url("http://localhost/")


def test_allows_dns64_well_known_prefix():
    # 64:ff9b::/96 embeds 34.202.68.214 (public). It must not be blocked as "reserved".
    with _patch_resolve(["64:ff9b::22ca:44d6"]):
        validate_public_url("http://httpbin.org/get")  # no exception


def test_blocks_dns64_embedded_loopback():
    # 64:ff9b::7f00:0001 embeds 127.0.0.1 — still loopback, must be blocked.
    with _patch_resolve(["64:ff9b::7f00:1"]):
        with pytest.raises(BlockedURLError, match="non-public address"):
            validate_public_url("http://httpbin.org/get")


def test_blocks_metadata_endpoint():
    with pytest.raises(BlockedURLError, match="non-public address"):
        validate_public_url("http://169.254.169.254/latest/meta-data/")


@pytest.mark.parametrize("url", [
    "http://10.0.0.5/",
    "http://192.168.1.1/",
    "http://172.16.0.1/",
    "http://100.64.0.1/",
])
def test_blocks_private_ranges(url):
    with pytest.raises(BlockedURLError, match="non-public address"):
        validate_public_url(url)


def test_blocks_localhost_resolution():
    with _patch_resolve(["127.0.0.1"]):
        with pytest.raises(BlockedURLError, match="non-public address"):
            validate_public_url("http://localhost/")


@pytest.mark.parametrize("url", [
    "ftp://example.com/file",
    "file:///etc/passwd",
    "javascript:alert(1)",
])
def test_blocks_non_http_schemes(url):
    with pytest.raises(BlockedURLError, match="Only http:// and https://"):
        validate_public_url(url)


def test_allows_public_url():
    with _patch_resolve([PUBLIC_IP]):
        validate_public_url("http://example.com/")  # no exception


def test_blocks_when_any_record_is_private():
    with _patch_resolve([PUBLIC_IP, "127.0.0.1"]):
        with pytest.raises(BlockedURLError, match="non-public address"):
            validate_public_url("http://example.com/")


def test_blocks_missing_url():
    with pytest.raises(BlockedURLError, match="Missing or invalid URL"):
        validate_public_url("")
    with pytest.raises(BlockedURLError, match="Missing or invalid URL"):
        validate_public_url(None)


def test_blocks_url_without_host():
    with pytest.raises(BlockedURLError, match="URL has no host"):
        validate_public_url("http:///path")


def _resolve_by_host(host, port):
    """Return a public IP for example.com, loopback for localhost/127.0.0.1."""
    if host in ("127.0.0.1", "localhost"):
        return ["127.0.0.1"]
    return [PUBLIC_IP]


@patch("core.url_guard.requests.request")
def test_safe_request_blocks_redirect_to_private(mock_request):
    public_resp = MagicMock()
    public_resp.is_redirect = True
    public_resp.is_permanent_redirect = False
    public_resp.headers = {"Location": "http://127.0.0.1/secret"}

    mock_request.return_value = public_resp

    with patch("core.url_guard._resolve_all_ips", side_effect=_resolve_by_host):
        with pytest.raises(BlockedURLError, match="non-public address"):
            safe_request("GET", "http://example.com/")


@patch("core.url_guard.requests.request")
def test_safe_request_follows_public_redirect(mock_request):
    redirect_resp = MagicMock()
    redirect_resp.is_redirect = True
    redirect_resp.is_permanent_redirect = False
    redirect_resp.headers = {"Location": "/next"}

    final_resp = MagicMock()
    final_resp.is_redirect = False
    final_resp.is_permanent_redirect = False

    mock_request.side_effect = [redirect_resp, final_resp]

    with _patch_resolve([PUBLIC_IP]):
        result = safe_request("GET", "http://example.com/")

    assert result is final_resp
    assert mock_request.call_count == 2


@patch("core.url_guard.requests.request")
def test_safe_request_enforces_max_redirects(mock_request):
    redirect_resp = MagicMock()
    redirect_resp.is_redirect = True
    redirect_resp.is_permanent_redirect = False
    redirect_resp.headers = {"Location": "/next"}

    mock_request.return_value = redirect_resp

    with _patch_resolve([PUBLIC_IP]):
        with pytest.raises(BlockedURLError, match="Too many redirects"):
            safe_request("GET", "http://example.com/", max_redirects=2)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
