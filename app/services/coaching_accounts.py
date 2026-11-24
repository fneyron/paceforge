"""Coaching › « Mon agenda »: Google Agenda and Outlook connected by OAuth, to read when the athlete is busy — and
nothing else (docs/coaching.md › « Mon agenda », docs/official-connectors.md › « Agendas »).

Dark until configured, as the official connectors: a provider is active only when its *_ENABLED flag is on and its
client id and secret are set (config.Settings.google_calendar_ready, microsoft_calendar_ready); until then its routes
answer 404, no page shows it and nothing calls it.

- the sign-in: OAuth 2.0 authorization code with PKCE (S256) and a state, both sealed in the signed session cookie
  for 10 minutes (app.connectors.base, as the official connectors do): a callback reached in another browser, or
  with another state, is refused. In the iPhone app the sign-in runs in the system's sign-in sheet (Google refuses
  embedded WebViews), signed in by the phone's one-time ticket (app.routers.mobile).
- what is asked: Google « calendar.freebusy » (« view your availability in your calendars »: busy intervals, no
  event); Microsoft « offline_access » and MICROSOFT_CALENDAR_SCOPE (Calendars.ReadBasic, or Calendars.Read where
  personal accounts refuse the first).
- what is read: Google's freebusy.query on the primary calendar — intervals only; Microsoft Graph's calendarView of
  the default calendar with $select=start,end,showAs,isAllDay,isCancelled — no subject, place, attendee nor body is
  asked for, and the answer is read for those five fields alone. What is kept is coaching_ical.Busy: a start, an end,
  « away » or not. No e-mail, no account id, no calendar name is stored.
- busy: Google says busy itself. Outlook: « busy » and « oof » (absent) are busy; « free », « tentative »,
  « workingElsewhere » and « unknown » are not (a tentative meeting is not a meeting yet; working elsewhere is working),
  a cancelled event neither. All-day, the existing rule (coaching_ical.AWAY_DAYS): 2 days or more is « away », a
  single day is ignored — Outlook says isAllDay; Google's intervals carry no such word, so an interval from one local
  midnight to another is read as all-day.
- the tokens: encrypted (EncryptedString), refreshed when they lapse; a refresh refused (invalid_grant: expired, as
  Google's 7 days for an app in « Testing », or withdrawn) is « reauth », never an error page. Never logged, nor
  a code: only the provider and an error code are.
- removing the source revokes what can be: Google's refresh token at its revoke endpoint; Microsoft has no
  revocation for one app's grant (the athlete withdraws it at account.microsoft.com), the tokens are deleted.
"""
import logging
from datetime import datetime, time, timedelta, timezone, tzinfo
from urllib.parse import urlencode, urlsplit

import httpx

from app.config import settings
from app.connectors import base
from app.services import coaching_ical as ical

logger = logging.getLogger(__name__)

PROVIDERS = ("google", "microsoft")
NAMES = {"google": "Google Agenda", "microsoft": "Outlook"}
CALLBACK = "/coaching/agenda/{provider}/callback"
GOOGLE_SCOPE = "https://www.googleapis.com/auth/calendar.freebusy"
MICROSOFT_SCOPES = ("Calendars.ReadBasic", "Calendars.Read")
GOOGLE_URLS = {
    "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
    "token": "https://oauth2.googleapis.com/token",
    "revoke": "https://oauth2.googleapis.com/revoke",
    "freebusy": "https://www.googleapis.com/calendar/v3/freeBusy",
}
MICROSOFT_URLS = {
    "authorize": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
    "token": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
    "calendar_view": "https://graph.microsoft.com/v1.0/me/calendarView",
}
BUSY_SHOW_AS = ("busy", "oof")
REFRESH_MARGIN = timedelta(minutes=5)
MAX_PAGES = 15  # Outlook: 15 pages of 100 events, the horizon's 21 days and then some
PAGE_SIZE = 100
TIMEOUT_S = 20
# a refresh refused for good: the athlete reconnects (a misconfigured client, invalid_client, is not their doing:
# the tokens are kept and the reading only fails)
GONE = ("invalid_grant", "interaction_required", "consent_required")
REVOKE_TIMEOUT_S = 8


