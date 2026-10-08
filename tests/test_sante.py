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
NEVER = ("séance", "pas jugée", "se construit", "pas assez de nuits", "autour de la course", "Ce matin",
         "Comment je vais", "⚑", "J+", "J‑", "Jour de course", "Footing facile", "intensité", "Reprise",
         "Forme du jour", "Cœur la nuit")


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
    assert "Synchroniser maintenant" in page and "Pas encore de données de COROS" in page
    assert "Connecter COROS" not in page


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
                       ("/sante?vue=entrainement", "/sante#charge"), ("/sante?vue=course", "/sante"),
                       ("/sante?vue=tendances", "/sante"), ("/sante?vue=aujourdhui", "/sante"),
                       ("/sante?vue=sommeil&r=365", "/sante#sommeil")):
        r = await as_user.get(url)
        assert r.status_code == 302 and r.headers["location"] == where, url
    r = await as_user.get("/sante/sommeil?r=90")
    assert r.status_code == 303 and r.headers["location"] == "/sante?r=90#sommeil"
    r = await as_user.get("/sante/sommeil?r=90", headers={"HX-Request": "true"})
    assert r.status_code == 303  # no partial any more: the one page


async def test_the_check_in_post_is_kept_harmlessly(as_user: AsyncClient, db_session: AsyncSession,
                                                    test_user: User, on_owner_day):
    """No check-in on the page; an old form still posting gets its answer stored and the page back, and the
    recovery never reads it (no « malade » trigger, no Ressenti)."""
    await seed_owner_v4(db_session, test_user)
    before = (await sante.health_page(db_session, test_user.id, today=D8))["score"]["value"]
    r = await as_user.post("/sante/feel", data={"feel": "3", "toggle": "sick"}, headers={"HX-Request": "true"})
    assert r.status_code == 303 and r.headers["location"] == "/sante"
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
    """The Transjeju (an activity: 16h53 stops included, ended 03/10 13:53 in Korea) is an ultra: 10 days of
    recovery, the raw score capped at 40 on D+1 → D+3, then at 65; 08/10 is D+5 from its end, 6 days after it
    started. No band yet (no night of 7 usable), so Sommeil (8h36 → 100) and Charge récente (ultra → 20) make the
    score: raw = (25 × 100 + 20 × 20) / 45 = 64,4, under every cap (65; 69 for Charge's 20; 80 without VFC nor FC):
    64, « Récupération en cours » (its band); the sentence names the activity, never its 16h53 (the ring's)."""
    act = await seed_owner_v4(db_session, test_user)
    page = await sante.health_page(db_session, test_user.id, today=D8)
    st, s = page["state"], page["score"]
    assert (st["key"], st["tone"], st["word"]) == ("effort", "warn", "Récupération en cours")
    assert st["text"] == "Grosse sortie il y a 6 jours : Transjeju 100M." and st["href"] == f"/activity/{act.id}"
    raw = (25 * 100 + 20 * 20) / 45
    assert s["value"] == 64 == math.floor(raw + 0.5) and s["tone"] == "warn"
    assert {p["key"]: round(p["sub"]) for p in s["parts"]} == {"sleep": 100, "load": 20}
    assert s["absent"] == ["hrv", "hr"] and s["caps"] == []  # the 65 cap is above raw: not binding


async def test_owner_nights_tags_and_no_band_yet(db_session: AsyncSession, test_user: User):
    """06/10 is D+3 after the effort: « après grosse sortie », out of the bands and of the illness alert; the
    others are usable. A band needs 7 nights: none yet, and no sentence says so on the page."""
    await seed_owner_v4(db_session, test_user)
    from app.services import sante_training as st

    sessions = await st.load_sessions(db_session, test_user.id, D8)
    nights = await nt.load_nights(db_session, test_user.id, D8, sessions=sessions, efforts=st.efforts(sessions))
    owned = {d: sorted(n.tags) for d, n in nights.items() if n.asleep is not None}
    assert owned == {date(2026, 9, 29): [], date(2026, 9, 30): [], date(2026, 10, 1): [], date(2026, 10, 6): ["big"],
                     date(2026, 10, 7): [], D8: []}
    assert (nights[D8].asleep, nights[D8].tst24, nights[D8].hrv, nights[D8].hr) == (516, 516, 99.7, 35.0)
    # his real nights before the race: PaceForge's own VFC from COROS's raw series (COROS says 90, 83, 80)
    assert [(nights[date(2026, 9, d)].hrv, nights[date(2026, 9, d)].hr) for d in (29, 30)] == [(86.8, 35.0),
                                                                                               (80.8, 35.0)]
    assert (nights[date(2026, 10, 1)].hrv, nights[date(2026, 10, 1)].hr) == (76.8, 36.0)
    for metric in ("hr", "hrv", "tst24"):
        assert nt.band(nights, metric, D8) is None
    assert nt.illness_alert(nights, D8) is None


