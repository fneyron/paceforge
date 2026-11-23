"""Coaching › « Mon agenda »: Google Agenda and Outlook connected by OAuth (services/coaching_accounts). Dark until
configured (nothing changes, every route 404s, nothing calls out); the sign-in round trip against fake providers (the
state checked, PKCE sent, the tokens kept encrypted, never logged); the refresh, an expired grant read as « à
reconnecter »; only busy intervals reach the database; removing a source revokes what can be and deletes its tokens
and busy time; the export, stopping and account deletion; the follower sees nothing; the app's sign-in sheet."""
import base64
import hashlib
import io
import json
import logging
import zipfile
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text

from app.config import settings
from app.connectors import base
from app.models.coaching import CoachingBusy, CoachingCalendar, CoachingLink, CoachingPlan
from app.services import coaching_accounts as accounts
from app.services import coaching_agenda as ag
from app.services import coaching_plan as cp
from app.tasks import coaching as task
from tests.test_coaching import _as, _plan, beta, people  # noqa: F401
from tests.test_coaching_agenda import _everything, queued  # noqa: F401
from tests.test_coaching_goal import clock  # noqa: F401

SECRETS = ("Entretien confidentiel", "Dr Martin", "paul.durand@exemple.fr", "Salle Opéra", "Agenda perso de Stella",
           "stella@gmail.com")
TOKENS = ("g-at-", "g-rt-", "m-at-", "m-rt-", "the-code")
GRAPH = "https://graph.microsoft.com/v1.0/me/calendarView"


def _z(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


class Fake:
    """Google's and Microsoft's endpoints as PaceForge calls them; what each was asked is kept."""

    def __init__(self, monday: date):
        self.monday = monday
        self.calls: list[httpx.Request] = []
        self.tokens: list[dict] = []
        self.revoked: list[str] = []
        self.n = 0
        self.token_error: str | None = None
        self.refresh_error: str | None = None
        self.read_status = 200
        self.challenge: str | None = None  # what the authorize URL carried, checked against the verifier sent
        self.next_host = "graph.microsoft.com"

    def at(self, d: int, hh: int, mm: int = 0) -> datetime:
        return datetime.combine(self.monday + timedelta(days=d), time(hh, mm), timezone.utc)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        url = str(request.url).split("?")[0]
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()} \
            if request.method == "POST" and b"{" not in request.content[:1] else {}
        if url in ("https://oauth2.googleapis.com/token", accounts.MICROSOFT_URLS["token"]):
            google = "googleapis" in url
            self.tokens.append(form)
            assert form["client_id"] == ("g-client" if google else "m-client")
            assert form["client_secret"] == ("g-secret" if google else "m-secret")
            if form["grant_type"] == "authorization_code":
                assert form["code"] == "the-code"
                assert form["redirect_uri"].endswith(f"/coaching/agenda/{'google' if google else 'microsoft'}/callback")
                digest = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest())
                assert digest.rstrip(b"=").decode() == self.challenge, "PKCE: the verifier of the challenge"
                if self.token_error:
                    return httpx.Response(400, json={"error": self.token_error})
            elif self.refresh_error:
                return httpx.Response(400, json={"error": self.refresh_error, "error_description": "Token expired"})
            self.n += 1
            p = "g" if google else "m"
            body = {"access_token": f"{p}-at-{self.n}", "expires_in": 3599, "token_type": "Bearer"}
            if form["grant_type"] == "authorization_code" or not google:  # Google sends it at the consent only
                body["refresh_token"] = f"{p}-rt-{self.n}"
            return httpx.Response(200, json=body)
        if url == "https://oauth2.googleapis.com/revoke":
            self.revoked.append(form["token"])
            return httpx.Response(200)
        if url == "https://www.googleapis.com/calendar/v3/freeBusy":
            assert request.headers["authorization"].startswith("Bearer g-at-")
            if self.read_status != 200:
                return httpx.Response(self.read_status, json={"error": {"message": "Entretien confidentiel"}})
            ask = json.loads(request.content)
            assert ask["items"] == [{"id": "primary"}] and set(ask) == {"timeMin", "timeMax", "items"}
            return httpx.Response(200, json={"kind": "calendar#freeBusy", "timeMin": ask["timeMin"], "calendars": {
                "primary": {"busy": [
                    {"start": _z(self.at(1, 5)), "end": _z(self.at(1, 22))},
                    {"start": _z(self.at(3, 6)), "end": _z(self.at(3, 8, 30)), "summary": "Entretien confidentiel"},
                    {"start": _z(self.at(4, 0)), "end": _z(self.at(5, 0))},  # one whole day: ignored
                    {"start": _z(self.at(8, 0)), "end": _z(self.at(11, 0))},  # three whole days: away
                ], "location": "Salle Opéra", "summary": "Agenda perso de Stella"}}})
        if url == GRAPH:
            assert request.headers["authorization"].startswith("Bearer m-at-")
            assert request.headers["prefer"] == 'outlook.timezone="UTC"'
            if self.read_status != 200:
                return httpx.Response(self.read_status, json={"error": {"code": "ErrorAccessDenied"}})
            q = request.url.params
            if q.get("page") == "2":
                return httpx.Response(200, json={"value": [self.event(3, 17, 19, "busy")]})
            assert q["$select"] == "start,end,showAs,isAllDay,isCancelled" and q["$top"] == "100"
            assert "startDateTime" in q and "endDateTime" in q
            return httpx.Response(200, json={"value": [
                self.event(1, 5, 22, "busy"), self.event(2, 12, 13, "tentative"), self.event(2, 15, 16, "free"),
                self.event(2, 17, 18, "workingElsewhere"), self.event(3, 6, 8, "oof"),
                {**self.event(2, 7, 8, "busy"), "isCancelled": True},
                self.all_day(4, 1, "busy"), self.all_day(8, 3, "oof"), self.all_day(15, 3, "free"),
            ], "@odata.nextLink": f"https://{self.next_host}/v1.0/me/calendarView?page=2&$skiptoken=x"})
        return httpx.Response(404)

    def event(self, d: int, a: int, b: int, show_as: str) -> dict:
        """An event as Graph sends it: with a subject, a place and attendees PaceForge never asked for."""
        return {"id": f"AAMk{d}{a}", "@odata.etag": "W/1", "subject": "Entretien confidentiel",
                "location": {"displayName": "Salle Opéra"}, "attendees": [{"emailAddress": {
                    "address": "paul.durand@exemple.fr", "name": "Dr Martin"}}], "bodyPreview": "Dr Martin",
                "showAs": show_as, "isAllDay": False, "isCancelled": False,
                "start": {"dateTime": self.at(d, a).strftime("%Y-%m-%dT%H:%M:%S.0000000"), "timeZone": "UTC"},
                "end": {"dateTime": self.at(d, b).strftime("%Y-%m-%dT%H:%M:%S.0000000"), "timeZone": "UTC"}}

    def all_day(self, d: int, days: int, show_as: str) -> dict:
        e = self.event(d, 0, 0, show_as)
        e["end"]["dateTime"] = self.at(d + days, 0).strftime("%Y-%m-%dT%H:%M:%S.0000000")
        return {**e, "isAllDay": True}


