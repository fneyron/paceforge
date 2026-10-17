"""Where a COROS or Strava connection started from the phone app ends (owner, 2026-10-17: « tout dans
l'application »): the app's sign-in sheet (ASWebAuthenticationSession, Custom Tabs) closes on the app's scheme.
The outcome rides as a code, never as a message: any page can link to the scheme."""
from urllib.parse import urlencode

APP_RETURN = "paceforge://devices"
RESULTS = ("ok", "cancelled", "failed", "expired")


def app_return(provider: str) -> str:
    return f"{APP_RETURN}?{urlencode({'connection': provider})}"


def is_app_return(url: str | None) -> bool:
    return url is not None and url.startswith(APP_RETURN + "?")


def with_result(url: str, result: str) -> str:
    assert result in RESULTS, result
    return f"{url}&{urlencode({'result': result})}"