class Failed(Exception):
    """A reading that did not go: `code` is the status the page says (coaching_agenda.STATUS)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def ready(provider: str) -> bool:
    if provider == "google":
        return settings.google_calendar_ready
    if provider == "microsoft":
        return settings.microsoft_calendar_ready
    return False


def active() -> list[str]:
    return [p for p in PROVIDERS if ready(p)]


def urls(provider: str) -> dict[str, str]:
    if provider == "google":
        return base.endpoints(GOOGLE_URLS, settings.GOOGLE_CALENDAR_URLS)
    return base.endpoints(MICROSOFT_URLS, settings.MICROSOFT_CALENDAR_URLS)


def redirect_uri(provider: str) -> str:
    return settings.BASE_URL.rstrip("/") + CALLBACK.format(provider=provider)


def microsoft_scope() -> str:
    """The delegated permission set by MICROSOFT_CALENDAR_SCOPE, one of the two read permissions; anything else (a
    typo, a wider one) reads as Calendars.ReadBasic."""
    wanted = (settings.MICROSOFT_CALENDAR_SCOPE or "").strip()
    for scope in MICROSOFT_SCOPES:
        if wanted.lower() == scope.lower():
            return scope
    if wanted:
        logger.error("MICROSOFT_CALENDAR_SCOPE %r is not a calendar read permission: Calendars.ReadBasic used", wanted)
    return MICROSOFT_SCOPES[0]


def scope(provider: str) -> str:
    return GOOGLE_SCOPE if provider == "google" else f"offline_access {microsoft_scope()}"


def _client(provider: str) -> tuple[str, str]:
    if provider == "google":
        return settings.GOOGLE_CALENDAR_CLIENT_ID.strip(), settings.GOOGLE_CALENDAR_CLIENT_SECRET.strip()
    return settings.MICROSOFT_CALENDAR_CLIENT_ID.strip(), settings.MICROSOFT_CALENDAR_CLIENT_SECRET.strip()


def authorize_url(provider: str, state: str, challenge: str) -> str:
    """The provider's consent page: the narrow scope, PKCE S256, the state; Google asked for a refresh token (offline,
    the consent shown each time so that a new one comes), Microsoft for the account to use."""
    client_id, _ = _client(provider)
    params = {"client_id": client_id, "response_type": "code", "redirect_uri": redirect_uri(provider),
              "scope": scope(provider), "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
    if provider == "google":
        params |= {"access_type": "offline", "prompt": "consent"}
    else:
        params |= {"response_mode": "query", "prompt": "select_account"}
    return urls(provider)["authorize"] + "?" + urlencode(params)


async def exchange(client: httpx.AsyncClient, provider: str, code: str, verifier: str) -> dict:
    """The code for tokens (base.TokenError with the OAuth error code otherwise)."""
    client_id, secret = _client(provider)
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(provider),
            "client_id": client_id, "client_secret": secret, "code_verifier": verifier}
    if provider == "microsoft":
        data["scope"] = scope(provider)
    return await base.token_request(client, urls(provider)["token"], data)


async def refresh(client: httpx.AsyncClient, provider: str, refresh_token: str) -> dict:
    client_id, secret = _client(provider)
    data = {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id,
            "client_secret": secret}
    if provider == "microsoft":
        data["scope"] = scope(provider)
    return await base.token_request(client, urls(provider)["token"], data)


def keep_tokens(row, tokens: dict, now: datetime | None = None) -> None:
    """A token answer onto the source (encrypted by the column): the access token, the refresh token when one came
    (Microsoft may rotate it; Google sends it at the consent only), when the access token lapses."""
    now = now or datetime.now(timezone.utc)
    row.access_token = str(tokens["access_token"])
    if tokens.get("refresh_token"):
        row.refresh_token = str(tokens["refresh_token"])
    try:
        expires_in = max(0, int(tokens.get("expires_in") or 0))
    except (TypeError, ValueError):
        expires_in = 0
    row.token_expires_at = now + timedelta(seconds=expires_in) if expires_in else None


def forget_tokens(row) -> None:
    row.access_token = row.refresh_token = row.token_expires_at = None


# ── reading ─────────────────────────────────────────────────────────────────

def _lapsed(row, now: datetime) -> bool:
    exp = base.as_utc(row.token_expires_at)
    return not row.access_token or (exp is not None and exp - now <= REFRESH_MARGIN)


async def _access(client: httpx.AsyncClient, row, force: bool = False) -> str:
    """A usable access token, refreshed when it lapses (or when the API refused the one kept). Failed(« reauth »)
    when the refresh is refused: the tokens are forgotten, the athlete reconnects. Read on the wall clock (the
    reading's `now` is the athlete's day)."""
    now = datetime.now(timezone.utc)
    if not force and not _lapsed(row, now):
        return row.access_token
    if not row.refresh_token:
        forget_tokens(row)
        raise Failed("reauth")
    try:
        tokens = await refresh(client, row.provider, row.refresh_token)
    except base.TokenError as e:
        if e.code in GONE:  # invalid_grant: expired (Google's 7 days in « Testing ») or withdrawn
            logger.warning("Coaching: %s calendar %d needs reconnecting (%s)", row.provider, row.id, e.code)
            forget_tokens(row)
            raise Failed("reauth") from None
        logger.warning("Coaching: %s calendar %d token refresh failed (%s)", row.provider, row.id, e.code)
        raise Failed("network") from None
    keep_tokens(row, tokens, now)
    return row.access_token


def _when(value) -> datetime | None:
    """An ISO time from the provider as UTC (Graph writes 7 decimals and no offset under outlook.timezone="UTC")."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    if "." in text:  # at most microseconds, whatever the provider writes
        head, _, tail = text.partition(".")
        digits = "".join(ch for ch in tail if ch.isdigit())
        text = head + "." + (digits[:6] or "0") + tail[len(digits):]
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)