async def test_owner_rings_contributors_and_sommeil(db_session: AsyncSession, test_user: User):
    await seed_owner_v4(db_session, test_user)
    page = await sante.health_page(db_session, test_user.id, today=D8)
    rec, sleep, charge = page["rings"]
    # under the Récupération ring only its label: the state's word is the title just below (said once)
    assert (rec["value"], rec["tone"], rec["sub"], rec["href"]) == ("64", "warn", None, "#recuperation")
    assert rec["aria"] == ("Récupération 64 sur 100. Récupération en cours. "
                           "Grosse sortie il y a 6 jours : Transjeju 100M.")
    assert (sleep["value"], sleep["tone"], sleep["href"]) == ("8h36", "ok", "#sommeil")  # ≥ 7 h: green
    assert sleep["dash"] == sleep["c"]  # 8h36 ≥ 8 h: a full ring
    assert (charge["value"], charge["tone"], charge["href"]) == ("16h53", "accent", "#charge")
    assert charge["tick"]  # the usual week, 1× on a ring that runs to 2×
    # Contributeurs: no number; Charge says « grosse sortie » without the days the state line already prints
    c = page["contrib"]
    assert [(r["name"], r["word"], r["sub"], r["tone"]) for r in c["rows"]] == [
        ("Sommeil", "suffisant", 100, "ok"), ("Charge récente", "grosse sortie", 20, "accent")]
    assert c["absent"] == "Pas encore dans le score : VFC, FC de nuit."
    # Sommeil: the hero prints the times, not the total (the ring's); 22:42 is approximate: 22:40
    s = page["sleep"]
    h = s["hero"]
    assert (h["label"], h["times"], h["nap"], h["night"], h["total"]) == ("Cette nuit", "22:40 → 07:30", None, None,
                                                                          None)
    assert h["timeline"]["main"] and not h["stages"]  # COROS: one bar, no hypnogram
    assert [k for k, _ in s["ranges"]] == ["14"] and s["r"] == "14"  # nothing 14 to 90 days old: no « 3 mois »
    bars = s["bars"]["14"]
    d = json.loads(bars["data"])
    # it rests on the mean (the latest night is the ring's); a tap: the 24 h, its parts, the night and its times
    assert bars["read"] == ["8h20", "en moyenne", ""]
    assert d["r"][13] == ["8h36", "", "nuit du mer. 7 au jeu. 8 · 22:40 → 07:30"]
    assert d["r"][12] == ["8h10", "nuit 5h50 + sieste 2h20", "nuit du mar. 6 au mer. 7 · 23:35 → 05:40"]
    assert d["r"][0] == ["1h22", "sieste seule", "nuit du jeu. 24 au ven. 25 · pas de nuit mesurée"]  # no « ? »
    assert "?" not in json.dumps(d["r"], ensure_ascii=False)
    assert s["habits"]["stats"] == [("Coucher", "23:50"), ("Lever", "09:25")]  # 5 nights: no regularity yet
    marks = {r["iso"]: r["marks"] for r in s["rows"]}
    assert marks["2026-10-06"] == "◇ après grosse sortie" and marks["2026-10-08"] == "—"