@pytest.fixture
def on(monkeypatch):
    """Both connectors configured (an enabled flag, a client id and a secret each)."""
    for key, value in (("GOOGLE_CALENDAR_ENABLED", True), ("GOOGLE_CALENDAR_CLIENT_ID", "g-client"),
                       ("GOOGLE_CALENDAR_CLIENT_SECRET", "g-secret"), ("MICROSOFT_CALENDAR_ENABLED", True),
                       ("MICROSOFT_CALENDAR_CLIENT_ID", "m-client"), ("MICROSOFT_CALENDAR_CLIENT_SECRET", "m-secret"),
                       ("BASE_URL", "https://paceforge.example")):
        monkeypatch.setattr(settings, key, value)


@pytest.fixture
def fake(monkeypatch, clock):
    clock.day = cp.monday_of(clock.day)
    f = Fake(clock.day)
    monkeypatch.setattr(base, "_transport", httpx.MockTransport(f.handler))
    return f


@pytest.fixture
async def athlete(client, people, db_session, clock):
    """Stella, with a plan made on a Monday at 05:00 UTC."""
    clock.day = cp.monday_of(clock.day)
    await _plan(client, db_session, people.athlete, days=("1", "3", "4", "6"), long_day="6", base="240")
    return people.athlete


def _dawn(clock) -> datetime:
    return datetime.combine(clock.day, time(5), timezone.utc)


def _q(location: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}


async def _connect(web: AsyncClient, fake: Fake, provider: str, *, app: bool = False, **back) -> httpx.Response:
    """« Connecter … »: the consent page's address, then the provider's answer with the state it was given."""
    r = await web.get(f"/coaching/agenda/{provider}/connect" + ("?app=1" if app else ""))
    assert r.status_code == 302, r.text
    sent = _q(r.headers["location"])
    fake.challenge = sent["code_challenge"]
    params = {"state": sent["state"], **({"code": "the-code"} if "error" not in back else {}), **back}
    return await web.get(f"/coaching/agenda/{provider}/callback", params=params)


