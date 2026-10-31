"""The consent to health data before a Garmin login starts (GDPR art. 9.2.a), around the Garmin router as main.py
includes it: Garmin's own files stay as they are.

Only POST /garmin/connect starts a login (the MFA code and the polling follow one already started); without the
consent it goes to the consent page instead, then back to where the form was (Réglages › Garmin, or the connection
page the phone app opens). Garmin itself is never asked, the password never read.
"""
from fastapi import Depends, HTTPException, Request

from app.dependencies import get_current_user
from app.models.user import User
from app.services import health_consent

CONNECT = "/garmin/connect"


async def consent_before_connect(request: Request, user: User = Depends(get_current_user)) -> None:
    if request.method != "POST" or request.url.path != CONNECT or health_consent.given(user):
        return
    form = await request.form()  # parsed once: the route reads the same form
    back = "/connections/garmin" if form.get("next") == "devices" else "/settings#garmin"
    url = health_consent.consent_url(back)
    if request.headers.get("HX-Request"):  # the form swaps a partial: a full navigation instead
        raise HTTPException(status_code=204, headers={"HX-Redirect": url})
    raise HTTPException(status_code=303, headers={"Location": url})