async def test_owner_cards(db_session: AsyncSession, test_user: User):
    act = await seed_owner_v4(db_session, test_user)
    page = await sante.health_page(db_session, test_user.id, today=D8)
    # a watch worn some nights only: the axis spans the measured nights (14 days at least), not 30 with dots at
    # the right edge; the latest night selected (its value is the card's): PaceForge's own 99,7 ms, no band yet
    vfc, fc = page["vfc"], page["fc"]
    assert (vfc["title"], vfc["n"], vfc["read"]) == ("VFC · 14 nuits", 14,
                                                     ["100\u202fms", "", "nuit du mer. 7 au jeu. 8"])
    assert (fc["title"], fc["n"], fc["read"]) == ("FC de nuit · 14 nuits", 14,
                                                  ["35\u202fbpm", "", "nuit du mer. 7 au jeu. 8"])
    assert [t["label"] for t in vfc["xt"]] == ["25", "26", "27", "28", "29", "30", "1", "2", "3", "4", "5", "6", "7",
                                               "8"]
    assert len(vfc["dots"]) == 5 and len(fc["dots"]) == 6
    # never a cliff from a few ms: the VFC axis spans ≥ 30 % of its median, the FC axis ≥ 14 bpm
    assert [t["label"] for t in vfc["ticks"]] == ["80", "100"] and [t["label"] for t in fc["ticks"]] == ["30", "40"]
    ch = page["charge"]
    d = json.loads(ch["data"])
    # it rests on the count (nothing selected: an empty day is never the default; the hours are the ring's)
    assert ch["read"] == ["1", "activité", ""] and d["sel"] == 14 and ch["rest"]
    assert d["r"][7] == ["16h53", "", "ven. 2 oct. · Transjeju 100M"]  # the long effort is simply a tall bar
    assert d["h"][7] == {"href": f"/activity/{act.id}", "label": "Voir ›"}
    assert d["r"][12] == ["—", "", "mer. 7 oct. · aucune activité"] and d["h"][12] is None
    # Récupération · 14 jours: each day as computed that day, with the same rule; it rests on the mean
    rec = page["recup"]
    d = json.loads(rec["data"])
    points = {day: r[:2] for day, r in zip(d["d"], d["r"], strict=True)}
    # before the race: Sommeil alone (no band, too few activities for Charge): 100, capped at 80 without VFC nor FC
    assert points["2026-09-29"] == points["2026-10-01"] == ["80", "● Bien récupéré"]
    # 06/10: D+3, 5h33 → 46,5, Charge 20: raw (25 × 46,5 + 20 × 20) / 45 = 34,7 under every cap → 35, à ménager
    assert points["2026-10-06"] == ["35", "■ À ménager"]
    assert points["2026-10-07"] == points["2026-10-08"] == ["64", "◐ Récupération en cours"]
    assert d["r"][11][2] == "mar. 6 oct. · Grosse sortie : Transjeju 100M"  # no « il y a », counted from that day
    assert points["2026-10-02"] == points["2026-10-04"] == ["—", ""]  # no night: no score
    assert rec["read"] == ["67", "en moyenne", ""]  # (80 × 3 + 35 + 64 × 2) / 6 = 67,2
    classes = [b["cls"] for b in rec["bars"]]
    assert classes[4] == "ok" and classes[11] == "danger" and classes[12] == "warn" and rec["bars"][-1]["today"]
    assert d["t"][11] == "danger" and [ln["label"] for ln in rec["lines"]] == ["70", "40"]


