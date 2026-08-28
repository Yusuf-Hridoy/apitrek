"""Shared rate limiter. Keys on the real client IP (via X-Forwarded-For on Render)."""
import os

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request


def client_ip_key(request: Request) -> str:
    # Render (and most PaaS) sit behind a proxy, so request.client.host is the
    # proxy. The real client is the first entry in X-Forwarded-For.
    # NOTE: X-Forwarded-For is spoofable if the app is NOT behind a trusted
    # proxy. On Render it is set by the platform, so the leftmost value is used.
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return get_remote_address(request)


# Global fallback limits applied to every route unless overridden.
DEFAULT_LIMITS = os.getenv("RATE_LIMIT_DEFAULT", "60/hour;300/day").split(";")

limiter = Limiter(key_func=client_ip_key, default_limits=DEFAULT_LIMITS)

# Per-route limits (env-overridable). Format: "<n>/<period>;<n>/<period>".
LIMIT_GENERATE = os.getenv("RATE_LIMIT_GENERATE", "10/minute;100/day")
LIMIT_SECURITY_SCAN = os.getenv("RATE_LIMIT_SECURITY", "5/minute;40/day")
LIMIT_EXECUTE_SUITE = os.getenv("RATE_LIMIT_EXECUTE", "15/minute;200/day")
LIMIT_EXECUTE_SINGLE = os.getenv("RATE_LIMIT_EXECUTE_SINGLE", "30/minute;300/day")
