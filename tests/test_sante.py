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
         # v4.4 (owner: « Sommeil 100, Charge récente 20 avec les donuts au-dessus, on comprend rien »): no
         # sub-score, no « Détail du score », no Charge; 2026-10-08 (« Fais comme WHOOP »): three dials, no single
         # ring with its state's word next to it, no « En bref » rows
         "Détail du score", "Pas encore dans le score", "Charge récente", "pf-contrib", "pf-ring-charge",
         'class="pf-rings"', "En bref", "pf-fact", "pf-sante-top", "pf-state-word", "pf-nhero")


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
    through the night (01:00 → 05:00 local, H): the raw score capped at 35 on D+1 → D+3, then at 65 until D+13
    (16/10; v4.2); 08/10 is D+5 from its end, 6 days after it started. No band yet (5 nights of VFC, 6 of FC de
    nuit: 7 needed), so Sommeil alone makes the mean (8h36 → 100; no Charge récente in it since 2026-10-08, owner:
    « Fais comme WHOOP »): raw 100; its window's 65 binds (80 without VFC nor FC de nuit): 65, « Récupération en
    cours », as with v4.2's Charge (raw 68). v4.3: the page says the state in words only, no activity named (owner:
    « mets juste les scores »); his Les Houches days of 18/09 and 19/09 have no window left (each ended on D+5:
    23/09, 24/09)."""
    await seed_owner_v4(db_session, test_user)
    page = await sante.health_page(db_session, test_user.id, today=D8)
    st, s = page["state"], page["score"]
    assert (st["key"], st["tone"], st["word"], st["text"]) == ("effort", "warn", "Récupération en cours", None)
    assert "href" not in st and "estimated" not in st and st["aria"] == "Récupération en cours."
    assert s["raw0"] == 100 and s["value"] == 65 and s["tone"] == "warn" and s["reason"] == "effort"
    assert {p["key"]: round(p["sub"]) for p in s["parts"]} == {"sleep": 100}
    assert s["absent"] == ["hrv", "hr"] and s["caps"] == ["effort", "no_heart"]  # the window's 65 binds
    from app.services import sante_training as st_
    sessions = await st_.load_sessions(db_session, test_user.id, D8)
    w = st_.effort_window(st_.efforts(sessions), D8)
    assert (w["cap"], w["until"]) == (65, date(2026, 10, 16))  # D+13: it ran through the night (01:00 → 05:00, H)


async def test_owner_nights_tags_and_no_band_yet(db_session: AsyncSession, test_user: User):
    """06/10 and 07/10 are D+3 and D+4 after the ultra: « après ultra » (D+1 → D+4, v4.2), they never fire the
    illness alert. 29/09 → 01/10 are his first nights in Korea, 7 zones east of Les Houches (his activities of
    18-19/09 at UTC+2, his nights at UTC+9): « décalage horaire », 7 nights from the first one (Janse van Rensburg
    2021). Every measured night counts in the bands (owner, 2026-10-08), but a band needs 7 nights: none yet (5 of
    VFC, 6 of FC de nuit, 6 of sleep)."""
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
        assert nt.band(nights, metric, D8) is None and nt.normal(nights, metric, D8) is None
    assert [sum(1 for n in nights.values() if n.value(m) is not None) for m in ("hrv", "hr", "tst24")] == [5, 6, 6]
    assert nt.illness_alert(nights, D8) is None


async def test_owner_dials_rows_and_sommeil(db_session: AsyncSession, test_user: User):
    """The approved mockup (2026-10-08, owner: « Fais comme WHOOP, ça doit rester simple »), on his fixture: three
    dials — Sommeil 96 % « suffisant » (8h36 of a 9-h need: 8 h without his answer, + 1 h owed since the race,
    in the sleep hue), Récupération 65 % « en cours »,
    Entraînement 15h35 (moving time, as Activités) « pas encore d'habitude » (his activities since 18/09 are 3 complete weeks: under the 4 of a
    usual week, so the 7 days' time itself, no arc) — each a link to its card; the Récupération card's rows: VFC
    and FC de nuit « en construction », ready in 2 nights and after his next night (every measured night counts),
    « Effort récent » 8 days before he is recovered (the Transjeju's window to 16/10, at 65: orange)."""
    await seed_owner_v4(db_session, test_user)
    page = await sante.health_page(db_session, test_user.id, today=D8)
    assert [(d["key"], d["value"], d["unit"], d["label"], d["sub"], d["tone"], d["href"]) for d in page["dials"]] == [
        ("sommeil", "96", "%", "Sommeil", "suffisant", "sleep", "#sommeil"),
        ("recup", "65", "%", "Récupération", "en cours", "warn", "#recuperation"),
        ("entrainement", "15h35", None, "Entraînement", "pas encore d'habitude", "accent", "#entrainement")]
    assert [d["aria"] for d in page["dials"]] == [
        "Sommeil 96\u00a0% de ton besoin de 9 heures, suffisant.", "Récupération 65\u00a0%, en cours.",
        "Entraînement : 15 heures 35 d'activité ces 7 derniers jours, pas encore d'habitude."]
    assert [d["dash"] for d in page["dials"]][2] == 0 and "ring" not in page and "facts" not in page
    assert [(r["name"], r["qual"], r["value"], r["word"], r["detail"], r["tone"]) for r in page["rows"]] == [
        ("VFC", "7 nuits", None, "en construction", "prête dans 2\u00a0nuits", "none"),
        ("FC de nuit", "7 nuits", None, "en construction", "prête après ta prochaine nuit", "none"),
        ("Effort récent", None, "8\u00a0jours", "avant d'être récupéré", None, "warn")]
    assert page["training"] == {"dial": page["dials"][2], "week": None, "usual": None,
                                "word": "pas encore de semaine habituelle", "since": date(2026, 10, 2)}
    # Sommeil: « Cette nuit », the times (22:42 is approximate: 22:40) and this morning's 24 h over its 9-h need,
    # its hours printed once (the dial says 96 %); the need said under it, and asked (no answer yet)
    s = page["sleep"]
    h = s["hero"]
    assert (h["label"], h["times"], h["nap"], h["total"], h["need"]) == ("Cette nuit", "22:40 → 07:30", None, "8h36",
                                                                         "sur 9h00 de besoin")
    assert s["need"]["line"] == "Ton besoin aujourd'hui\u00a0: 8\u00a0h, + 1\u00a0h de sommeil en retard."
    assert s["need"]["ask"] and not s["need"]["answered"] and not any(on for *_, on in s["need"]["choices"])
    # its stages from COROS's « Sleep Summary » (the main night's: shown, never judged), WHOOP's order; COROS has
    # no intervals: no hypnogram, and no plain night bar either (the stages bar replaces it)
    assert h["timeline"] is None and not h["stages"]
    # each phase rounded to 10 min (H, v4.2): 12, 326, 71 and 119 min; the bar keeps the raw shape
    assert [(p["name"], p["min"], p["hm"]) for p in h["phases"]["parts"]] == [
        ("Éveil", 12, "10 min"), ("Léger", 326, "5h30"), ("Profond", 71, "1h10"), ("Paradoxal", 119, "2h00")]
    assert h["phases"]["aria"] == ("Phases estimées par ta montre : éveil 10 minutes, léger 5 heures 30, profond "
                                   "1 heure 10, paradoxal 2 heures.")
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
    # every night counts in the medians (owner, 2026-10-08), the jet-lagged and the post-ultra ones too: 6 nights in
    # 28 days (5 at least, H); the « rendormi » 05:40 of 07/10 left out of the wake; no regularity yet (8, H)
    assert s["habits"] == {"stats": [("Coucher", "23:55"), ("Lever", "09:15")], "n": 6, "regular": None}
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
    # each card opens on its status line (v4.3): no usual values yet. Every measured night counts, last night
    # included (owner, 2026-10-08: « Tous les relevés VFC doivent compter en fait, pareil pour la FC »): VFC has 5
    # (29/09, 30/09, 01/10, 07/10, 08/10), 2 more make the 7 of a band; FC de nuit 6 (06/10 too), 1 more
    # the row in the Récupération card says when they will be ready (printed once), the card says it in words
    when = {"vfc": "dans 2\u00a0nuits", "fc": "après ta prochaine nuit"}
    for key, c in (("vfc", vfc), ("fc", fc)):
        assert c["status"] == {"key": "none", "value": None, "word": "en construction", "detail": f"prête {when[key]}",
                               "tone": None, "meaning": None,
                               "text": "Tes valeurs habituelles s'afficheront ici dès 7 nuits mesurées."}
        assert not c["band"] and not c["band_prov"] and all(set(d) == {"i", "x", "y"} for d in c["dots"])  # filled
    # the 7-night line where 3 nights of the last 7 hold: FC de nuit from 06/10 (01/10 alone draws no segment)
    assert vfc["legend"] == [sante.LEGEND_DOT] and fc["legend"] == [sante.LEGEND_DOT, sante.LEGEND_MEAN]
    # Récupération · 14 jours: each day as computed that day, with the same rule; it rests on the mean
    rec = page["recup"]
    d = json.loads(rec["data"])
    points = {day: r[:2] for day, r in zip(d["d"], d["r"], strict=True)}
    # 25/09 → 28/09: no night, and his Les Houches days (two Très longues, 18/09 and 19/09) have no window left
    # (65 until 23/09 and 24/09): no score — v4.2's M3 made them one 16-h ultra, 65 until 29/09 (« Grosse sortie :
    # Morning Trail Run », the owner's « je ne comprends pas »)
    assert [points[f"2026-09-{k}"] for k in (25, 26, 27, 28)] == [["—", ""]] * 4
    # a day without a score draws nothing (owner, 2026-10-08: « pas de point »): a tap says it plainly
    assert d["r"][0] == ["—", "", "ven. 25 sept. · pas de mesure ce jour-là"]
    assert d["a"][0] == "vendredi 25 septembre : pas de mesure ce jour-là."
    assert [b["i"] for b in rec["bars"] if b.get("miss")] == [0, 1, 2, 3, 7, 8, 9, 10]
    # before the race: Sommeil alone (no band): 100, capped at 80 without VFC nor FC
    assert points["2026-09-29"] == points["2026-10-01"] == ["80\u00a0%", "● Bonne récupération"]
    # 02/10: the Transjeju still running at midnight (uploaded on 03/10), no night: no score; 03/10 (the day it
    # ended) → 05/10: no night, no score either, its window or not (owner, 2026-10-09, like WHOOP: « Mets un
    # cadran vide, oui »): nothing drawn, a tap says it plainly
    assert points["2026-10-02"] == points["2026-10-03"] == points["2026-10-04"] == points["2026-10-05"] == ["—", ""]
    assert [d["r"][k][2] for k in (8, 9, 10)] == [f"{x} · pas de mesure ce jour-là" for x in (
        "sam. 3 oct.", "dim. 4 oct.", "lun. 5 oct.")]
    assert d["a"][8] == "samedi 3 octobre : pas de mesure ce jour-là."
    # 06/10: D+3, 5h33 against a 9h30 need (8 h, + 30 min after the ultra, + 1 h owed): Sommeil alone 20, under
    # the window's 35 (2026-10-09; 35 before, the need then 8 h)
    assert points["2026-10-06"] == ["20\u00a0%", "■ Récupération faible"] and d["r"][11][2] == "mar. 6 oct."
    assert d["a"][11] == "mardi 6 octobre : 20\u00a0%, récupération faible."
    # 07/10 and 08/10: Sommeil 95 and 100 (8h10 of 9h30, 8h36 of 9 h), the window's 65 binds
    assert points["2026-10-07"] == points["2026-10-08"] == ["65\u00a0%", "◐ Récupération en cours"]
    assert d["a"][13] == "jeudi 8 octobre : 65\u00a0%, récupération en cours."
    # the date, the score and the state only: no activity named in the card (v4.3, owner: « mets juste les scores »)
    for word in ("Transjeju", "Morning", "sortie", "il y a"):
        assert word not in rec["data"], word
    assert rec["read"] == ["65\u00a0%", "en moyenne", ""]  # (80 × 3 + 20 + 65 × 2) / 6 = 65
    classes, est = [b["cls"] for b in rec["bars"]], [b["est"] for b in rec["bars"]]
    assert classes[4] == "ok" and classes[8:11] == [""] * 3 and classes[11] == "danger" and classes[12] == "warn"
    assert rec["bars"][-1]["today"] and not any(est) and rec["hatched"] == []  # no score estimated any more
    assert d["t"][11] == "danger" and [ln["label"] for ln in rec["lines"]] == ["70\u00a0%", "40\u00a0%"]


async def test_the_entrainement_link_lists_the_7_days_it_counts(as_user: AsyncClient, db_session: AsyncSession,
                                                                 test_user: User, monkeypatch):
    """Owner, 2026-10-09: « C'est deux fois le même lien ? Ça ne filtre pas sur la semaine ? »: the card's one link
    opens Activités on the activities its 7 days count — the same sessions, the same total — with a way back to
    all; a day that is not one of the last month opens the whole list."""
    from app.services import sante_training as st_

    await seed_owner_v4(db_session, test_user)

    async def today(*a, **k):
        return D8
    monkeypatch.setattr(sante, "athlete_today", today)
    monkeypatch.setattr(st_, "athlete_today", today)
    page = await sante.health_page(db_session, test_user.id, today=D8)
    since = page["training"]["since"]
    sessions = [s for s in await st_.load_sessions(db_session, test_user.id, D8) if s.day >= since]
    html = (await as_user.get(f"/activities?depuis={since.isoformat()}")).text
    recent = html.split('<section id="recent"')[1].split("</section>")[0]
    assert '<h2 id="recent-h" class="pf-h3">7 derniers jours</h2>' in recent and "15h35" in recent  # the dial's
    assert recent.count('class="pf-activity-row') == len(sessions) > 0
    assert '<a class="pf-sum-link" href="/activities">Toutes tes activités ›</a>' in recent
    assert '<section id="semaines"' not in html and 'id="load-more"' not in html  # the 7 days alone
    assert "7 j :" not in html  # their kilometres printed once, in their own line
    for bad in ("abc", "2026-07-01", "2026-12-01"):
        assert 'id="recent"' not in (await as_user.get(f"/activities?depuis={bad}")).text, bad


async def test_owner_page_html(as_user: AsyncClient, db_session: AsyncSession, test_user: User, on_owner_day):
    await _link(db_session, test_user)
    await seed_owner_v4(db_session, test_user)
    html = (await as_user.get("/sante")).text
    main = _main(html)
    # one page, the approved mockup's order (2026-10-08, « Fais comme WHOOP »; « Pourquoi je n'ai rien dans Sommeil
    # et Entraînement ? »): the dials, then their three cards right under them in the same order — Récupération (its
    # rows), Sommeil (last night), Entraînement (the 7 days) —, then the details — Récupération's 14 days and its
    # VFC and FC de nuit charts, Sommeil's 24-h chart and habits —, then the folds
    order = ['class="pf-dials"', 'id="recuperation"', 'class="pf-rows"', 'id="sommeil"', 'class="pf-row pf-night"',
             'id="entrainement"', 'class="pf-row pf-week"', 'id="recuperation-detail"', "Récupération · 14 jours",
             'id="vfc"',
             'id="fc"', 'id="sommeil-detail"', "Sommeil sur 24 h", "pf-habits", "Comment je calcule ta récupération",
             "Comment je lis tes nuits", "Les chiffres de chaque nuit"]
    at = [main.index(k) for k in order]
    assert at == sorted(at)
    # each card's title a link « › » to its details further down (Activités for Entraînement), like the mockup
    titles = re.findall(r'<h2 id="h-(\w+)" class="pf-sum-h"><a class="pf-sum-go" href="([^"]+)">([^<]+)<span '
                        r'class="pf-sum-chev" aria-hidden="true">›</span></a></h2>', main)
    assert titles == [("recup", "#recuperation-detail", "Récupération"), ("sommeil", "#sommeil-detail", "Sommeil"),
                      ("train", "/activities?depuis=2026-10-02", "Entraînement")]
    # three dials, in WHOOP's order, each a link to its card (a full aria-label), the state said in words
    dials = re.findall(r'<a class="pf-ring pf-ring-(\w+) is-(\w+)" href="(#\w+)" aria-label="([^"]+)">(.*?)</a>',
                       main, re.S)
    assert [(k, tone, href, unescape(aria)) for k, tone, href, aria, _ in dials] == [
        ("sommeil", "sleep", "#sommeil", "Sommeil 96\u00a0% de ton besoin de 9 heures, suffisant."),
        ("recup", "warn", "#recuperation", "Récupération 65\u00a0%, en cours."),
        ("entrainement", "accent", "#entrainement",
         "Entraînement : 15 heures 35 d'activité ces 7 derniers jours, pas encore d'habitude.")]
    assert [unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip()) for *_, body in dials] == [
        "96 % Sommeil suffisant", "65 % Récupération en cours", "15h35 Entraînement pas encore d'habitude"]
    assert main.count('class="pf-ring ') == 3 and "pf-state-text" not in main  # no alert: no sentence
    for href in re.findall(r'href="#([\w-]+)"', main.split('<div class="pf-dials">')[1].split("</div>\n")[0]):
        assert f'id="{href}"' in main, href  # never a link to nothing
    assert "/activity/" not in main and "Transjeju" not in main and "Morning Trail Run" not in main
    # the Récupération card's rows: a dot and the name, the value over its word (never colour alone); no link
    recup = main.split('<section id="recuperation"')[1].split("</section>")[0]
    rows = [(tone, unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip()))
            for tone, body in re.findall(r'<li class="pf-row is-(\w+)">(.*?)</li>', recup, re.S)]
    assert rows == [("none", "VFC 7 nuits en construction prête dans 2 nuits"),
                    ("none", "FC de nuit 7 nuits en construction prête après ta prochaine nuit"),
                    ("warn", "Effort récent 8 jours avant d'être récupéré")]
    assert "<a " not in recup.split("pf-rows")[1].split("</ul>")[0]
    # the details: the VFC and FC de nuit charts in words (the rows print the numbers)
    recup_detail = main.split('<section id="recuperation-detail"')[1].split("</section>")[0]
    # the usual values to come, said where they will be drawn (owner, 2026-10-09: « Tu ne mets pas les fourchettes
    # pour VFC et FC repos dans les graphiques ? »)
    assert recup_detail.count('<p class="pf-card-status is-plain">Tes valeurs habituelles s&#39;afficheront ici dès 7 '
                              "nuits mesurées.</p>") == 2
    # Sommeil: « Cette nuit », its times, its hours over its 9-h need; the stages; then, in the details, the 24-h
    # chart and the habits
    sommeil = main.split('<section id="sommeil"')[1].split("</section>")[0]
    night = re.search(r'<div class="pf-row pf-night">(.*?)</div>', sommeil, re.S).group(1)
    assert unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", night)).strip()) == (
        "Cette nuit 22:40 → 07:30 8h36 sur 9h00 de besoin")
    assert sommeil.index("pf-night") < sommeil.index("pf-phases") and "Sommeil sur 24 h" not in sommeil
    sleep_detail = main.split('<section id="sommeil-detail"')[1].split("</section>")[0]
    assert sleep_detail.index("Sommeil sur 24 h") < sleep_detail.index("pf-habits")
    # Entraînement: the 7 days (the dial prints their time: no usual week yet), the link to Activités
    train = main.split('<section id="entrainement"')[1].split(">", 1)[1].split("</section>")[0]
    assert unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", train)).strip()) == (
        "Entraînement › 7 derniers jours pas encore de semaine habituelle")
    # one link, its title, to these 7 days in Activités (owner, 2026-10-09: « C'est deux fois le même lien ? Ça ne
    # filtre pas sur la semaine ? »)
    assert train.count("<a ") == 1 and "Voir tes semaines" not in train
    assert "Détail du score" not in main and "Contributeurs" not in main and "En bref" not in main
    # each number printed once before a tap (the closed folds are the accessible alternative): the dials' (the
    # score, this morning's sleep as a percentage, the week's time), the effort's days, last night's hours, the VFC
    # and FC of last night, the means, the stages; no sub-score (no « 20 »); « 65 % » twice, two figures that
    # happen to match: today's score (the dial) and the 14 days' mean (the Récupération chart's readout)
    seen = re.sub(r"\s+", " ", _visible(html).replace("\u00a0", " ").replace("\u202f", " "))
    for number in ("96 %", "15h35", "8 jours", "8h36", "100 ms", "35 bpm", "8h20", "1h10", "5h30", "2h00"):
        assert len(re.findall(rf"(?<![\d,h:]){re.escape(number)}(?![\d,h:A-Za-z])", seen)) == 1, number
    assert len(re.findall(r"(?<![\d,h:])65 %(?![\d,h:A-Za-z])", seen)) == 2 and "65 % en moyenne" in seen
    assert not re.search(r"(?<![\d,h:])20(?![\d,h:A-Za-z])", seen)
    words = re.sub(r"\s+", " ", seen)
    assert words.count("en cours") == 1  # the dial's word, once
    assert "pf-viz-flag" not in main and "pf-viz-ev" not in main
    # Santé's cards: no ‹ › disc; a tap, a drag or the keyboard (the hidden range input) selects
    assert 'data-step=' not in main and main.count('class="pf-viz-range sr-only"') == 4  # 4 charts, no 3 mois
    # a day or a night without a measure draws nothing and no legend names it (owner, 2026-10-08: « s'il n'y a pas
    # de mesure tu ne mets rien, pas de point »): no gap dot, no « pas de score » / « pas de mesure » swatch
    for part in (recup_detail, sleep_detail):
        assert "pf-viz-gap" not in part and "is-gap" not in part
        legends = "".join(re.findall(r'<p class="pf-viz-legend"[^>]*>(.*?)</p>', part, re.S))
        assert "pas de score" not in legends and "pas de mesure" not in legends
    history = recup_detail.split("Récupération · 14 jours")[1].split('id="vfc"')[0]
    # no legend at all: a day without a night has no score and draws nothing (owner, 2026-10-09, like WHOOP)
    assert re.findall(r'<i class="pf-lg ([\w-]+)"></i>([^<]+)</span>', history) == [] and "estimé" not in history
    assert "is-out" not in main and "ne compte" not in main
    # the stages: one bar, its legend names each phase with its minutes (never colour alone); « Comment je lis tes
    # nuits » says they are the watch's estimate
    assert main.count('class="pf-phases-bar" aria-hidden="true"') == 1
    assert '<li><i class="pf-ph is-deep" aria-hidden="true"></i>Profond <b>1h10</b></li>' in main
    assert "pf-tl-night" not in main and "pf-nhero" not in main and "pf-hero\"" not in main
    # the Récupération dial prints the score, as a percentage (the « % » smaller), its name and its word under it
    ring = main.split('<a class="pf-ring pf-ring-recup')[1].split("</a>")[0]
    assert '<span>65<span class="pf-ring-unit">%</span></span>' in ring
    assert '<span class="pf-ring-label" aria-hidden="true">Récupération</span>' in ring


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
    # no form but the sleep need's question (2026-10-09), no chip: the « Ressenti » check-in never comes back
    assert main.count("<form") == 1 and '<form class="pf-need-ask" method="post" action="/sante/besoin">' in main
    assert "pf-chip" not in main
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


