"""Santé v4 (SANTE_V4_SPEC.md): one page, WHOOP/Oura-like, from past
activities and the nights only. The routes (login, not connected, no data
yet, a failure, the old views' links, the old range swap, the check-in POST
kept harmlessly, the opening sync), Santé first in both navs and as the
signed-in home, the owner's 8 Oct 2026 as a fixture (his real COROS nights,
the Transjeju 100M as a plain activity: 02/10 21:00 in Korea, 16h53), a rich
Garmin wearer, an empty user, and what must never come back: tabs, the
check-in, a race chip or flag, « séance », the filler sentences."""

import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from html import unescape
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.health import HealthMetric
from app.models.route import Route
from app.models.user import User
from app.services import nights as nt
from app.services import sante
from tests import test_coros
from tests.owner_v4 import D8, seed_owner_v4
from tests.test_coros import _link
from tests.test_nights import night_rows

# the COROS test fixtures (a linked athlete, commits turned into flushes)
as_user, no_commit = test_coros.as_user, test_coros.no_commit
ROOT = Path(__file__).resolve().parent.parent
BRAND = ("Récup COROS", "calculée par COROS", "Training Readiness", "Body Battery", "Score de sommeil", "×1,",
         "forte hausse", "84 %", "VO2", "Stress de jour", "fatigue %", "Fraîcheur")
NEVER = ("séance", "pas jugée", "pas assez de nuits", "autour de la course", "Ce matin",
         "Comment je vais", "⚑", "J+", "J‑", "Jour de course", "Footing facile", "intensité", "Reprise",
         "Forme du jour", "Cœur la nuit",
         # v4.1: no sync status (Réglages'), no Charge card (Activités'), no unlabelled tick on the Charge ring
         "synchro", "Synchroniser", "Dernière synchro", "pas encore reçue", "Charge · 14 jours", 'id="charge"',
         "pf-ring-tick", "pf-sync-btn",
         # v4.4 (owner: « Sommeil 100, Charge récente 20 avec les donuts au-dessus, on comprend rien »): one ring, no
         # sub-score, no « Détail du score »
         "Détail du score", "Pas encore dans le score", "Charge récente", "pf-contrib", "pf-ring-sommeil",
         "pf-ring-charge", 'class="pf-rings"')


def _add(db: AsyncSession, user: User, metric: str, d: date, value: float, details=None, source="COROS"):
    db.add(HealthMetric(user_id=user.id, date=d, metric=metric, value=value, source=source, details=details,
                        n_samples=1))


def _main(html: str) -> str:
    """The page's own content (the top bar and the tab bar left out)."""
    return html.split('id="main-content"', 1)[1].split("</main>", 1)[0]


def _visible(html: str) -> str:
    """The values a reader sees before opening anything: no script, no closed fold, no chart axis (the SVGs'
    tick labels are a scale, not a value), no tag, no aria attribute."""
    body = re.sub(r"<script.*?</script>", " ", _main(html), flags=re.S)
    body = re.sub(r"<details.*?</details>", " ", body, flags=re.S)
    body = re.sub(r"<svg.*?</svg>", " ", body, flags=re.S)
    return re.sub(r"<[^>]+>", " ", body)


@pytest.fixture
def on_owner_day(monkeypatch):
    """The page's « today » is 8 Oct 2026 (the athlete's date in Korea)."""
    async def today(*a, **k):
        return D8
    monkeypatch.setattr(sante, "athlete_today", today)


# ── routes ──────────────────────────────────────────────────────────────────

async def test_sante_requires_login(client: AsyncClient):
    r = await client.get("/sante")
    assert r.status_code == 307 and r.headers["location"] == "/"
    r = await client.get("/sante/sommeil?r=90")
    assert r.status_code == 307


async def test_not_connected(as_user: AsyncClient):
    page = (await as_user.get("/sante")).text
    assert "Connecter COROS" in page and 'href="/settings#coros"' in page
    assert "/coros/connect" not in page and "Synchroniser maintenant" not in page  # connecting happens in Réglages
    assert 'role="tablist"' not in page and "séance" not in page.lower()