async def _source(db, user, provider) -> CoachingCalendar:
    return (await db.execute(select(CoachingCalendar).where(
        CoachingCalendar.user_id == user.id, CoachingCalendar.provider == provider)
        .execution_options(populate_existing=True))).scalar_one()


async def _busy(db, user) -> list[tuple]:
    rows = (await db.execute(select(CoachingBusy.start_at, CoachingBusy.end_at, CoachingBusy.away).where(
        CoachingBusy.user_id == user.id).order_by(CoachingBusy.start_at))).all()
    return [(a.replace(tzinfo=timezone.utc) if a.tzinfo is None else a,
             b.replace(tzinfo=timezone.utc) if b.tzinfo is None else b, w) for a, b, w in rows]


def _fold(page: str) -> str:
    return page.split('id="coach-tool-agenda"')[1].split('id="coach-tool-share"')[0]


# ── dark until configured ───────────────────────────────────────────────────

async def test_nothing_configured_nothing_changes_and_every_route_answers_404(client, athlete, db_session, monkeypatch):
    def never(request):
        raise AssertionError(f"a provider was called: {request.url}")
    monkeypatch.setattr(base, "_transport", httpx.MockTransport(never))
    before = (await client.get("/coaching?outil=agenda")).text
    fold = _fold(before)
    assert "coach-cal-url" in fold and "Où trouver l’adresse" in fold, "the address to paste, as before"
    for absent in ("data-agenda-connect", "Connecter Google", "Connecter Outlook", "Jamais les titres", "pf-coach-connect"):
        assert absent not in fold, absent
    assert "coaching-agenda.js" not in before
    privacy = (await client.get("/privacy")).text
    assert "Apple et Google ne reçoivent rien de PaceForge." in privacy and "Google Agenda" not in privacy
    assert "Microsoft" not in privacy and "Seulement si tu ajoutes une adresse dans «&nbsp;Mon agenda&nbsp;»&nbsp;: cette adresse (chiffrée), tes heures" in privacy
    # half configured is not configured: a flag without its secret, a secret without its flag
    monkeypatch.setattr(settings, "GOOGLE_CALENDAR_ENABLED", True)
    monkeypatch.setattr(settings, "GOOGLE_CALENDAR_CLIENT_ID", "g-client")
    monkeypatch.setattr(settings, "MICROSOFT_CALENDAR_CLIENT_ID", "m-client")
    monkeypatch.setattr(settings, "MICROSOFT_CALENDAR_CLIENT_SECRET", "m-secret")
    assert accounts.active() == []
    assert _fold((await client.get("/coaching?outil=agenda")).text) == fold, "the fold reads as before"
    assert (await client.get("/privacy")).text == privacy
    for provider in ("google", "microsoft", "apple"):
        for method, path in (("GET", f"/coaching/agenda/{provider}/connect"),
                             ("GET", f"/coaching/agenda/{provider}/callback?code=c&state=s"),
                             ("POST", f"/coaching/agenda/{provider}/app"), ("GET", f"/mobile/oauth/agenda/{provider}"),
                             ("POST", f"/mobile/oauth/agenda/{provider}"),
                             ("GET", f"/mobile/oauth/agenda/{provider}/confirm")):
            r = await client.request(method, path, data={"ticket": "x"} if method == "POST" else None)
            assert r.status_code in (404, 422), (method, path, r.status_code)
            assert r.status_code == 404 or provider == "apple", (method, path)


# ── Google: the round trip ──────────────────────────────────────────────────