def _whole_days(start: datetime, end: datetime, zone: tzinfo) -> int:
    """How many days an interval covers when it runs from one local midnight to another, else 0."""
    a, b = start.astimezone(zone), end.astimezone(zone)
    if a.time() != time(0) or b.time() != time(0) or b <= a:
        return 0
    return (b.date() - a.date()).days


def _local_days(day_a, day_b, zone: tzinfo) -> tuple[datetime, datetime]:
    """An all-day span laid on the athlete's own clock, midnight to midnight (as coaching_ical does)."""
    return (datetime.combine(day_a, time(0), zone).astimezone(timezone.utc),
            datetime.combine(day_b, time(0), zone).astimezone(timezone.utc))


def google_busy(answer, zone: tzinfo) -> list[ical.Busy]:
    """freebusy.query's answer: the primary calendar's busy intervals. Failed(« format ») when the calendar reports
    an error or the answer is not one."""
    cal = (answer.get("calendars") or {}).get("primary") if isinstance(answer, dict) else None
    if not isinstance(cal, dict) or cal.get("errors"):
        raise Failed("format")
    out = []
    for item in cal.get("busy") or ():
        if not isinstance(item, dict):
            continue
        a, b = _when(item.get("start")), _when(item.get("end"))
        if a is None or b is None or b <= a:
            continue
        days = _whole_days(a, b, zone)
        if days and days < ical.AWAY_DAYS:
            continue  # a single all-day block: ignored, as a one-day all-day event of an address
        out.append(ical.Busy(a, b, days >= ical.AWAY_DAYS))
    return out


def outlook_busy(events, zone: tzinfo) -> list[ical.Busy]:
    """calendarView's events read for start, end, showAs, isAllDay and isCancelled, and nothing else."""
    out = []
    for e in events:
        if not isinstance(e, dict) or e.get("isCancelled") or e.get("showAs") not in BUSY_SHOW_AS:
            continue
        a = _when((e.get("start") or {}).get("dateTime") if isinstance(e.get("start"), dict) else None)
        b = _when((e.get("end") or {}).get("dateTime") if isinstance(e.get("end"), dict) else None)
        if a is None or b is None or b <= a:
            continue
        if e.get("isAllDay"):
            # midnight to midnight: the dates are what counts (rounded, should a zone have shifted the midnights)
            day_a, day_b = (a + timedelta(hours=12)).date(), (b + timedelta(hours=12)).date()
            if (day_b - day_a).days < ical.AWAY_DAYS:
                continue
            a, b = _local_days(day_a, day_b, zone)
            out.append(ical.Busy(a, b, True))
        else:
            out.append(ical.Busy(a, b, False))
    return out


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _get_busy(client: httpx.AsyncClient, provider: str, token: str, lo: datetime, hi: datetime,
                    zone: tzinfo) -> list[ical.Busy]:
    """One reading of the horizon; Failed(« denied ») on a 401 or 403 (the token refused), other failures coded."""
    auth = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if provider == "google":
        r = await client.post(urls("google")["freebusy"], headers=auth,
                              json={"timeMin": _iso(lo), "timeMax": _iso(hi), "items": [{"id": "primary"}]})
        _check(r, provider)
        return google_busy(_json(r), zone)
    events: list = []
    url: str = urls("microsoft")["calendar_view"] + "?" + urlencode({
        "startDateTime": _iso(lo), "endDateTime": _iso(hi), "$select": "start,end,showAs,isAllDay,isCancelled",
        "$top": PAGE_SIZE})
    allowed = urlsplit(urls("microsoft")["calendar_view"]).hostname
    for _ in range(MAX_PAGES):
        r = await client.get(url, headers={**auth, "Prefer": 'outlook.timezone="UTC"'})
        _check(r, provider)
        body = _json(r)
        if not isinstance(body, dict) or not isinstance(body.get("value"), list):
            raise Failed("format")
        events.extend(body["value"])
        following = body.get("@odata.nextLink")
        if not following:
            break
        if not isinstance(following, str) or urlsplit(following).scheme != "https" \
                or urlsplit(following).hostname != allowed:
            raise Failed("format")  # a next page is followed on Graph's own host only
        url = following
    return outlook_busy(events, zone)