async def test_owner_page_html(as_user: AsyncClient, db_session: AsyncSession, test_user: User, on_owner_day):
    await _link(db_session, test_user)
    act = await seed_owner_v4(db_session, test_user)
    html = (await as_user.get("/sante")).text
    main = _main(html)
    # one page, in the spec's order
    order = ['class="pf-rings"', 'class="pf-state', 'id="contributeurs"', 'id="recuperation"', 'id="sommeil"',
             'id="vfc"', 'id="fc"', 'id="charge"', "Comment je calcule ta récupération",
             "Comment je lis tes nuits", "Les chiffres de chaque nuit"]
    at = [main.index(k) for k in order]
    assert at == sorted(at)
    assert 'aria-label="Récupération 64 sur 100. Récupération en cours.' in main
    assert f'<a href="/activity/{act.id}">Grosse sortie il y a 6 jours : Transjeju 100M.</a>' in main
    assert '<svg class="pf-state-glyph"' in main  # the tone in a shape too
    # each number printed once before a tap (the closed folds are the accessible alternative): the score, the
    # night, the week's hours, the VFC and FC of last night, the means; the state word once (the title)
    seen = _visible(html)
    for number in ("64", "8h36", "16h53", "100", "35", "8h20", "67"):
        assert len(re.findall(rf"(?<![\d,h:]){re.escape(number)}(?![\d,h:A-Za-z])", seen)) == 1, number
    assert seen.count("Récupération en cours") == 1 and "en cours" not in seen.replace("Récupération en cours", "")
    assert "pf-viz-flag" not in main and "pf-viz-ev" not in main
    # Santé's cards: no ‹ › disc; a tap, a drag or the keyboard (the hidden range input) selects
    assert 'data-step=' not in main and main.count('class="pf-viz-range sr-only"') == 5  # 5 cards, no 3 mois


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
    # the race's name: never a chip, a flag or a countdown; the activity is named once, in the state's sentence
    assert _visible(html).count("Transjeju 100M") == 1 and "Grosse sortie il y a 6 jours : Transjeju 100M." in main
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
    """A full band, HR in it, the 7-night HRV under it (sub-score 40–69): the score is the weighted mean, no
    placement: still « Bien récupéré » (≥ 70), lower; the Contributeurs say « basse » for VFC, an orange bar."""
    today = date(2026, 10, 8)
    await _seed_rows(db_session, test_user, _garmin_rows(today, hrv_last=58.0))
    await _runs(db_session, test_user, today)
    page = await sante.health_page(db_session, test_user.id, today=today)
    assert page["state"]["key"] == "ok" and page["state"]["text"] is None
    s = page["score"]
    nights = nt.build_nights(_garmin_rows(today, hrv_last=58.0), today)
    b = nt.band(nights, "hrv", today - timedelta(days=6))
    z = (math.log(58.0) - math.log(b["center"])) / b["sd"]
    sub = 100 * (z + 2.5) / 2
    raw = (30 * sub + 25 * 100 + 25 * 100 + 20 * 100) / 100
    assert not b["provisional"] and -2.5 < z < -0.5 and 40 <= sub < 70
    assert s["value"] == math.floor(raw + 0.5) and 70 <= s["value"] < 90 and s["caps"] == []
    rows = {r["name"]: r for r in page["contrib"]["rows"]}
    assert {k: r["word"] for k, r in rows.items()} == {"VFC": "basse", "FC de nuit": "dans ta normale",
                                                       "Sommeil": "suffisant", "Charge récente": "pas de grosse sortie"}
    assert rows["VFC"]["tone"] == "warn" and page["contrib"]["absent"] is None
    assert page["vfc"]["read"][2].startswith("nuit du mer. 7 au jeu. 8 · normale ")
    assert "provisoire" not in page["vfc"]["read"][2] and page["vfc"]["title"] == "VFC · 30 nuits"
    assert page["rings"][0]["tone"] == "ok" and page["rings"][0]["sub"] is None


async def test_rich_wearer_a_red_hrv_caps_the_ring_at_69(db_session: AsyncSession, test_user: User):
    """The judges' rich user: the 7-night VFC far under its normal (a red, empty bar) can never sit under a
    green ring: 69, « Récupération en cours », « VFC basse sur 7 nuits. »."""
    today = date(2026, 10, 8)
    await _seed_rows(db_session, test_user, _garmin_rows(today, hrv_last=50.0))
    await _runs(db_session, test_user, today)
    page = await sante.health_page(db_session, test_user.id, today=today)
    st, s = page["state"], page["score"]
    assert (st["key"], st["tone"], st["text"]) == ("hrv", "warn", "VFC basse sur 7 nuits.")
    assert s["value"] == 69 and s["caps"] == ["red"] and s["raw0"] == 70
    assert {r["name"]: (r["word"], r["tone"]) for r in page["contrib"]["rows"]}["VFC"] == ("basse", "danger")
    assert page["rings"][0]["tone"] == "warn"


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
    assert [nm for nm, _, _ in h["timeline"]["lanes"]] == ["Éveil", "REM", "Léger", "Profond"]