async def test_google_sign_in_round_trip_state_pkce_encrypted_tokens_never_logged(
        client, athlete, db_session, clock, on, fake, queued, caplog):
    caplog.set_level(logging.DEBUG)
    page = (await client.get("/coaching?outil=agenda")).text
    fold = _fold(page)
    assert 'href="/coaching/agenda/google/connect"' in fold and 'href="/coaching/agenda/microsoft/connect"' in fold
    assert ">Connecter Google Agenda<" in fold and ">Connecter Outlook<" in fold
    assert "PaceForge lit seulement quand tu es occupé. Jamais les titres." in fold
    assert fold.index("Connecter Google Agenda") < fold.index("coach-cal-url"), "above the address"
    assert "coaching-agenda.js" in page
    async with AsyncClient(transport=client._transport, base_url="https://test") as web:
        r = await web.get("/coaching/agenda/google/connect")
        assert r.status_code == 302
        to = urlsplit(r.headers["location"])
        sent = _q(r.headers["location"])
        assert f"{to.scheme}://{to.netloc}{to.path}" == "https://accounts.google.com/o/oauth2/v2/auth"
        assert sent["scope"] == "https://www.googleapis.com/auth/calendar.freebusy", "the narrowest scope, alone"
        assert sent["redirect_uri"] == "https://paceforge.example/coaching/agenda/google/callback"
        assert sent["client_id"] == "g-client" and sent["response_type"] == "code"
        assert sent["access_type"] == "offline" and sent["prompt"] == "consent"
        assert sent["code_challenge_method"] == "S256" and len(sent["code_challenge"]) == 43
        assert "include_granted_scopes" not in sent
        # another state, or none: refused, nothing exchanged
        r = await web.get("/coaching/agenda/google/callback", params={"code": "the-code", "state": "forged"})
        assert r.headers["location"] == "/coaching?outil=agenda&connexion=google&resultat=expire#coach-tool-agenda"
        r = await web.get("/coaching/agenda/google/callback", params={"code": "the-code", "state": sent["state"]})
        assert "resultat=expire" in r.headers["location"], "the pending sign-in is used once"
        assert fake.tokens == []
        # the state of one provider is not the other's
        r = await web.get("/coaching/agenda/microsoft/connect")
        r = await web.get("/coaching/agenda/google/callback",
                          params={"code": "the-code", "state": _q(r.headers["location"])["state"]})
        assert "resultat=expire" in r.headers["location"] and fake.tokens == []
        r = await _connect(web, fake, "google")
    assert r.status_code == 303
    assert r.headers["location"] == "/coaching?outil=agenda&connexion=google&resultat=ok#coach-tool-agenda"
    assert fake.tokens[0]["grant_type"] == "authorization_code" and "code_verifier" in fake.tokens[0]
    row = await _source(db_session, athlete, "google")
    assert row.access_token == "g-at-1" and row.refresh_token == "g-rt-1" and row.url is None and row.status is None
    assert row.token_expires_at is not None
    raw = (await db_session.execute(text("SELECT access_token, refresh_token FROM coaching_calendars"))).one()
    assert all(v and not v.startswith(("g-at", "g-rt")) and "g-" not in v for v in raw), "encrypted at rest"
    assert queued == [athlete.id], "read by a worker"
    page = (await client.get(r.headers["location"].split("#")[0])).text
    fold = _fold(page)
    assert "Google Agenda connecté : lecture en cours." in fold and 'role="status"' in fold
    assert "<b>Google Agenda</b>" in fold and "Lecture en cours…" in fold
    assert "Connecter Google Agenda" not in fold and "Connecter Outlook" in fold, "one Google account at a time"
    logs = caplog.text
    for secret in (*TOKENS, sent["state"], "g-secret"):
        assert secret not in logs, f"« {secret} » was logged"


async def test_the_busy_time_is_read_replaced_and_nothing_but_intervals_is_stored(
        client, athlete, db_session, clock, on, fake, queued, caplog):
    caplog.set_level(logging.DEBUG)
    async with AsyncClient(transport=client._transport, base_url="https://test") as web:
        await _connect(web, fake, "google")
    assert await task.read_agenda(db_session, athlete.id, _dawn(clock)) == 1
    ask = json.loads([c for c in fake.calls if "freeBusy" in str(c.url)][0].content)
    assert ask["timeMin"] == _z(_dawn(clock)) and ask["timeMax"] == _z(_dawn(clock) + timedelta(days=21))
    at = fake.at
    assert await _busy(db_session, athlete) == [
        (at(1, 5), at(1, 22), False), (at(3, 6), at(3, 8, 30), False), (at(8, 0), at(11, 0), True)]
    row = await _source(db_session, athlete, "google")
    assert row.status == ag.OK and row.fetched_at is not None
    dump, logs = await _everything(db_session), caplog.text
    for secret in SECRETS:
        assert secret not in dump, f"« {secret} » was stored"
        assert secret not in logs, f"« {secret} » was logged"
    page = (await client.get("/coaching?outil=agenda")).text
    assert "<b>Google Agenda</b>" in _fold(page) and "Lu aujourd’hui" in _fold(page)
    assert "Entretien confidentiel" not in page
    # read again: the source's rows replaced, never added to
    assert await task.read_agenda(db_session, athlete.id, _dawn(clock)) == 1
    assert len(await _busy(db_session, athlete)) == 3


