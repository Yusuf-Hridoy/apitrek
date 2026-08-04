"""
Live API response fetcher module.
Makes real HTTP requests and returns parsed JSON responses safely.
"""

import urllib.parse
from typing import Any, Dict, Optional

import requests


class LiveFetchError(Exception):
    """Raised when live API fetching fails."""
    pass


MAX_RESPONSE_SIZE_BYTES = 1024 * 1024  # 1 MB
REQUEST_TIMEOUT_SECONDS = 10


def _is_valid_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def fetch_api_response(
    endpoint: str,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    request_body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Send a real HTTP request and return the parsed JSON response body.

    Args:
        endpoint: Full URL to fetch.
        method: HTTP method (GET, POST, etc.).
        headers: Optional request headers.
        request_body: Optional JSON body for POST/PUT/PATCH.

    Returns:
        Parsed JSON response as a Python dict.

    Raises:
        LiveFetchError: On any failure with a human-readable message.
    """
    if not endpoint or not isinstance(endpoint, str):
        raise LiveFetchError("Invalid or missing endpoint URL.")

    if not _is_valid_url(endpoint):
        raise LiveFetchError(
            "Invalid URL format. Please provide a valid http:// or https:// URL."
        )

    method = method.upper().strip()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
        raise LiveFetchError(f"Unsupported HTTP method: {method}")

    request_headers: Dict[str, str] = {}
    if headers:
        request_headers.update(headers)
    if method in ("POST", "PUT", "PATCH") and request_body is not None:
        request_headers.setdefault("Content-Type", "application/json")

    try:
        req_kwargs: Dict[str, Any] = {
            "url": endpoint,
            "method": method,
            "headers": request_headers,
            "timeout": REQUEST_TIMEOUT_SECONDS,
            "allow_redirects": True,
        }

        if method in ("POST", "PUT", "PATCH") and request_body is not None:
            req_kwargs["json"] = request_body

        response = requests.request(**req_kwargs)
    except requests.exceptions.Timeout:
        raise LiveFetchError(
            f"Request timed out after {REQUEST_TIMEOUT_SECONDS} seconds."
        )
    except requests.exceptions.ConnectionError:
        raise LiveFetchError(
            "Could not connect to the API. Please check the URL and try again."
        )
    except requests.exceptions.TooManyRedirects:
        raise LiveFetchError("Too many redirects. The URL may be misconfigured.")
    except requests.exceptions.RequestException as e:
        raise LiveFetchError(f"Request failed: {e}")

    # Check response size using Content-Length header if available
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            if int(content_length) > MAX_RESPONSE_SIZE_BYTES:
                raise LiveFetchError(
                    f"Response too large ({int(content_length) / 1024:.0f} KB). "
                    f"Maximum allowed is {MAX_RESPONSE_SIZE_BYTES // 1024} KB."
                )
        except ValueError:
            pass  # Ignore malformed Content-Length

    # Rough size check on actual body
    if len(response.content) > MAX_RESPONSE_SIZE_BYTES:
        raise LiveFetchError(
            f"Response too large ({len(response.content) / 1024:.0f} KB). "
            f"Maximum allowed is {MAX_RESPONSE_SIZE_BYTES // 1024} KB."
        )

    content_type = response.headers.get("Content-Type", "")
    if "application/json" not in content_type and response.text.strip():
        # Some APIs return JSON with a non-standard Content-Type, so still try
        # parsing, but reject obvious HTML early.
        if response.text.strip().startswith("<"):
            raise LiveFetchError(
                f"The API returned HTML instead of JSON (status {response.status_code}). "
                "Please check the endpoint URL."
            )

    if response.status_code >= 500:
        raise LiveFetchError(
            f"The API returned a server error (status {response.status_code}). "
            "The endpoint may be temporarily unavailable."
        )
    elif response.status_code == 401:
        raise LiveFetchError(
            "The API returned 401 Unauthorized. Authentication may be required."
        )
    elif response.status_code == 403:
        raise LiveFetchError(
            "The API returned 403 Forbidden. You may not have permission to access this endpoint."
        )
    elif response.status_code == 404:
        raise LiveFetchError(
            "The API returned 404 Not Found. Please check the endpoint URL."
        )
    elif response.status_code >= 400:
        # For 400, 422, etc. — still attempt to parse JSON since many APIs return
        # structured error JSON. We only raise if parsing fails below.
        pass

    if not response.text or not response.text.strip():
        raise LiveFetchError("The API returned an empty response.")

    try:
        parsed = response.json()
    except (requests.exceptions.JSONDecodeError, ValueError):
        raise LiveFetchError(
            f"The API response is not valid JSON "
            f"(status {response.status_code}, content-type: {content_type or 'unknown'}). "
            "Please paste the sample response manually."
        )

    if not isinstance(parsed, (dict, list)):
        raise LiveFetchError(
            "The API returned JSON, but it is not a JSON object or array."
        )

    return parsed