# ── an empty user ───────────────────────────────────────────────────────────

async def test_strava_only_has_no_score_and_one_line(as_user: AsyncClient, db_session: AsyncSession,
                                                     test_user: User):
    today = await sante.athlete_today(db_session, test_user.id)
    await _runs(db_session, test_user, today, n=6)
    page = await sante.health_page(db_session, test_user.id, today=today)
    assert page["state"] is None and page["score"]["value"] is None
    assert page["line"] == "Connecte ta montre pour ta récupération." and page["connect"]
    assert [r["value"] for r in page["rings"][:2]] == ["—", "—"] and page["rings"][2]["value"] != "—"
    assert page["contrib"] is None and page["recup"] is None and page["sleep"]["state"] == "never"
    assert page["vfc"] is None and page["fc"] is None and page["charge"] is not None
    # no section for them: the two empty rings are plain (never a link to nothing) and say nothing under the label
    assert [(r["href"], r["sub"]) for r in page["rings"][:2]] == [(None, None), (None, None)]
    assert page["rings"][2]["href"] == "#charge"
    html = (await as_user.get("/sante")).text
    assert "Connecte ta montre pour ta récupération." in html
    assert 'href="/settings#coros"' in html and 'href="/settings#garmin"' in html
    assert 'id="sommeil"' not in html and "Comment je lis tes nuits" not in html
    rings = re.findall(r'<(a|div) class="pf-ring pf-ring-(\w+)[^"]*"(?: href="([^"]+)")?', _main(html))
    assert rings == [("div", "recup", ""), ("div", "sommeil", ""), ("a", "charge", "#charge")]
    for href in re.findall(r'class="pf-ring[^"]*" href="#([\w-]+)"', html):
        assert f'id="{href}"' in html, href


async def test_a_watch_without_a_night_this_morning_is_not_asked_to_connect(db_session: AsyncSession,
                                                                           test_user: User):
    today = date(2026, 10, 8)
    await _seed_rows(db_session, test_user, night_rows([2, 3], today=today, source="COROS",
                                                       hr_method="coros_sleep_summary"))
    page = await sante.health_page(db_session, test_user.id, today=today)
    assert page["state"] is None and page["line"] == "Pas de nuit mesurée ce matin." and not page["connect"]
    assert page["sleep"]["hero"]["total"] == "7h20 sur 24 h"  # an older night: its total, the ring is empty


async def test_nothing_at_all_is_the_connect_panel(as_user: AsyncClient):
    page = (await as_user.get("/sante")).text
    assert "Ta montre et tes activités" in page and "pf-rings" not in page
    for anchor in ("coros", "garmin", "strava"):  # Strava too: the panel names it, a button connects it
        assert f'href="/settings#{anchor}"' in page, anchor
    assert "Connecter Strava" in page


async def test_every_ring_links_to_a_section_the_page_has(db_session: AsyncSession, test_user: User):
    """A watch that sent one night but none this morning, no score: Sommeil still links to its section,
    Récupération is a plain ring (no Contributeurs, under 2 days for its 14 days), Charge too (no activity in 14
    days: no card); with 2 past days scored, Récupération links to its 14 days even without today's score."""
    today = date(2026, 10, 8)
    await _seed_rows(db_session, test_user, night_rows([3], today=today, source="COROS",
                                                       hr_method="coros_sleep_summary"))
    page = await sante.health_page(db_session, test_user.id, today=today)
    rec, sleep, charge = page["rings"]
    assert page["score"]["value"] is None and page["recup"] is None and rec["href"] is None
    assert sleep["href"] == "#sommeil" and sleep["value"] == "—" and page["sleep"]["state"] == "ok"
    # no activity in 14 days: no card, a plain Charge ring that says « 0 h », never « 0 min »
    assert charge["href"] is None and charge["value"] == "0\u202fh" and page["charge"] is None
    await _seed_rows(db_session, test_user, night_rows([2], today=today, source="COROS",
                                                       hr_method="coros_sleep_summary"))
    page = await sante.health_page(db_session, test_user.id, today=today)
    assert page["score"]["value"] is None and page["recup"] and page["rings"][0]["href"] == "#recuperation"