async def test_the_token_is_refreshed_and_an_expired_grant_is_to_reconnect(
        client, athlete, db_session, clock, on, fake, queued, caplog):
    caplog.set_level(logging.DEBUG)
    async with AsyncClient(transport=client._transport, base_url="https://test") as web:
        await _connect(web, fake, "google")
    assert await task.read_agenda(db_session, athlete.id, _dawn(clock)) == 1
    row = await _source(db_session, athlete, "google")
    row.token_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await db_session.flush()
    assert await task.read_agenda(db_session, athlete.id, _dawn(clock)) == 1
    refresh = fake.tokens[-1]
    assert refresh["grant_type"] == "refresh_token" and refresh["refresh_token"] == "g-rt-1"
    row = await _source(db_session, athlete, "google")
    assert row.access_token == "g-at-2" and row.refresh_token == "g-rt-1", "Google keeps its refresh token"
    # the grant expired (an app in « Testing »: 7 days) or was withdrawn: « à reconnecter », no error, no crash
    row.token_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await db_session.flush()
    fake.refresh_error = "invalid_grant"
    kept = await _busy(db_session, athlete)
    assert await task.read_agenda(db_session, athlete.id, _dawn(clock)) == 0
    row = await _source(db_session, athlete, "google")
    assert row.status == "reauth" and row.access_token is None and row.refresh_token is None
    assert await _busy(db_session, athlete) == kept, "the last good reading stands"
    r = await client.get("/coaching?outil=agenda")
    fold = _fold(r.text)
    assert r.status_code == 200 and "À reconnecter : l’accès a expiré ou a été retiré." in fold
    assert f'id="coach-cal-reconnect-{row.id}" href="/coaching/agenda/google/connect"' in fold
    # reconnecting renews the same source
    fake.refresh_error = None
    async with AsyncClient(transport=client._transport, base_url="https://test") as web:
        await _connect(web, fake, "google")
    row = await _source(db_session, athlete, "google")
    assert row.status is None and row.refresh_token == "g-rt-3"
    for secret in TOKENS:
        assert secret not in caplog.text


async def test_a_refused_reading_refreshes_once_then_is_to_reconnect(client, athlete, db_session, clock, on, fake,
                                                                     queued):
    async with AsyncClient(transport=client._transport, base_url="https://test") as web:
        await _connect(web, fake, "microsoft")
    fake.read_status = 403
    assert await task.read_agenda(db_session, athlete.id, _dawn(clock)) == 0
    assert [t["grant_type"] for t in fake.tokens] == ["authorization_code", "refresh_token"]
    assert len([c for c in fake.calls if str(c.url).startswith(GRAPH)]) == 2
    assert (await _source(db_session, athlete, "microsoft")).status == "reauth"
    fake.read_status = 503
    async with AsyncClient(transport=client._transport, base_url="https://test") as web:
        await _connect(web, fake, "microsoft")
    assert await task.read_agenda(db_session, athlete.id, _dawn(clock)) == 0
    assert (await _source(db_session, athlete, "microsoft")).status == "network"


# ── Microsoft ───────────────────────────────────────────────────────────────