async def _rested(db: AsyncSession, user: User, need: int = 450):
    """The athlete answered « 7 h 30 » to « Combien d'heures de sommeil te faut-il pour te sentir reposé ? »: their
    7h20 nights owe 10 min each, a 7h50 need, « suffisant » (the rules a test reads stay alone; the default 8-h
    need has its own tests: test_sante_need)."""
    user.sleep_need_min = need
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
    the VFC row is orange (under its normal), and its card says so in words (v4.3); no sub-score shown (v4.4). The
    usual values are every measured night of the 60 days up to and including last night (owner, 2026-10-08)."""
    today = date(2026, 10, 8)
    await _seed_rows(db_session, test_user, _garmin_rows(today, hrv_last=60.0))
    await _runs(db_session, test_user, today)
    await _rested(db_session, test_user)
    page = await sante.health_page(db_session, test_user.id, today=today)
    assert page["state"]["key"] == "ok" and page["state"]["text"] is None
    s = page["score"]
    nights = nt.build_nights(_garmin_rows(today, hrv_last=60.0), today)
    b = nt.normal(nights, "hrv", today)
    assert b["n"] == 60 and b["until"] == today + timedelta(days=1)  # the week itself included
    z = (math.log(60.0) - math.log(b["center"])) / b["sd"]
    sub = 100 * (z + 2.5) / 2
    raw = (25 * sub + 25 * 100 + 30 * 100) / 80
    assert not b["provisional"] and -2.5 < z < -0.5 and 40 <= sub < 70
    assert s["value"] == math.floor(raw + 0.5) and 70 <= s["value"] < 90 and s["caps"] == []
    # the dials, in percent: Sommeil 7h20 of his 7h50 need (7 h 30 answered, + 20 min owed), 94 % « suffisant »
    # (the sleep colour); Récupération the score, « bonne »; the rows: no Effort récent (no window); VFC's 7-night
    # mean (60 ms, every night) against its usual value (the band's centre), orange; FC de nuit's 44,86 against 45:
    # −0,3 %, to the percent 0 %: « comme d'habitude »
    assert [(d["value"], d["sub"], d["tone"]) for d in page["dials"][:2]] == [("94", "suffisant", "sleep"),
                                                                             (str(s["value"]), "bonne", "ok")]
    vfc = f"{sante.viz.signed(sante.sc.rounded(100 * (60.0 - b['center']) / b['center']))}\u00a0%"
    assert vfc.startswith("\u2212") and [(f["name"], f["value"], f["word"], f["tone"]) for f in page["rows"]] == [
        ("VFC", vfc, "plus basse que d'habitude", "warn"), ("FC de nuit", None, "comme d'habitude", "ok")]
    # « bien ou pas bien ? »: each card opens on its status line, its row's percentage, word and colour
    assert page["vfc"]["status"] == {"key": "below", "value": vfc, "word": "plus basse que d'habitude",
                                     "text": f"{vfc} plus basse que d'habitude", "tone": "warn",
                                     "meaning": "Ça arrive avec la fatigue, le stress, l'alcool ou un début de maladie."}
    assert page["fc"]["status"] == {"key": "in", "value": None, "word": "comme d'habitude", "text": "comme d'habitude",
                                    "tone": "ok", "meaning": None}
    assert page["vfc"]["read"][2].startswith("nuit du mer. 7 au jeu. 8 · d'habitude ")
    assert "provisoire" not in page["vfc"]["read"][2] and page["vfc"]["title"] == "VFC · 30 nuits"


async def test_rich_wearer_a_red_hrv_makes_the_dial_69_en_cours(db_session: AsyncSession, test_user: User):
    """The judges' rich user: the 7-night VFC far under its normal (an empty bar) with FC de nuit in its normal:
    alone it caps nothing (v4.2, Buchheit 2014 Table 2), so its row is orange, never red; the mean
    (25 × 0 + 25 × 100 + 30 × 100) / 80 = 68,75 → 69, « Récupération en cours » (the reason is data: no sentence).
    The week's own nights are in the usual values now (owner, 2026-10-08): 7 nights at 35 ms (−46 %) empty the bar
    (z −2,6); 7 at 50 ms (−26 %) no longer do (test_sante_score: a low week is read against a band that holds
    it)."""
    today = date(2026, 10, 8)
    await _seed_rows(db_session, test_user, _garmin_rows(today, hrv_last=35.0))
    await _runs(db_session, test_user, today)
    await _rested(db_session, test_user)
    page = await sante.health_page(db_session, test_user.id, today=today)
    st, s = page["state"], page["score"]
    assert (st["key"], st["tone"], st["text"]) == ("hrv", "warn", None)
    assert s["value"] == 69 and s["caps"] == [] and s["raw0"] == 68.75
    assert next(p for p in s["parts"] if p["key"] == "hrv")["sub"] == 0
    row = {f["key"]: f for f in page["rows"]}["vfc"]
    assert (row["value"], row["word"], row["tone"]) == ("\u221246\u00a0%", "plus basse que d'habitude", "warn")
    assert (page["vfc"]["status"]["value"], page["vfc"]["status"]["word"], page["vfc"]["status"]["tone"]) == (
        row["value"], row["word"], row["tone"])  # one source of truth
    d = page["dials"][1]
    assert (d["key"], d["value"], d["unit"], d["sub"], d["tone"]) == ("recup", "69", "%", "en cours", "warn")


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
    assert page["rows"] == [] and "ring" not in page and "facts" not in page
    assert page["recup"] is None and page["sleep"]["state"] == "never"
    assert page["vfc"] is None and page["fc"] is None and "charge" not in page
    # no card for them: the empty Sommeil and Récupération dials are plain (never a link to nothing), each with its
    # word; Entraînement links to its card: the runs' time, « pas encore d'habitude » (under 4 complete weeks)
    sleep, recup, train = page["dials"]
    assert (sleep["value"], sleep["sub"], sleep["href"]) == ("—", "pas enregistré", None)
    assert (recup["value"], recup["sub"], recup["href"]) == ("—", "pas de score", None)
    assert (train["sub"], train["href"], train["unit"]) == ("pas encore d'habitude", "#entrainement", None)
    html = (await as_user.get("/sante")).text
    assert "Connecte ta montre pour voir ta récupération." in html
    assert 'href="/settings#coros"' in html and 'href="/settings#garmin"' in html
    assert 'id="sommeil"' not in html and "Comment je lis tes nuits" not in html and 'id="recuperation"' not in html
    rings = re.findall(r'<(a|div) class="pf-ring pf-ring-(\w+)[^"]*"(?: href="([^"]+)")?', _main(html))
    assert rings == [("div", "sommeil", ""), ("div", "recup", ""), ("a", "entrainement", "#entrainement")]


async def test_a_watch_without_a_night_this_morning_is_not_asked_to_connect(db_session: AsyncSession,
                                                                           test_user: User):
    today = date(2026, 10, 8)
    await _seed_rows(db_session, test_user, night_rows([2, 3], today=today, source="COROS",
                                                       hr_method="coros_sleep_summary"))
    page = await sante.health_page(db_session, test_user.id, today=today)
    assert page["state"] is None and page["line"] == sante.td.NO_NIGHT and not page["connect"]
    assert sante.td.NO_NIGHT == "Pas de score ce matin : ta montre n'a pas enregistré ta nuit."
    h = page["sleep"]["hero"]  # an older night: its card, its hours; the dial is this morning's: empty
    assert (h["label"], h["total"]) == ("nuit du lun. 5 au mar. 6", "7h20")
    assert (page["dials"][0]["value"], page["dials"][0]["sub"]) == ("—", "pas enregistré")


async def test_nothing_at_all_is_the_connect_panel(as_user: AsyncClient):
    page = (await as_user.get("/sante")).text
    assert "Ta montre et tes activités" in page and "pf-rings" not in page
    for anchor in ("coros", "garmin", "strava"):  # Strava too: the panel names it, a button connects it
        assert f'href="/settings#{anchor}"' in page, anchor
    assert "Connecter Strava" in page


def _sleep_only(rows: dict) -> dict:
    """night_rows without the heart: the nights' sleep alone (no VFC, no FC de nuit card)."""
    return {**rows, "hrv": {}, "hr_night": {}}