def _check(r: httpx.Response, provider: str) -> None:
    if r.status_code in (401, 403):
        raise Failed("denied")
    if r.status_code == 429:
        raise Failed("rate")
    if r.status_code != 200:
        logger.warning("Coaching: %s calendar answered %d", provider, r.status_code)
        raise Failed("network")


def _json(r: httpx.Response):
    try:
        return r.json()
    except ValueError:
        raise Failed("format") from None


async def read(row, zone: tzinfo, now: datetime, days: int = ical.HORIZON_DAYS) -> list[ical.Busy]:
    """The source's busy intervals for the next `days` days, merged, the access token refreshed on the way (written
    on `row`, encrypted). Failed(code) when they cannot be read."""
    if not ready(row.provider):
        raise Failed("off")
    lo = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    hi = lo + timedelta(days=days)
    try:
        async with base.http_client(timeout=TIMEOUT_S) as client:
            token = await _access(client, row)
            try:
                busy = await _get_busy(client, row.provider, token, lo, hi, zone)
            except Failed as e:
                if e.code != "denied":
                    raise
                # the token kept was refused: refreshed once, then read again; refused again, the access is gone
                busy = await _get_busy(client, row.provider, await _access(client, row, force=True), lo, hi, zone)
    except Failed as e:
        if e.code == "denied":
            logger.warning("Coaching: %s calendar %d refused the reading (%s)", row.provider, row.id,
                           "check MICROSOFT_CALENDAR_SCOPE" if row.provider == "microsoft" else "scope")
            raise Failed("reauth") from None
        raise
    except httpx.TimeoutException:
        raise Failed("timeout") from None
    except httpx.HTTPError:
        raise Failed("network") from None
    clipped = [ical.Busy(max(b.start, lo), min(b.end, hi), b.away) for b in busy if b.end > lo and b.start < hi]
    return ical.merge(clipped)[:ical.MAX_INTERVALS]


async def revoke(row) -> bool:
    """What the provider lets PaceForge withdraw, best effort and time-bounded: Google's grant (its refresh token, else
    the access token). Microsoft has nothing to call. True when the provider said yes."""
    token = row.refresh_token or row.access_token
    if row.provider != "google" or not token:
        return False
    try:
        async with base.http_client(timeout=REVOKE_TIMEOUT_S) as client:
            r = await client.post(urls("google")["revoke"], data={"token": token},
                                  headers={"Content-Type": "application/x-www-form-urlencoded"})
    except httpx.HTTPError as e:
        logger.warning("Coaching: Google calendar %d not revoked (%s)", row.id, type(e).__name__)
        return False
    if r.status_code != 200:  # 400: already revoked or expired
        logger.info("Coaching: Google calendar %d revoke answered %d", row.id, r.status_code)
    return r.status_code == 200


async def let_go(row) -> None:
    """revoke, for the account deletion's steps (their outcome is not read)."""
    await revoke(row)


# ── the outcome of a sign-in, as one line on « Mon agenda » ─────────────────
# Carried as two codes in the address (/coaching?outil=agenda&connexion=google&resultat=ok, or paceforge://agenda?…
# from the app's sign-in sheet): never a message, any page can link to them.

APP_RETURN = "paceforge://agenda"
RESULTS = ("ok", "annule", "expire", "refus", "organisation", "panne")
ORGANISATION = "Ton organisation n’autorise pas cette connexion : demande à ton administrateur."


def result_line(provider: str | None, result: str | None) -> tuple[str, bool] | None:
    """(the sentence, whether it went well) for a known pair of codes, else None."""
    name = NAMES.get(provider or "")
    if name is None or result not in RESULTS:
        return None
    return {"ok": (f"{name} connecté : lecture en cours.", True),
            "annule": ("Connexion annulée.", False),
            "expire": ("La connexion a expiré. Réessaie.", False),
            "refus": (f"{name} a refusé la connexion. Réessaie.", False),
            "organisation": (ORGANISATION, False),
            "panne": (f"{name} ne répond pas pour l’instant. Réessaie dans quelques minutes.", False)}[result]


def back_to(provider: str, result: str, app: bool) -> str:
    """Where a sign-in ends: « Mon agenda » with its line, or the app's sign-in sheet, which closes on its scheme."""
    assert result in RESULTS, result
    query = urlencode({"connexion": provider, "resultat": result})
    return f"{APP_RETURN}?{query}" if app else f"/coaching?outil=agenda&{query}#coach-tool-agenda"


def refusal(error: str, description: str | None) -> str:
    """The provider's refusal on the callback as a result code: cancelled, an organisation that does not allow it
    (Microsoft's « admin approval required », Google Workspace's admin_policy_enforced), or refused."""
    text = (description or "").lower()
    if (error in ("admin_policy_enforced", "consent_required")
            or any(mark in text for mark in ("aadsts90094", "aadsts90095", "aadsts65001", "admin"))):
        return "organisation"
    return "annule" if error == "access_denied" else "refus"