async def test_outlook_reads_start_end_and_availability_only_paged_and_by_its_own_rules(
        client, athlete, db_session, clock, on, fake, queued, caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    async with AsyncClient(transport=client._transport, base_url="https://test") as web:
        r = await web.get("/coaching/agenda/microsoft/connect")
        sent = _q(r.headers["location"])
        assert r.headers["location"].startswith("https://login.microsoftonline.com/common/oauth2/v2.0/authorize?")
        assert sent["scope"] == "offline_access Calendars.ReadBasic" and sent["response_mode"] == "query"
        assert sent["code_challenge_method"] == "S256" and sent["prompt"] == "select_account"
        assert sent["redirect_uri"] == "https://paceforge.example/coaching/agenda/microsoft/callback"
        monkeypatch.setattr(settings, "MICROSOFT_CALENDAR_SCOPE", "calendars.read")
        assert _q((await web.get("/coaching/agenda/microsoft/connect")).headers["location"])["scope"] == \
            "offline_access Calendars.Read", "the documented fallback, without a release"
        monkeypatch.setattr(settings, "MICROSOFT_CALENDAR_SCOPE", "Calendars.ReadWrite")
        assert _q((await web.get("/coaching/agenda/microsoft/connect")).headers["location"])["scope"] == \
            "offline_access Calendars.ReadBasic", "never a wider permission"
        r = await _connect(web, fake, "microsoft")
    assert "connexion=microsoft&resultat=ok" in r.headers["location"]
    assert fake.tokens[0]["scope"] == "offline_access Calendars.ReadBasic"
    assert await task.read_agenda(db_session, athlete.id, _dawn(clock)) == 1
    at = fake.at
    # busy and oof are busy; free, tentative, workingElsewhere and a cancelled event are not; one all-day day is
    # ignored, three are « away » on the athlete's own days (UTC here)
    assert await _busy(db_session, athlete) == [
        (at(1, 5), at(1, 22), False), (at(3, 6), at(3, 8), False), (at(3, 17), at(3, 19), False),
        (at(8, 0), at(11, 0), True)]
    dump = await _everything(db_session)
    for secret in SECRETS:
        assert secret not in dump and secret not in caplog.text, secret
    # Microsoft rotates the refresh token: the new one kept
    row = await _source(db_session, athlete, "microsoft")
    row.token_expires_at = datetime.now(timezone.utc)
    await db_session.flush()
    await task.read_agenda(db_session, athlete.id, _dawn(clock))
    row = await _source(db_session, athlete, "microsoft")
    assert row.refresh_token == "m-rt-2" and fake.tokens[-1]["refresh_token"] == "m-rt-1"
    # a next page elsewhere than Graph is never followed
    fake.next_host = "evil.example"
    assert await task.read_agenda(db_session, athlete.id, _dawn(clock)) == 0
    assert (await _source(db_session, athlete, "microsoft")).status == "format"
    assert not any("evil.example" in str(c.url) for c in fake.calls)


async def test_a_refusal_comes_back_as_one_plain_line(client, athlete, db_session, clock, on, fake, queued):
    said = {}
    async with AsyncClient(transport=client._transport, base_url="https://test") as web:
        for key, back in (("org", {"error": "access_denied", "error_description":
                                   "AADSTS90094: The grant requires admin permission."}),
                          ("cancel", {"error": "access_denied", "error_description": "AADSTS65004: User declined"}),
                          ("other", {"error": "server_error"})):
            r = await _connect(web, fake, "microsoft", **back)
            said[key] = (await client.get(r.headers["location"].split("#")[0])).text
        fake.token_error = "invalid_grant"
        r = await _connect(web, fake, "google")
        said["token"] = (await client.get(r.headers["location"].split("#")[0])).text
    assert "Ton organisation n’autorise pas cette connexion : demande à ton administrateur, ou colle l’adresse de ton agenda." in _fold(said["org"])
    assert "Connexion annulée." in _fold(said["cancel"])
    assert "Outlook a refusé la connexion. Réessaie." in _fold(said["other"])
    assert "Google Agenda a refusé la connexion. Réessaie." in _fold(said["token"])
    for page in said.values():
        assert "AADSTS" not in page and "pf-coach-error" in _fold(page) and 'data-open="agenda"' in page and 'class="pf-coach-panel is-asked" id="coach-tool-agenda"' in page
    assert (await db_session.execute(select(func.count()).select_from(CoachingCalendar))).scalar() == 0
    assert queued == []
    # an unknown pair of codes says nothing
    assert "pf-coach-error" not in _fold((await client.get("/coaching?outil=agenda&connexion=google&resultat=x")).text)


# ── removing, stopping, export, deletion, the follower ──────────────────────

async def test_removing_a_source_revokes_and_deletes_its_tokens_and_busy_time(client, athlete, db_session, clock, on,
                                                                              fake, queued):
    async with AsyncClient(transport=client._transport, base_url="https://test") as web:
        await _connect(web, fake, "google")
        await _connect(web, fake, "microsoft")
    await task.read_agenda(db_session, athlete.id, _dawn(clock))
    google, microsoft = await _source(db_session, athlete, "google"), await _source(db_session, athlete, "microsoft")
    assert {b for b in (await db_session.execute(select(CoachingBusy.calendar_id))).scalars()} == {google.id, microsoft.id}
    assert (await client.post(f"/coaching/agenda/{google.id}/remove")).status_code == 303
    assert fake.revoked == ["g-rt-1"], "Google's grant withdrawn with its refresh token"
    assert await db_session.get(CoachingCalendar, google.id) is None
    assert {b for b in (await db_session.execute(select(CoachingBusy.calendar_id))).scalars()} == {microsoft.id}
    calls = len(fake.calls)
    assert (await client.post(f"/coaching/agenda/{microsoft.id}/remove")).status_code == 303
    assert len(fake.calls) == calls, "Microsoft has nothing to revoke: the tokens are deleted"
    assert (await db_session.execute(select(func.count()).select_from(CoachingCalendar))).scalar() == 0
    assert (await db_session.execute(select(func.count()).select_from(CoachingBusy))).scalar() == 0
    fold = _fold((await client.get("/coaching?outil=agenda")).text)
    assert "Connecter Google Agenda" in fold and "Connecter Outlook" in fold


async def test_export_stopping_deletion_and_the_follower(client, athlete, people, db_session, clock, on, fake, queued):
    coach = people.coach
    db_session.add(CoachingLink(athlete_id=athlete.id, coach_id=coach.id))
    async with AsyncClient(transport=client._transport, base_url="https://test") as web:
        await _connect(web, fake, "google")
    await task.read_agenda(db_session, athlete.id, _dawn(clock))

    async def export(user) -> dict:
        r = await _as(client, user).get("/settings/export")
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            return {name: z.read(name).decode() for name in z.namelist()}
    mine = await export(athlete)
    data = json.loads(mine["coaching.json"])
    assert data["agenda"] == [{"service": "Google Agenda", "ajoute_le": data["agenda"][0]["ajoute_le"],
                               "lu_le": data["agenda"][0]["lu_le"], "etat": "ok"}]
    assert len(data["heures_occupees"]) == 3
    assert not any(t in "".join(mine.values()) for t in TOKENS), "never a token in the export"
    # the follower: the times of the sessions, never the agenda, its buttons or the service
    theirs = (await _as(client, coach).get(f"/coaching/athletes/{athlete.id}")).text
    for absent in ("Google Agenda", "data-agenda-connect", "coach-tool-agenda", "coaching-agenda.js"):
        assert absent not in theirs, absent
    assert json.loads((await export(coach))["coaching.json"])["agenda"] == []
    assert (await client.get("/coaching/agenda/google/connect")).status_code == 404, "the follower has no plan here"
    # « Arrêter le coaching »: revoked, and everything goes
    assert (await _as(client, athlete).post("/coaching/stop")).status_code == 303
    assert fake.revoked == ["g-rt-1"]
    for model in (CoachingPlan, CoachingCalendar, CoachingBusy):
        assert (await db_session.execute(select(func.count()).select_from(model))).scalar() == 0


async def test_deleting_the_account_revokes_google(client, athlete, db_session, clock, on, fake, queued):
    async with AsyncClient(transport=client._transport, base_url="https://test") as web:
        await _connect(web, fake, "google")
    r = await client.post("/settings/delete-account", data={"confirmation": "SUPPRIMER"})
    assert r.status_code == 302
    assert fake.revoked == ["g-rt-1"], "its rows go with the account (ON DELETE CASCADE: test_account_deletion)"


async def test_withdrawing_the_health_consent_leaves_the_agenda(client, athlete, db_session, clock, on, fake, queued):
    async with AsyncClient(transport=client._transport, base_url="https://test") as web:
        await _connect(web, fake, "google")
    await client.post("/settings/health-consent/withdraw")
    assert fake.revoked == [] and (await _source(db_session, athlete, "google")).refresh_token == "g-rt-1"


# ── the app: the system's sign-in sheet ─────────────────────────────────────

async def test_the_app_signs_in_through_the_system_sheet_and_comes_back_to_the_agenda(
        client, athlete, db_session, clock, on, fake, queued):
    page = (await client.get("/coaching?outil=agenda")).text
    css = open("app/static/css/interface.css").read()
    js = open("app/static/js/coaching-agenda.js").read()
    assert ".pf-native:not(.pf-native-agenda) [data-agenda-connect]" in css, "an older app keeps the screen of before"
    assert "paceforgeNativeAgenda" in js and "/app" in js and 'data-agenda-connect="google"' in page
    r = await client.post("/coaching/agenda/google/app")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    link = r.json()["link"]
    assert link.startswith("https://paceforge.example/mobile/oauth/agenda/google#")
    ticket = link.split("#", 1)[1]
    async with AsyncClient(transport=client._transport, base_url="https://test") as sheet:
        first = (await sheet.get("/mobile/oauth/agenda/google")).text
        assert "location.hash" in first and ticket not in first
        assert "paceforge://agenda?connexion=google\\u0026resultat=expire" in first
        r = await sheet.post("/mobile/oauth/agenda/google", data={"ticket": ticket})
        assert r.status_code == 303 and r.headers["location"] == "/mobile/oauth/agenda/google/confirm"
        confirm = (await sheet.get(r.headers["location"])).text
        assert "Connecter Google Agenda" in confirm and "Stella" in confirm
        assert "Seules tes heures occupées dans Google Agenda, jamais les titres" in confirm
        assert 'href="/coaching/agenda/google/connect?app=1"' in confirm
        assert 'href="paceforge://agenda?connexion=google&amp;resultat=annule"' in confirm
        r = await _connect(sheet, fake, "google", app=True)
        assert r.headers["location"] == "paceforge://agenda?connexion=google&resultat=ok", "the sheet closes on it"
    assert (await _source(db_session, athlete, "google")).refresh_token == "g-rt-1"
    async with AsyncClient(transport=client._transport, base_url="https://test") as again:
        r = await again.post("/mobile/oauth/agenda/google", data={"ticket": ticket})
        assert r.headers["location"] == "paceforge://agenda?connexion=google&resultat=expire", "one use"
        other = (await client.post("/coaching/agenda/microsoft/app")).json()["link"].split("#", 1)[1]
        r = await again.post("/mobile/oauth/agenda/google", data={"ticket": other})
        assert r.headers["location"] == "paceforge://agenda?connexion=google&resultat=expire", "its provider only"
    # where the app lands: « Mon agenda » with its line
    landed = (await client.get("/coaching?outil=agenda&connexion=google&resultat=ok")).text
    assert "Google Agenda connecté : lecture en cours." in _fold(landed)


async def test_the_privacy_policy_names_google_and_microsoft_only_once_configured(client, people, on, monkeypatch):
    privacy = (await client.get("/privacy")).text
    assert "<strong>Google</strong> (Google LLC, États-Unis, hors UE), si tu connectes Google Agenda" in privacy
    assert "<strong>Microsoft</strong> (Microsoft Corporation, États-Unis, hors UE), si tu connectes Outlook" in privacy
    assert "Apple ne reçoit rien de PaceForge, ni Google en dehors de Google Agenda si tu le connectes." in privacy
    assert "ou connectes Google Agenda ou Outlook dans" in privacy
    monkeypatch.setattr(settings, "GOOGLE_CALENDAR_ENABLED", False)
    privacy = (await client.get("/privacy")).text
    assert "<strong>Google</strong> (Google LLC" not in privacy and "ou connectes Outlook dans" in privacy
    assert "Apple et Google ne reçoivent rien de PaceForge." in privacy
    monkeypatch.setattr(settings, "COACHING_BETA_USERS", "off")
    privacy = (await client.get("/privacy")).text
    assert "Microsoft" not in privacy, "Coaching off: no agenda, no recipient"


def test_the_reading_rules_alone():
    from zoneinfo import ZoneInfo

    paris = ZoneInfo("Europe/Paris")
    day = date(2026, 11, 23)
    mid = lambda d: datetime.combine(day + timedelta(days=d), time(0), paris).astimezone(timezone.utc)  # noqa: E731
    got = accounts.google_busy({"calendars": {"primary": {"busy": [
        {"start": _z(mid(0)), "end": _z(mid(1))}, {"start": _z(mid(2)), "end": _z(mid(4))},
        {"start": "2026-11-23T08:00:00Z", "end": "2026-11-23T09:00:00Z"}, {"start": "bad", "end": "x"}]}}}, paris)
    assert [(b.start, b.end, b.away) for b in got] == [
        (mid(2), mid(4), True), (datetime(2026, 11, 23, 8, tzinfo=timezone.utc),
                                 datetime(2026, 11, 23, 9, tzinfo=timezone.utc), False)]
    with pytest.raises(accounts.Failed):
        accounts.google_busy({"calendars": {"primary": {"errors": [{"reason": "notFound"}]}}}, paris)
    # an Outlook all-day event is laid on the athlete's own days, whatever zone shifted its midnights
    e = {"showAs": "oof", "isAllDay": True, "start": {"dateTime": "2026-11-22T23:00:00.0000000"},
         "end": {"dateTime": "2026-11-25T23:00:00.0000000"}}
    (b,) = accounts.outlook_busy([e], paris)
    assert (b.start, b.end, b.away) == (mid(0), mid(3), True)
    assert accounts.result_line("google", "ok") == ("Google Agenda connecté : lecture en cours.", True)
    assert accounts.result_line("apple", "ok") is None and accounts.result_line("google", "<b>") is None
    assert accounts.refusal("admin_policy_enforced", None) == "organisation"
