"""The form token of the pages a signed-in browser posts to (the phone link, the lactate tests): one per session,
compared in constant time. The session key keeps its first name, so the sessions open today still hold a valid
token."""
import secrets

from fastapi import HTTPException, Request

KEY = "mobile_csrf"


def csrf(request: Request) -> str:
    if KEY not in request.session:
        request.session[KEY] = secrets.token_urlsafe(32)
    return request.session[KEY]


def check_csrf(request: Request, token: str) -> None:
    if not secrets.compare_digest(request.session.get(KEY, ""), token) or not token:
        raise HTTPException(403, "Recharge la page puis réessaie.")
