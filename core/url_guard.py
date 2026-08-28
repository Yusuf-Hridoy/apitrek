"""
Outbound URL safety guard (SSRF protection).

Single touchpoint for every HTTP request made against a USER-SUPPLIED target.
Validates that the host resolves only to public IP addresses, blocks
loopback / private / link-local / reserved ranges, and re-validates every
redirect hop before following it.

All modules that fetch a user-controlled endpoint MUST use safe_request()
instead of calling requests.request() directly.
"""
import ipaddress
import socket
from typing import Any, Dict, Optional
from urllib.parse import urlparse, urljoin

import requests

DEFAULT_TIMEOUT_SECONDS = 10
MAX_REDIRECTS = 3

# Cloud metadata endpoints are link-local (169.254.0.0/16) and already blocked
# by is_link_local, but we keep an explicit note for reviewers.


class BlockedURLError(Exception):
    """Raised when a target URL is not safe to fetch (SSRF risk)."""
    pass


def _normalize_ip(ip_str: str) -> ipaddress._BaseAddress:
    ip = ipaddress.ip_address(ip_str)
    # Unwrap IPv4-mapped IPv6 (e.g. ::ffff:127.0.0.1) so it can't bypass checks.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    # Unwrap DNS64 / NAT64 Well-Known Prefix (64:ff9b::/96). These addresses
    # embed a real public IPv4 address and are returned by DNS64 resolvers on
    # IPv6-only networks; blocking them falsely blocks legitimate IPv4 sites.
    elif isinstance(ip, ipaddress.IPv6Address) and ip in ipaddress.ip_network("64:ff9b::/96"):
        ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return ip


def _is_ip_blocked(ip_str: str) -> bool:
    ip = _normalize_ip(ip_str)
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        return True
    # Carrier-grade NAT range (RFC 6598) — treat as non-public.
    if ip.version == 4 and ip in ipaddress.ip_network("100.64.0.0/10"):
        return True
    return False


def _resolve_all_ips(host: str, port: int) -> list[str]:
    """Resolve a host to every A/AAAA address. Fail closed on any error."""
    infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    return list({info[4][0] for info in infos})


def validate_public_url(url: str) -> None:
    """
    Raise BlockedURLError unless `url` is an http/https URL whose host
    resolves EXCLUSIVELY to public IP addresses.
    """
    if not url or not isinstance(url, str):
        raise BlockedURLError("Missing or invalid URL.")

    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise BlockedURLError("Only http:// and https:// URLs are allowed.")

    host = parsed.hostname
    if not host:
        raise BlockedURLError("URL has no host.")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    try:
        ips = _resolve_all_ips(host, port)
    except socket.gaierror:
        raise BlockedURLError(f"Could not resolve host: {host}")
    except Exception as e:  # fail closed
        raise BlockedURLError(f"Host resolution failed for {host}: {e}")

    if not ips:
        raise BlockedURLError(f"No addresses found for host: {host}")

    for ip in ips:
        if _is_ip_blocked(ip):
            raise BlockedURLError(
                f"Target resolves to a non-public address ({ip}); "
                "blocked to prevent SSRF."
            )


def safe_request(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    json: Optional[Any] = None,
    data: Optional[Any] = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    max_redirects: int = MAX_REDIRECTS,
) -> requests.Response:
    """
    SSRF-safe replacement for requests.request().

    Redirects are handled manually so that EACH hop is re-validated before it
    is followed. Automatic redirects are disabled at the requests layer.
    """
    current = url
    for _ in range(max_redirects + 1):
        validate_public_url(current)  # validate every hop, including the first
        resp = requests.request(
            method=method,
            url=current,
            headers=headers,
            json=json,
            data=data,
            timeout=timeout,
            allow_redirects=False,
        )
        if resp.is_redirect or resp.is_permanent_redirect:
            location = resp.headers.get("Location")
            if not location:
                return resp
            current = urljoin(current, location)
            continue
        return resp
    raise BlockedURLError("Too many redirects while following the target URL.")