async def test_the_dials_link_to_a_section_the_page_has(db_session: AsyncSession, test_user: User):
    """A watch that sent one night (its sleep alone) but none this morning, no score: Sommeil links to its card,
    Récupération is a plain dial (no row, under 2 days for its 14 days: nothing to link to), Entraînement always
    links to its card; no state: the line says why. With 2 past days scored, Récupération links to its 14 days in
    the details (no row: no card under the dials) even without today's score; a night with its heart values gives
    the card its rows (VFC, FC de nuit), and the dial links to it."""
    today = date(2026, 10, 8)
    kw = {"today": today, "source": "COROS", "hr_method": "coros_sleep_summary"}
    await _seed_rows(db_session, test_user, _sleep_only(night_rows([3], **kw)))
    page = await sante.health_page(db_session, test_user.id, today=today)
    assert page["score"]["value"] is None and page["recup"] is None and page["rows"] == []
    assert [d["href"] for d in page["dials"]] == ["#sommeil", None, "#entrainement"]
    assert page["line"] == sante.td.NO_NIGHT and page["sleep"]["state"] == "ok"
    await _seed_rows(db_session, test_user, _sleep_only(night_rows([2], **kw)))
    page = await sante.health_page(db_session, test_user.id, today=today)
    assert page["score"]["value"] is None and page["recup"] and page["dials"][1]["href"] == "#recuperation-detail"
    await _seed_rows(db_session, test_user, night_rows([5], **kw))
    page = await sante.health_page(db_session, test_user.id, today=today)
    assert [(r["key"], r["word"]) for r in page["rows"]] == [("vfc", "en construction"), ("fc", "en construction")]
    assert page["dials"][1]["href"] == "#recuperation"


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