async def test_the_charge_ring_runs_to_twice_the_usual_week(db_session: AsyncSession, test_user: User):
    """The arc runs from 0 to 2 × the usual week (the median of the last 12 complete weeks, H), a tick at 1 ×: a
    double week looks different from a normal one; the usual week is said, never printed."""
    today = date(2026, 10, 8)
    await _seed_rows(db_session, test_user, _garmin_rows(today))
    for i in range(90):  # 50 min every 3 days for 90 days: the usual week ≈ 1h40 to 2h30
        d = today - timedelta(days=1 + 3 * i)
        db_session.add(Activity(user_id=test_user.id, strava_activity_id=9900 + i, sport_type="Run", name="Footing",
                                start_date=datetime(d.year, d.month, d.day, 6, tzinfo=timezone.utc), distance=9000,
                                moving_time=3000, elapsed_time=3000, total_elevation_gain=40,
                                average_heartrate=140, raw_data={"utc_offset": 7200}))
    await db_session.flush()
    page = await sante.health_page(db_session, test_user.id, today=today)
    charge = page["rings"][2]
    week = sum(1 for i in range(90) if 1 + 3 * i <= 6) * 50
    usual = sante._usual_week(await sante.st.load_sessions(db_session, test_user.id, today), today)
    assert charge["value"] == sante.viz.hm(week) and charge["tick"]["x1"] == 50.0 and charge["tick"]["y1"] > 50
    assert charge["dash"] == round(min(1, week / (2 * usual)) * charge["c"], 2) and charge["dash"] < charge["c"]
    assert "semaine habituelle" in charge["aria"] and sante.viz.hm(usual) not in charge["aria"]


# ── opening Santé syncs a stale link ────────────────────────────────────────

async def test_opening_sante_syncs_a_stale_link_and_reloads_only_with_news(as_user: AsyncClient,
                                                                          db_session: AsyncSession,
                                                                          test_user: User, monkeypatch):
    from app.services import coros as coros_service

    conn = await _link(db_session, test_user)
    started = []
    monkeypatch.setattr(coros_service, "schedule_sync", lambda uid: started.append(uid) or True)
    # never synced: the page starts a sync and waits for it, quietly
    page = (await as_user.get("/sante")).text
    assert started == [test_user.id] and "Mise à jour de tes données…" in page and "Synchro en cours" not in page
    url = re.search(r'hx-get="(/sante/sync-status\?v=[^"]+)"', page).group(1).replace("&amp;", "&")
    conn.sync_claimed_at = datetime.now(timezone.utc)
    await db_session.flush()
    r = await as_user.get(url)
    assert "Mise à jour de tes données…" in r.text and "n=1" in r.text and "HX-Refresh" not in r.headers
    conn.sync_claimed_at = None
    conn.last_sync_at = datetime.now(timezone.utc)
    await db_session.flush()
    r = await as_user.get(url)
    assert "HX-Refresh" not in r.headers and "Synchroniser maintenant" in r.text
    conn.sync_claimed_at = datetime.now(timezone.utc)
    await db_session.flush()
    r = await as_user.get(url.replace("n=0", "n=40"))
    assert "Synchroniser maintenant" in r.text and "sync-status" not in r.text
    conn.sync_claimed_at = None
    _add(db_session, test_user, "sleep", date.today(), 450, {"bedtime": "23:00", "wake": "07:00"})
    await db_session.flush()
    r = await as_user.get(url)
    assert r.headers.get("HX-Refresh") == "true"
    started.clear()
    page = (await as_user.get("/sante")).text
    assert started == [] and "Synchroniser maintenant" in page
    conn.last_sync_at = datetime.now(timezone.utc) - timedelta(hours=3)
    conn.last_error = "COROS ne répond pas pour l'instant."
    await db_session.flush()
    page = (await as_user.get("/sante")).text
    assert started == [] and "Synchroniser maintenant" in page


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
    assert started == [test_user.id] and "cette nuit pas encore reçue" in page
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
