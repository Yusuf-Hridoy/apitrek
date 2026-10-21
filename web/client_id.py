"""
Anonymous per-browser id for history privacy.

History is scoped to the browser that created it without any login: the
first response to a browser sets a long-lived random HttpOnly cookie
(``apitrek_sid``, a UUID) and every session row records it as ``client_id``.
Two visitors therefore never see each other's endpoints, and guessing a
session id is not enough to read someone else's session.
"""
import uuid
from typing import Optional

from fastapi import Request, Response

SID_COOKIE = "apitrek_sid"
SID_MAX_AGE_SECONDS = 60 * 60 * 24 * 365  # one year


def browser_id(request: Request) -> Optional[str]:
    """The visitor's id, or None when the browser has no cookie yet."""
    sid = request.cookies.get(SID_COOKIE)
    return sid or None


def ensure_browser_id(request: Request, response: Response) -> str:
    """Return the visitor's id, setting the cookie when this is first contact.

    A brand-new browser gets a fresh id and therefore an empty history list
    until it creates sessions of its own.
    """
    sid = browser_id(request)
    if sid:
        return sid
    sid = str(uuid.uuid4())
    response.set_cookie(
        key=SID_COOKIE,
        value=sid,
        max_age=SID_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
    )
    return sid