async def test_connected_without_data_yet(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    await _link(db_session, test_user)
    page = (await as_user.get("/sante")).text
    assert "Pas encore de données de COROS" in page and "Connecter COROS" not in page
    assert "Synchroniser maintenant" not in page and "/sante/sync\"" not in page  # Réglages' (owner, 2026-10-08)


async def test_a_failure_is_not_shown_as_not_connected(as_user: AsyncClient, db_session: AsyncSession,
                                                       test_user: User, monkeypatch):
    await _link(db_session, test_user)

    async def boom(*a, **k):
        raise ValueError("bad row")
    monkeypatch.setattr("app.routers.sante.health_page", boom)
    page = (await as_user.get("/sante")).text
    assert "Impossible d'afficher tes données" in page and "Connecter COROS" not in page


async def test_the_old_views_land_on_the_one_page(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    """?vue=… (Aujourd'hui, Sommeil, Entraînement, Course, Tendances) and the old range swap land on /sante, at
    the section that holds what is left of them; the sleep range (14 or 90) is kept. No race is read for it."""
    db_session.add(Route(user_id=test_user.id, name="Trail des Glaciers", total_distance_km=42,
                         race_date=(date.today() + timedelta(days=5)).isoformat(), start_hour=5, start_minute=0))
    await db_session.flush()
    for url, where in (("/sante?vue=sommeil", "/sante#sommeil"), ("/sante?vue=sommeil&r=90", "/sante?r=90#sommeil"),
                       ("/sante?vue=entrainement", "/sante"), ("/sante?vue=course", "/sante"),
                       ("/sante?vue=tendances", "/sante"), ("/sante?vue=aujourdhui", "/sante"),
                       ("/sante?vue=sommeil&r=365", "/sante#sommeil")):
        r = await as_user.get(url)
        assert r.status_code == 302 and r.headers["location"] == where, url
    r = await as_user.get("/sante/sommeil?r=90")
    assert r.status_code == 303 and r.headers["location"] == "/sante?r=90#sommeil"
    # an htmx call (a v3 page left open): a full navigation, never the whole page pasted inside the old one (REG-1)
    r = await as_user.get("/sante/sommeil?r=90", headers={"HX-Request": "true"})
    assert r.status_code == 204 and r.headers["HX-Redirect"] == "/sante?r=90#sommeil" and not r.text


async def test_the_check_in_post_is_kept_harmlessly(as_user: AsyncClient, db_session: AsyncSession,
                                                    test_user: User, on_owner_day):
    """No check-in on the page; an old form still posting gets its answer stored and the page back, and the
    recovery never reads it (no « malade » trigger, no Ressenti)."""
    await seed_owner_v4(db_session, test_user)
    before = (await sante.health_page(db_session, test_user.id, today=D8))["score"]["value"]
    r = await as_user.post("/sante/feel", data={"feel": "3", "toggle": "sick"}, headers={"HX-Request": "true"})
    assert r.status_code == 204 and r.headers["HX-Redirect"] == "/sante"  # REG-1: htmx navigates, never nests
    assert (await as_user.post("/sante/feel", data={"feel": "3"})).status_code == 303  # a plain form: a redirect
    row = (await db_session.execute(select(HealthMetric).where(HealthMetric.metric == "feel"))).scalar_one()
    assert row.value == 3 and row.details["why"] == ["sick"]
    page = await sante.health_page(db_session, test_user.id, today=D8)
    assert page["score"]["value"] == before and page["state"]["key"] == "effort"
    html = (await as_user.get("/sante")).text
    assert "/sante/feel" not in html and "Ce matin" not in html and "Malade" not in html


# ── Santé first ─────────────────────────────────────────────────────────────

async def test_sante_is_first_in_both_navs(as_user: AsyncClient):
    page = (await as_user.get("/activities")).text
    top = page.split('class="pf-nav"')[1].split("</nav>")[0]
    tabbar = page.split('class="pf-tabbar"')[1].split("</nav>")[0]
    for nav in (top, tabbar):
        assert re.findall(r'href="(/[a-z]+)"', nav) == ["/sante", "/simulator", "/activities", "/settings"]
    assert '<a href="/sante" class="pf-logo"' in page  # the logo goes home
    page = (await as_user.get("/sante")).text
    assert page.count('href="/sante" aria-current="page"') == 2


async def test_the_signed_in_home_is_sante(client: AsyncClient, db_session: AsyncSession, no_commit):
    from app.services.auth import hash_password

    user = User(email="me@x.fr", password_hash=hash_password("pw-12345678"), email_verified=True,
                email_verify_token="tok-1")
    db_session.add(user)
    await db_session.flush()
    async with AsyncClient(transport=client._transport, base_url="https://test") as c:
        assert (await c.get("/")).status_code == 200  # signed out: the public home, unchanged
        r = await c.post("/auth/login", data={"email": "me@x.fr", "password": "pw-12345678"})
        assert r.status_code == 302 and r.headers["location"] == "/sante"
        r = await c.get("/")
        assert r.status_code == 302 and r.headers["location"] == "/sante"
        r = await c.get("/auth/verify-email?token=tok-1")
        assert r.status_code == 302 and r.headers["location"] == "/sante"
        user.password_reset_token = "reset-1"
        user.password_reset_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        await db_session.flush()
        r = await c.post("/auth/reset-password", data={"token": "reset-1", "password": "pw-87654321"})
        assert r.status_code == 302 and r.headers["location"] == "/sante"


async def test_a_strava_link_lands_on_sante_unless_it_said_where(client: AsyncClient, db_session: AsyncSession,
                                                                 no_commit, monkeypatch):
    from unittest.mock import AsyncMock

    from app.crypto import encrypt_secret
    from app.services.auth import hash_password
    from app.services.strava import StravaService

    monkeypatch.setattr(StravaService, "exchange_token", AsyncMock(return_value={
        "access_token": "at", "refresh_token": "rt", "expires_at": 9999999999,
        "athlete": {"id": 778, "firstname": "A", "lastname": "B", "profile": None}}))
    monkeypatch.setattr(StravaService, "create_webhook_subscription", AsyncMock(return_value=None))
    monkeypatch.setattr("app.tasks.initial_sync.initial_sync.delay", lambda uid: None)
    user = User(email="me@x.fr", password_hash=hash_password("pw-12345678"), email_verified=True,
                strava_client_id="4242", strava_client_secret_encrypted=encrypt_secret("s3cret"))
    db_session.add(user)
    await db_session.flush()
    async with AsyncClient(transport=client._transport, base_url="https://test") as c:
        await c.post("/auth/login", data={"email": "me@x.fr", "password": "pw-12345678"})
        await c.get("/auth/strava")
        r = await c.get("/auth/strava/callback?code=abc")
        assert r.status_code == 302 and r.headers["location"] == "/sante"
        await c.get("/auth/strava?next=settings")
        r = await c.get("/auth/strava/callback?code=abc")
        assert r.headers["location"] == "/settings#strava"  # an explicit « next » is kept


async def test_the_installed_app_opens_on_sante(as_user: AsyncClient, client: AsyncClient):
    """The phone app (the PWA manifest) starts on /sante; an app installed with the old start_url (/dashboard)
    lands there too; the error pages' button goes home, not to the races."""
    manifest = json.loads((ROOT / "app/static/manifest.json").read_text(encoding="utf-8"))
    assert manifest["start_url"] == "/sante"
    r = await as_user.get("/dashboard")
    assert r.status_code == 302 and r.headers["location"] == "/sante"
    for f in ("404.html", "500.html"):
        html = (ROOT / "app/templates" / f).read_text(encoding="utf-8")
        assert "Retour à mes courses" not in html and '<a href="/sante" class="pf-btn' in html, f
    r = await as_user.get("/nulle-part")
    assert r.status_code == 404 and "Retour à l'accueil" in r.text


# ── the owner, 8 Oct 2026 ───────────────────────────────────────────────────

async def test_owner_8_october_state_and_score(db_session: AsyncSession, test_user: User):
    """The Transjeju (an activity: 16h53 stops included, ended 03/10 13:53 in Korea) is an ultra that ran
    through the night (01:00 → 05:00 local, H): the raw score capped at 40 on D+1 → D+3, then at 65 until D+13
    (16/10; v4.2); 08/10 is D+5 from its end, 6 days after it started. No band yet (no night of 7 usable), so
    Sommeil (8h36 → 100, weight 30) and Charge récente (ultra → 20, weight 20: a window is open) make the score:
    raw = (30 × 100 + 20 × 20) / 50 = 68; its window's 65 binds (69 for Charge's 20; 80 without VFC nor FC): 65,
    « Récupération en cours ». v4.3: the page says that word only, no activity named (owner: « mets juste les
    scores »); his Les Houches days of 18/09 and 19/09 have no window left (each ended on D+5: 23/09, 24/09)."""
    await seed_owner_v4(db_session, test_user)
    page = await sante.health_page(db_session, test_user.id, today=D8)
    st, s = page["state"], page["score"]
    assert (st["key"], st["tone"], st["word"], st["text"]) == ("effort", "warn", "Récupération en cours", None)
    assert "href" not in st and not st["estimated"] and st["aria"] == "Récupération en cours."
    raw = (30 * 100 + 20 * 20) / 50
    assert s["raw0"] == raw == 68 and s["value"] == 65 and s["tone"] == "warn" and s["reason"] == "effort"
    assert {p["key"]: round(p["sub"]) for p in s["parts"]} == {"sleep": 100, "load": 20}
    assert s["absent"] == ["hrv", "hr"] and s["caps"] == ["effort"]  # the window's 65 binds
    load = next(p for p in s["parts"] if p["key"] == "load")
    assert load["window"]["until"] == date(2026, 10, 16)  # D+13: it ran through the night (01:00 → 05:00, H)


async def test_owner_nights_tags_and_no_band_yet(db_session: AsyncSession, test_user: User):
    """06/10 and 07/10 are D+3 and D+4 after the ultra: « après ultra » (D+1 → D+4, v4.2), out of the bands, of the
    illness alert and of the bedtime medians. 29/09 → 01/10 are his first nights in Korea, 7 zones east of Les
    Houches (his activities of 18-19/09 at UTC+2, his nights at UTC+9): « décalage horaire », 7 nights from the
    first one (Janse van Rensburg 2021). A band needs 7 nights: none yet. 16h53 is under 20 h: nothing more after
    D+4."""
    await seed_owner_v4(db_session, test_user)
    from app.services import sante_training as st

    sessions = await st.load_sessions(db_session, test_user.id, D8)
    nights = await nt.load_nights(db_session, test_user.id, D8, sessions=sessions, efforts=st.efforts(sessions))
    owned = {d: sorted(n.tags) for d, n in nights.items() if n.asleep is not None}
    assert owned == {date(2026, 9, 29): ["jetlag"], date(2026, 9, 30): ["jetlag"], date(2026, 10, 1): ["jetlag"],
                     date(2026, 10, 6): ["ultra"], date(2026, 10, 7): ["ultra"], D8: []}
    assert (nights[D8].asleep, nights[D8].tst24, nights[D8].hrv, nights[D8].hr) == (516, 516, 99.7, 35.0)
    # his real nights before the race: PaceForge's own VFC from COROS's raw series (COROS says 90, 83, 80)
    assert [(nights[date(2026, 9, d)].hrv, nights[date(2026, 9, d)].hr) for d in (29, 30)] == [(86.8, 35.0),
                                                                                               (80.8, 35.0)]
    assert (nights[date(2026, 10, 1)].hrv, nights[date(2026, 10, 1)].hr) == (76.8, 36.0)
    for metric in ("hr", "hrv", "tst24"):
        assert nt.band(nights, metric, D8) is None
    assert nt.illness_alert(nights, D8) is None


async def test_owner_top_facts_and_sommeil(db_session: AsyncSession, test_user: User):
    await seed_owner_v4(db_session, test_user)
    page = await sante.health_page(db_session, test_user.id, today=D8)
    # one ring (v4.4), Récupération: the score as a percentage (owner: « Mets des pourcentages plutôt que des
    # valeurs »), in its state's colour, no label under it (the state's word next to it names it), nothing under it
    # (a night was measured: not « estimé »); no Sommeil or Charge ring, no Détail
    rec = page["ring"]
    assert (rec["value"], rec["unit"], rec["tone"], rec["label"], rec["sub"], rec["href"]) == (
        "65", "%", "warn", None, None, "#recuperation")
    assert rec["aria"] == "Récupération 65\u00a0%. Récupération en cours."
    assert "rings" not in page and "detail" not in page
    # the facts: this morning's 24 h, 8h36 of an 8-h need, at most 100 % (≥ 7 h: « suffisant », green); the
    # Transjeju's window open to 16/10 at 65 (« encore 8 jours », orange: no activity named, no date, no hours, no
    # percentage); VFC and FC de nuit: no usual values yet, « en construction », no percentage (grey)
    assert [(f["name"], f["qual"], f["value"], f["word"], f["tone"], f["href"]) for f in page["facts"]] == [
        ("Sommeil", "sur 8\u00a0h", "100\u00a0%", "suffisant", "ok", "#sommeil"),
        ("Effort récent", None, None, "encore 8 jours", "warn", None),
        ("VFC", "7 nuits", None, "en construction", "none", "#vfc"),
        ("FC de nuit", "7 nuits", None, "en construction", "none", "#fc")]
    # Sommeil: the hero prints the times and this morning's 24 h, its hours printed once (the row says 100 %);
    # 22:42 is approximate: 22:40
    s = page["sleep"]
    h = s["hero"]
    assert (h["label"], h["times"], h["nap"], h["night"], h["total"]) == ("Cette nuit", "22:40 → 07:30", None, None,
                                                                          "8h36 sur 24\u202fh")
    # its stages from COROS's « Sleep Summary » (the main night's: shown, never judged), WHOOP's order; COROS has
    # no intervals: no hypnogram, and no plain night bar either (the stages bar replaces it)
    assert h["timeline"] is None and not h["stages"]
    # each phase rounded to 10 min (H, v4.2): 12, 326, 71 and 119 min; the bar keeps the raw shape
    assert [(p["name"], p["min"], p["hm"]) for p in h["phases"]["parts"]] == [
        ("Éveil", 12, "10 min"), ("Léger", 326, "5h30"), ("Profond", 71, "1h10"), ("Paradoxal", 119, "2h00")]
    assert h["phases"]["aria"] == ("Phases estimées par ta montre : éveil 10 minutes, léger 5 heures 30, profond "
                                   "1 heure 10, paradoxal 2 heures 00.")
    assert [k for k, _ in s["ranges"]] == ["14"] and s["r"] == "14"  # nothing 14 to 90 days old: no « 3 mois »
    bars = s["bars"]["14"]
    d = json.loads(bars["data"])
    # it rests on the mean (the latest night is the Sommeil row's); a tap: the 24 h, its parts, the night and its
    # times
    assert bars["read"] == ["8h20", "en moyenne", ""]
    assert d["r"][13] == ["8h36", "", "nuit du mer. 7 au jeu. 8 · 22:40 → 07:30"]
    assert d["r"][12] == ["8h10", "nuit 5h50 + sieste 2h20", "nuit du mar. 6 au mer. 7 · 23:35 → 05:40"]
    assert d["r"][0] == ["1h22", "sieste seule", "nuit du jeu. 24 au ven. 25 · nuit non enregistrée"]  # no « ? »
    assert "?" not in json.dumps(d["r"], ensure_ascii=False)
    # 4 nights left for the medians in 28 days (06/10 and 07/10 are D+3 and D+4 after the ultra): none yet (5, H)
    assert s["habits"] is None
    marks = {r["iso"]: r["marks"] for r in s["rows"]}
    assert marks["2026-10-06"] == marks["2026-10-07"] == "◇ après un gros effort" and marks["2026-10-08"] == "—"


async def test_owner_cards(db_session: AsyncSession, test_user: User):
    await seed_owner_v4(db_session, test_user)
    page = await sante.health_page(db_session, test_user.id, today=D8)
    # a watch worn some nights only: the axis spans the measured nights (14 days at least), not 30 with dots at
    # the right edge; the latest night selected (its value is the card's): PaceForge's own 99,7 ms, no band yet
    vfc, fc = page["vfc"], page["fc"]
    # a no-break space: the display font has no narrow one (« 100ms » glued, UX11)
    assert (vfc["title"], vfc["n"], vfc["read"]) == ("VFC · 14 nuits", 14,
                                                     ["100\u00a0ms", "", "nuit du mer. 7 au jeu. 8"])
    assert (fc["title"], fc["n"], fc["read"]) == ("FC de nuit · 14 nuits", 14,
                                                  ["35\u00a0bpm", "", "nuit du mer. 7 au jeu. 8"])
    assert [t["label"] for t in vfc["xt"]] == ["25", "26", "27", "28", "29", "30", "1", "2", "3", "4", "5", "6", "7",
                                               "8"]
    assert len(vfc["dots"]) == 5 and len(fc["dots"]) == 6
    # never a cliff from a few ms: the VFC axis spans ≥ 30 % of its median, the FC axis ≥ 14 bpm
    assert [t["label"] for t in vfc["ticks"]] == ["80", "100"] and [t["label"] for t in fc["ticks"]] == ["30", "40"]
    assert "charge" not in page  # no « Charge · 14 jours »: the activities are Activités' (v4.1)
    # each card opens on its status line (v4.3): no usual values yet — none of his nights since 29/09 counts (Korea,
    # 7 zones east, then the ultra) and 08/10 alone does: 6 more make the 7 of a band, read by the score once they
    # are before its 7-night window: 13 nights from now (21/10); v4.4: the nights that do not count are hollow
    for c in (vfc, fc):
        assert c["status"] == {"key": "none", "value": None, "word": "en construction", "tone": None,
                               "meaning": sante.COUNTS, "text": "En construction : tes valeurs habituelles seront "
                                                                "prêtes dans 13 nuits, si tu portes ta montre."}
        assert [d["out"] for d in c["dots"]][-1] is False and all(d["out"] for d in c["dots"][:-1])
        assert c["legend"] == sante.LEGEND_DOTS["both"] and not c["band"] and not c["band_prov"]
    # Récupération · 14 jours: each day as computed that day, with the same rule; it rests on the mean
    rec = page["recup"]
    d = json.loads(rec["data"])
    points = {day: r[:2] for day, r in zip(d["d"], d["r"], strict=True)}
    # 25/09 → 28/09: no night, and his Les Houches days (two Très longues, 18/09 and 19/09) have no window left
    # (65 until 23/09 and 24/09): no score — v4.2's M3 made them one 16-h ultra, 65 until 29/09 (« Grosse sortie :
    # Morning Trail Run », the owner's « je ne comprends pas »)
    assert [points[f"2026-09-{k}"] for k in (25, 26, 27, 28)] == [["—", ""]] * 4
    # before the race: Sommeil alone (no band): 100, capped at 80 without VFC nor FC
    assert points["2026-09-29"] == points["2026-10-01"] == ["80\u00a0%", "● Bonne récupération"]
    # 02/10: the Transjeju still running at midnight (uploaded on 03/10), no night: no score; 03/10 (the day it
    # ended) → 05/10: no night, its window alone: the Ultra's 35 (v4.3, H), « estimé, nuit non enregistrée »
    assert points["2026-10-02"] == ["—", ""]
    assert points["2026-10-03"] == points["2026-10-04"] == points["2026-10-05"] == ["35\u00a0%",
                                                                                   "■ Récupération faible"]
    assert [d["r"][k][2] for k in (8, 9, 10)] == [f"{x} · estimé, nuit non enregistrée" for x in (
        "sam. 3 oct.", "dim. 4 oct.", "lun. 5 oct.")]
    assert d["a"][8] == ("samedi 3 octobre : 35\u00a0%, récupération faible, estimé car ta montre n'a pas "
                         "enregistré la nuit.")
    # 06/10: D+3, 5h33 → 46,5, Charge 20: raw (30 × 46,5 + 20 × 20) / 50 = 35,9, the window's 35 binds: the same
    # 35 as the days without a night (v4.2: 36 « À ménager » under the 40 of the days before, « pourquoi le 6 ? »)
    assert points["2026-10-06"] == ["35\u00a0%", "■ Récupération faible"] and d["r"][11][2] == "mar. 6 oct."
    assert d["a"][11] == "mardi 6 octobre : 35\u00a0%, récupération faible."
    # 07/10 and 08/10: Sommeil 100, Charge 20: raw 68, the window's 65 binds
    assert points["2026-10-07"] == points["2026-10-08"] == ["65\u00a0%", "◐ Récupération en cours"]
    assert d["a"][13] == "jeudi 8 octobre : 65\u00a0%, récupération en cours."
    # the date, the score and the state only: no activity named in the card (v4.3, owner: « mets juste les scores »)
    for word in ("Transjeju", "Morning", "sortie", "il y a"):
        assert word not in rec["data"], word
    assert rec["read"] == ["57\u00a0%", "en moyenne", ""]  # (80 × 3 + 35 × 4 + 65 × 2) / 9 = 56,7
    classes, est = [b["cls"] for b in rec["bars"]], [b["est"] for b in rec["bars"]]
    assert classes[4] == "ok" and classes[8:12] == ["danger"] * 4 and classes[12] == "warn" and rec["bars"][-1]["today"]
    assert est == [False] * 8 + [True] * 3 + [False] * 3 and rec["hatched"] == ["danger"]  # 03 → 05/10 hatched
    assert d["t"][11] == "danger" and [ln["label"] for ln in rec["lines"]] == ["70\u00a0%", "40\u00a0%"]


async def test_owner_page_html(as_user: AsyncClient, db_session: AsyncSession, test_user: User, on_owner_day):
    await _link(db_session, test_user)
    await seed_owner_v4(db_session, test_user)
    html = (await as_user.get("/sante")).text
    main = _main(html)
    # one page, in the spec's order
    order = ['class="pf-sante-top', 'class="pf-card pf-facts"', 'id="recuperation"', 'id="sommeil"',
             'id="vfc"', 'id="fc"', "Comment je calcule ta récupération", "Comment je lis tes nuits",
             "Les chiffres de chaque nuit"]
    at = [main.index(k) for k in order]
    assert at == sorted(at)
    assert 'aria-label="Récupération 65\u00a0%. Récupération en cours."' in main
    assert main.count('class="pf-ring ') == 1  # one ring (v4.4)
    # the state: its glyph and its word next to the ring, nothing else (v4.3: no activity named, no link to one)
    state = re.search(r'<h2 class="pf-state-word">(.*?)</h2>', main, re.S).group(1)
    assert re.sub(r"<[^>]+>", " ", state).split() == ["Récupération", "en", "cours"]
    assert '<svg class="pf-state-glyph"' in state and "pf-state-text" not in main  # the tone in a shape too
    assert "/activity/" not in main and "Transjeju" not in main and "Morning Trail Run" not in main
    # the facts: a dot and the name, the value and its word; a link to its section when there is one (Effort
    # récent has none); no title (the page's is « Aujourd'hui »), no sub-score
    facts = main.split('<section class="pf-card pf-facts"')[1].split("</section>")[0]
    rows = [(tag, href, unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip()))
            for tag, href, body in re.findall(r'<(a|div) class="pf-fact is-\w+"(?: href="([^"]+)")?>(.*?)</\1>', facts)]
    assert rows == [("a", "#sommeil", "Sommeil sur 8 h 100 % suffisant"),
                    ("div", "", "Effort récent encore 8 jours"), ("a", "#vfc", "VFC 7 nuits en construction"),
                    ("a", "#fc", "FC de nuit 7 nuits en construction")]
    for href in re.findall(r'href="#([\w-]+)"', facts + main.split('<div class="pf-sante-top')[1].split("</h2>")[0]):
        assert f'id="{href}"' in main, href  # never a link to nothing
    assert "<h2" not in facts and "<h3" not in facts and "Détail du score" not in main and "Contributeurs" not in main
    # each number printed once before a tap (the closed folds are the accessible alternative): the score, this
    # morning's sleep (as a percentage at the top, its hours in the Sommeil section), the VFC and FC of last night,
    # the means; the state word once (the title); no sub-score (no « 20 »), no week's hours (no Charge ring)
    seen = re.sub(r"\s+", " ", _visible(html).replace("\u00a0", " ").replace("\u202f", " "))
    for number in ("65 %", "100 %", "8h36", "100 ms", "35 bpm", "8h20", "57 %", "1h10", "5h30", "2h00"):
        assert len(re.findall(rf"(?<![\d,h:]){re.escape(number)}(?![\d,h:A-Za-z])", seen)) == 1, number
    assert "16h53" not in seen and not re.search(r"(?<![\d,h:])20(?![\d,h:A-Za-z])", seen)
    words = re.sub(r"\s+", " ", seen)
    assert words.count("Récupération en cours") == 1 and "en cours" not in words.replace("Récupération en cours", "")
    # the Sommeil section's « Cette nuit » prints the window and this morning's 24 h (the row says it in %)
    hero = main.split('<div class="pf-card pf-nhero">')[1].split("</div>")[0]
    assert "22:40 → 07:30" in hero and "8h36 sur 24" in hero and "8h36" not in facts
    assert "pf-viz-flag" not in main and "pf-viz-ev" not in main
    # Santé's cards: no ‹ › disc; a tap, a drag or the keyboard (the hidden range input) selects
    assert 'data-step=' not in main and main.count('class="pf-viz-range sr-only"') == 4  # 4 cards, no 3 mois
    # the stages: one bar, its legend names each phase with its minutes (never colour alone), the caption
    assert main.count('class="pf-phases-bar" aria-hidden="true"') == 1
    assert '<li><i class="pf-ph is-deep" aria-hidden="true"></i>Profond <b>1h10</b></li>' in main
    assert ("Ta montre estime les phases d'après ton pouls et tes mouvements. Elles montrent la forme de ta nuit, "
            "pas sa qualité." in main and "pf-tl-night" not in main)
    # the Sommeil hero is not the race page's grid (UX3)
    assert 'class="pf-card pf-nhero"' in main and "pf-hero\"" not in main
    # the ring prints the score alone, as a percentage (the « % » smaller): the state's word names it, no label
    ring = main.split('<a class="pf-ring pf-ring-recup')[1].split("</a>")[0]
    assert '<span>65<span class="pf-ring-unit">%</span></span>' in ring and "pf-ring-label" not in ring


async def test_nothing_that_was_removed_comes_back(as_user: AsyncClient, db_session: AsyncSession, test_user: User,
                                                   on_owner_day):
    """No tabs, no check-in, no race chip or flag (a Route exists: Santé never reads it), no « séance », no filler
    sentence, no brand value, no « Cœur la nuit », no clock-window chart."""
    await _link(db_session, test_user)
    await seed_owner_v4(db_session, test_user)
    db_session.add(Route(user_id=test_user.id, name="Transjeju 100M", total_distance_km=162,
                         total_elevation_gain=6100, race_date="2026-10-02", start_hour=21, start_minute=0,
                         sport_type="trail", result_json={"total_actual_s": 60807, "actual": []}))
    for k in range(10):
        _add(db_session, test_user, "recovery", D8 - timedelta(days=k), 84, {"level": "x", "full_h": 45})
        _add(db_session, test_user, "sleep_score", D8 - timedelta(days=k), 89)
    _add(db_session, test_user, "feel", D8, 3, {"why": ["sick", "legs"], "alcohol": True}, "PaceForge")
    await db_session.flush()
    html = (await as_user.get("/sante")).text
    main = _main(html)
    assert 'role="tablist"' not in html and 'role="tab"' not in html and "pf-stab" not in html
    assert "<form" not in main and "pf-chip" not in main
    lower = main.lower()
    for word in NEVER + BRAND:
        assert word.lower() not in lower, word
    # the race's name: never a chip, a flag or a countdown, and no activity named at all (v4.3, owner: « Ne
    # mentionne pas les sorties dans la partie Santé »)
    assert "Transjeju" not in main and "Grosse sortie" not in main and "/activity/" not in main
    assert "pf-viz-win" not in main and "pf-hyp" not in main


def test_no_seance_in_santes_templates_and_copy():
    """« Ne parle pas de séance » (owner, 2026-10-08): not a word of it in what Santé prints."""
    files = ["app/templates/sante.html", "app/templates/partials/sante_page.html",
             "app/templates/partials/sante_sync.html", "app/services/sante.py", "app/services/sante_today.py",
             "app/services/sante_score.py", "app/services/sante_sleep.py"]
    for f in files:
        assert "séance" not in (ROOT / f).read_text(encoding="utf-8").lower(), f
    assert all("séance" not in w for w in nt.TAG_WORDS.values())  # the nights' table prints these


# ── a rich Garmin wearer ────────────────────────────────────────────────────

def _garmin_rows(today: date, hrv_last=None, hr_last=None, days=60) -> dict:
    """Every night on a Garmin: HRV ≈ 70 ms (ln spread ≈ 0.11), nightly HR 44–46, 7h20 asleep, real stages."""
    return night_rows(range(0, days), today=today,
                      hr=lambda k: hr_last if hr_last is not None and k < 7 else 44.0 + k % 3,
                      hrv=lambda k: hrv_last if hrv_last is not None and k < 7 else 70 * math.exp(0.08 * (k % 5 - 2)))


async def _seed_rows(db: AsyncSession, user: User, rows: dict):
    for metric, per_day in rows.items():
        for d, (v, det, src) in per_day.items():
            _add(db, user, metric, d, v, det, src)
    await db.flush()


async def _runs(db: AsyncSession, user: User, today: date, n: int = 10):
    for i in range(n):
        d = today - timedelta(days=1 + 3 * i)
        db.add(Activity(user_id=user.id, strava_activity_id=7700 + i, sport_type="Run", name=f"Footing {i}",
                        start_date=datetime(d.year, d.month, d.day, 6, tzinfo=timezone.utc), distance=10000,
                        moving_time=3000, elapsed_time=3100, total_elevation_gain=40, average_heartrate=140,
                        raw_data={"utc_offset": 7200}))
    await db.flush()


async def test_rich_wearer_a_lowish_hrv_is_green_and_lower(db_session: AsyncSession, test_user: User):
    """A full band, HR in it, the 7-night HRV under it (sub-score 40–69): the score is the weighted mean of VFC,
    FC de nuit and Sommeil (no window: no Charge, v4.2), no placement: still « Bonne récupération » (≥ 70), lower;
    the VFC row is orange (under its normal), and its card says so in words (v4.3); no sub-score shown (v4.4)."""
    today = date(2026, 10, 8)
    await _seed_rows(db_session, test_user, _garmin_rows(today, hrv_last=60.0))
    await _runs(db_session, test_user, today)
    page = await sante.health_page(db_session, test_user.id, today=today)
    assert page["state"]["key"] == "ok" and page["state"]["text"] is None
    s = page["score"]
    nights = nt.build_nights(_garmin_rows(today, hrv_last=60.0), today)
    b = nt.band(nights, "hrv", today - timedelta(days=6))
    z = (math.log(60.0) - math.log(b["center"])) / b["sd"]
    sub = 100 * (z + 2.5) / 2
    raw = (25 * sub + 25 * 100 + 30 * 100) / 80
    assert not b["provisional"] and -2.5 < z < -0.5 and 40 <= sub < 70
    assert s["value"] == math.floor(raw + 0.5) and 70 <= s["value"] < 90 and s["caps"] == []
    # the facts (v4.4, in percent): 7h20 of 8 h, 92 % « suffisant »; no Effort récent (no window); VFC's 7-night
    # mean (60 ms, every night) against its usual value (the band's centre), orange; FC de nuit's 44,86 against 45:
    # −0,3 %, to the percent 0 %: « comme d'habitude »
    vfc = f"{sante.viz.signed(sante.sc.rounded(100 * (60.0 - b['center']) / b['center']))}\u00a0%"
    assert vfc.startswith("\u2212") and [(f["name"], f["value"], f["word"], f["tone"]) for f in page["facts"]] == [
        ("Sommeil", "92\u00a0%", "suffisant", "ok"), ("VFC", vfc, "plus basse que d'habitude", "warn"),
        ("FC de nuit", None, "comme d'habitude", "ok")]
    # « bien ou pas bien ? »: each card opens on its status line, its row's percentage, word and colour
    assert page["vfc"]["status"] == {"key": "below", "value": vfc, "word": "plus basse que d'habitude",
                                     "text": f"{vfc} plus basse que d'habitude", "tone": "warn",
                                     "meaning": "Ça arrive avec la fatigue, le stress, l'alcool ou un début de maladie."}
    assert page["fc"]["status"] == {"key": "in", "value": None, "word": "comme d'habitude", "text": "comme d'habitude",
                                    "tone": "ok", "meaning": None}
    assert page["vfc"]["read"][2].startswith("nuit du mer. 7 au jeu. 8 · d'habitude ")
    assert "provisoire" not in page["vfc"]["read"][2] and page["vfc"]["title"] == "VFC · 30 nuits"
    assert page["ring"]["tone"] == "ok" and page["ring"]["sub"] is None


async def test_rich_wearer_a_red_hrv_caps_the_ring_at_69(db_session: AsyncSession, test_user: User):
    """The judges' rich user: the 7-night VFC far under its normal (an empty bar) with FC de nuit in its normal:
    alone it caps nothing (v4.2, Buchheit 2014 Table 2), so its facts row is orange, never red; the mean
    (25 × 0 + 25 × 100 + 30 × 100) / 80 = 68,75 → 69, « Récupération en cours » (the reason is data: no sentence)."""
    today = date(2026, 10, 8)
    await _seed_rows(db_session, test_user, _garmin_rows(today, hrv_last=50.0))
    await _runs(db_session, test_user, today)
    page = await sante.health_page(db_session, test_user.id, today=today)
    st, s = page["state"], page["score"]
    assert (st["key"], st["tone"], st["text"]) == ("hrv", "warn", None)
    assert s["value"] == 69 and s["caps"] == [] and s["raw0"] == 68.75
    assert next(p for p in s["parts"] if p["key"] == "hrv")["sub"] == 0
    row = {f["key"]: f for f in page["facts"]}["vfc"]
    assert (row["value"], row["word"], row["tone"]) == ("\u221229\u00a0%", "plus basse que d'habitude", "warn")
    assert (page["vfc"]["status"]["value"], page["vfc"]["status"]["word"], page["vfc"]["status"]["tone"]) == (
        row["value"], row["word"], row["tone"])  # one source of truth
    assert page["ring"]["tone"] == "warn"


async def test_rich_wearer_hypnogram_in_the_hero(db_session: AsyncSession, test_user: User):
    from app.models.health import HealthSample

    today = date(2026, 10, 8)
    await _seed_rows(db_session, test_user, _garmin_rows(today))
    start = datetime(2026, 10, 7, 23, 0)
    for i, kind in enumerate(("core", "deep", "core", "rem", "awake", "core", "rem")):
        a = start + timedelta(minutes=60 * i)
        db_session.add(HealthSample(user_id=test_user.id, metric="sleep", kind=kind, start_at=a,
                                    end_at=a + timedelta(minutes=60), value=60, source="Garmin"))
    await db_session.flush()
    h = (await sante.health_page(db_session, test_user.id, today=today))["sleep"]["hero"]
    assert h["stages"] and len(h["timeline"]["segs"]) == 7 and h["timeline"]["main"] is None
    assert [nm for nm, _, _ in h["timeline"]["lanes"]] == ["Éveil", "Paradoxal", "Léger", "Profond"]
    assert [s["k"] for s in h["timeline"]["segs"]] == ["light", "deep", "light", "rem", "awake", "light", "rem"]
    # the hypnogram above, the stages bar under it: its minutes summed from the real intervals (no stored minutes)
    assert [(p["name"], p["min"]) for p in h["phases"]["parts"]] == [("Éveil", 60), ("Léger", 180), ("Profond", 60),
                                                                      ("Paradoxal", 120)]


# ── an empty user ───────────────────────────────────────────────────────────

async def test_strava_only_has_no_score_and_one_line(as_user: AsyncClient, db_session: AsyncSession,
                                                     test_user: User):
    today = await sante.athlete_today(db_session, test_user.id)
    await _runs(db_session, test_user, today, n=6)
    page = await sante.health_page(db_session, test_user.id, today=today)
    assert page["state"] is None and page["score"]["value"] is None
    assert page["line"] == "Connecte ta montre pour voir ta récupération." and page["connect"]
    assert page["ring"]["value"] == "—" and page["facts"] == [] and "rings" not in page
    assert page["recup"] is None and page["sleep"]["state"] == "never"
    assert page["vfc"] is None and page["fc"] is None and "charge" not in page
    # no section for it: the empty ring is plain (never a link to nothing) and says nothing under it
    assert (page["ring"]["href"], page["ring"]["sub"]) == (None, None)
    html = (await as_user.get("/sante")).text
    assert "Connecte ta montre pour voir ta récupération." in html
    assert 'href="/settings#coros"' in html and 'href="/settings#garmin"' in html
    assert 'id="sommeil"' not in html and "Comment je lis tes nuits" not in html and "pf-facts" not in html
    rings = re.findall(r'<(a|div) class="pf-ring pf-ring-(\w+)[^"]*"(?: href="([^"]+)")?', _main(html))
    assert rings == [("div", "recup", "")]


async def test_a_watch_without_a_night_this_morning_is_not_asked_to_connect(db_session: AsyncSession,
                                                                           test_user: User):
    today = date(2026, 10, 8)
    await _seed_rows(db_session, test_user, night_rows([2, 3], today=today, source="COROS",
                                                       hr_method="coros_sleep_summary"))
    page = await sante.health_page(db_session, test_user.id, today=today)
    assert page["state"] is None and page["line"] == sante.td.NO_NIGHT and not page["connect"]
    assert sante.td.NO_NIGHT == "Pas de score ce matin : ta montre n'a pas enregistré ta nuit."
    assert page["sleep"]["hero"]["total"] == "7h20 sur 24 h"  # an older night: its total, the ring is empty


async def test_nothing_at_all_is_the_connect_panel(as_user: AsyncClient):
    page = (await as_user.get("/sante")).text
    assert "Ta montre et tes activités" in page and "pf-rings" not in page
    for anchor in ("coros", "garmin", "strava"):  # Strava too: the panel names it, a button connects it
        assert f'href="/settings#{anchor}"' in page, anchor
    assert "Connecter Strava" in page


async def test_the_ring_links_to_a_section_the_page_has(db_session: AsyncSession, test_user: User):
    """A watch that sent one night but none this morning, no score: Récupération is a plain ring (under 2 days
    for its 14 days), no facts (no state: the line says why, the Sommeil section shows the night); with 2 past
    days scored, Récupération links to its 14 days even without today's score."""
    today = date(2026, 10, 8)
    await _seed_rows(db_session, test_user, night_rows([3], today=today, source="COROS",
                                                       hr_method="coros_sleep_summary"))
    page = await sante.health_page(db_session, test_user.id, today=today)
    assert page["score"]["value"] is None and page["recup"] is None and page["ring"]["href"] is None
    assert page["facts"] == [] and page["line"] == sante.td.NO_NIGHT and page["sleep"]["state"] == "ok"
    await _seed_rows(db_session, test_user, night_rows([2], today=today, source="COROS",
                                                       hr_method="coros_sleep_summary"))
    page = await sante.health_page(db_session, test_user.id, today=today)
    assert page["score"]["value"] is None and page["recup"] and page["ring"]["href"] == "#recuperation"


# ── opening Santé syncs a stale link ────────────────────────────────────────

async def test_opening_sante_syncs_a_stale_link_and_reloads_only_with_news(as_user: AsyncClient,
                                                                          db_session: AsyncSession,
                                                                          test_user: User, monkeypatch):
    from app.services import coros as coros_service

    conn = await _link(db_session, test_user)
    started = []
    monkeypatch.setattr(coros_service, "schedule_sync", lambda uid: started.append(uid) or True)
    # never synced: the page starts a sync and waits for it, silently (no spinner, no word: owner, 2026-10-08)
    page = (await as_user.get("/sante")).text
    assert started == [test_user.id] and "Mise à jour" not in page and "Synchro" not in page
    poller = re.search(r'<div id="sante-sync" hx-get="(/sante/sync-status\?v=[^"]+)"[^>]*></div>', page)
    url = poller.group(1).replace("&amp;", "&")
    conn.sync_claimed_at = datetime.now(timezone.utc)
    await db_session.flush()
    r = await as_user.get(url)
    assert 'hx-get="/sante/sync-status' in r.text and "n=1" in r.text and "HX-Refresh" not in r.headers
    conn.sync_claimed_at = None
    conn.last_sync_at = datetime.now(timezone.utc)
    await db_session.flush()
    r = await as_user.get(url)
    assert "HX-Refresh" not in r.headers and r.text.strip() == '<div id="sante-sync"></div>'  # done: nothing shown
    conn.sync_claimed_at = datetime.now(timezone.utc)
    await db_session.flush()
    r = await as_user.get(url.replace("n=0", "n=40"))
    assert r.text.strip() == '<div id="sante-sync"></div>'  # it stops waiting after 2 min
    conn.sync_claimed_at = None
    _add(db_session, test_user, "sleep", date.today(), 450, {"bedtime": "23:00", "wake": "07:00"})
    await db_session.flush()
    r = await as_user.get(url)
    assert r.headers.get("HX-Refresh") == "true"
    started.clear()
    page = (await as_user.get("/sante")).text
    assert started == [] and "sante-sync" not in page and "Synchroniser maintenant" not in page
    conn.last_sync_at = datetime.now(timezone.utc) - timedelta(hours=3)
    conn.last_error = "COROS ne répond pas pour l'instant."
    await db_session.flush()
    page = (await as_user.get("/sante")).text  # a failed watch: not retried on its own (Réglages say why)
    assert started == [] and "ne répond pas" not in page


async def test_a_crashing_sync_never_stays_en_cours(db_session: AsyncSession, test_user: User, monkeypatch):
    from app.services import coros as coros_service

    conn = await _link(db_session, test_user)
    monkeypatch.setattr(db_session, "commit", db_session.flush)

    async def boom(*a, **k):
        raise ValueError("an unexpected bug")
    monkeypatch.setattr(coros_service, "sync_connection", boom)
    out = await coros_service.run_sync(db_session, conn)
    assert out == {"ok": False, "error": "La synchro a échoué : réessaie plus tard."}
    assert conn.sync_claimed_at is None and conn.last_error


async def test_a_missing_night_syncs_again_sooner(as_user: AsyncClient, db_session: AsyncSession,
                                                 test_user: User, monkeypatch):
    from app.services import coros as coros_service

    conn = await _link(db_session, test_user)
    conn.last_sync_at = datetime.now(timezone.utc) - timedelta(minutes=40)
    started = []
    monkeypatch.setattr(coros_service, "schedule_sync", lambda uid: started.append(uid) or True)
    today = datetime.now(timezone.utc).date()
    db_session.add(HealthMetric(user_id=test_user.id, date=today - timedelta(days=1), metric="sleep", value=450,
                                source="COROS", n_samples=1, details={"bedtime": "23:00", "wake": "07:00"}))
    await db_session.flush()
    page = (await as_user.get("/sante")).text
    assert started == [test_user.id] and "pas encore reçue" not in page  # synced sooner, silently
    started.clear()
    db_session.add(HealthMetric(user_id=test_user.id, date=today, metric="sleep", value=440,
                                source="COROS", n_samples=1, details={"bedtime": "23:10", "wake": "06:50"}))
    await db_session.flush()
    await as_user.get("/sante")
    assert started == []
    conn.last_sync_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    await db_session.execute(delete(HealthMetric).where(HealthMetric.date == today))
    await db_session.flush()
    await as_user.get("/sante")
    assert started == []


async def test_dedupe_matches_activites_when_strava_writes_json_null(db_session: AsyncSession, test_user: User):
    from app.services import sante_training as st

    t = datetime(2026, 10, 1, 7, tzinfo=timezone.utc)
    for i, (dt, km, sec, splits) in enumerate([(0, 10.0, 3000, [{"split": 1}]), (60, 10.2, 3300, None)]):
        db_session.add(Activity(user_id=test_user.id, strava_activity_id=7000 + i, sport_type="Run", name=f"r{i}",
                                start_date=t + timedelta(seconds=dt), distance=km * 1000, moving_time=sec,
                                elapsed_time=sec, raw_data={}, splits_metric=splits))
    await db_session.flush()
    ss = await st.load_sessions(db_session, test_user.id, date(2026, 10, 6))
    assert [s.minutes for s in ss] == [50]  # the copy with splits, as Activités keeps it
