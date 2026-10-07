"""The Santé page (v3): « Aujourd'hui | Sommeil » — the routes (login, not
connected, no data yet, errors, the old views redirected, the range swap, the
check-in swap, the opening sync), the owner's own October as a fixture
(Transjeju 100M started 02/10 21:00 in Korea, 16h53; the 7 Oct nap), and the
decision ladder rung by rung, (H) rungs included, then the tiles."""

import re
from datetime import date, datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.health import HealthMetric
from app.models.route import Route
from app.models.user import User
from app.services import nights as nt
from app.services import sante, sante_today
from app.services.sante_today import decide, make_tiles, sparse_line
from app.services.sante_training import Session
from tests import test_coros
from tests.test_coros import _link
from tests.test_nights import D, owner_rows

# the COROS test fixtures (a linked athlete, commits turned into flushes)
as_user, no_commit = test_coros.as_user, test_coros.no_commit

PF_HRV = {"n": 30, "method": "ln_mean_main"}  # PaceForge's own nightly HRV
BRAND = ("Récup COROS", "calculée par COROS", "Training Readiness", "Body Battery", "Score de sommeil", "×1,",
         "forte hausse", "Récupération", "84 %",
         "Charge <small>", "VO2", "Stress de jour", "fatigue %", "Fraîcheur")


def _add(db: AsyncSession, user: User, metric: str, d: date, value: float, details=None, source="COROS"):
    db.add(HealthMetric(user_id=user.id, date=d, metric=metric, value=value, source=source, details=details,
                        n_samples=1))


async def _seed_owner(db: AsyncSession, user: User) -> Route:
    """The owner's October as the COROS sync writes it, the brand rows COROS
    also had (never read), and the Transjeju 100M with its result (16h53)."""
    for metric, per_day in owner_rows().items():
        for d, (v, det, src) in per_day.items():
            _add(db, user, metric, d, v, det, src)
    for k in range(20):
        d = D - timedelta(days=k)
        _add(db, user, "steps", d, 9000, {"kcal": 500})
        _add(db, user, "load", d, 160, {"long": 110, "ratio": 1.45, "comment": "Excessive"})
        _add(db, user, "stress", d, 25)
    _add(db, user, "recovery", D, 84, {"level": "Moderate training recommended", "full_h": 45})
    _add(db, user, "sleep_score", D, 89)
    _add(db, user, "vo2max", D, 61)
    route = Route(user_id=user.id, name="Transjeju 100M", total_distance_km=162, total_elevation_gain=6100,
                  race_date="2026-10-02", start_hour=21, start_minute=0, sport_type="trail",
                  result_json={"total_actual_s": 16 * 3600 + 53 * 60, "actual": []})
    db.add(route)
    await db.flush()
    return route


@pytest.fixture
def on_owner_day(monkeypatch):
    """The page's « today » is 7 Oct 2026 (the athlete's date in Korea)."""
    async def today(*a, **k):
        return D
    monkeypatch.setattr(sante, "athlete_today", today)


# ── routes ──────────────────────────────────────────────────────────────────

async def test_sante_requires_login(client: AsyncClient):
    r = await client.get("/sante")
    assert r.status_code == 307 and r.headers["location"] == "/"
    r = await client.get("/sante/sommeil?r=90")
    assert r.status_code == 307


async def test_nav_item_and_activities_link(as_user: AsyncClient):
    page = (await as_user.get("/activities")).text
    assert page.count('href="/sante"') == 2  # top bar and tab bar only: Activités no longer talks about health
    page = (await as_user.get("/sante")).text
    assert page.count('href="/sante" aria-current="page"') == 2


async def test_not_connected(as_user: AsyncClient):
    page = (await as_user.get("/sante")).text
    assert "Connecter COROS" in page and 'href="/settings#coros"' in page
    assert "/coros/connect" not in page and "Synchroniser maintenant" not in page  # connecting happens in Réglages
    assert 'role="tablist"' not in page


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


async def test_the_old_views_moved(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    for vue, where in (("entrainement", "/activities#semaines"), ("tendances", "/activities#fatigue")):
        r = await as_user.get(f"/sante?vue={vue}")
        assert r.status_code == 302 and r.headers["location"] == where  # the fold opens itself
    r = await as_user.get("/sante?vue=course")
    assert r.status_code == 302 and r.headers["location"] == "/activities"  # no race: Activités
    route = Route(user_id=test_user.id, name="Trail des Glaciers", total_distance_km=42,
                  race_date=(date.today() + timedelta(days=20)).isoformat(), start_hour=5, start_minute=0)
    db_session.add(route)
    await db_session.flush()
    r = await as_user.get("/sante?vue=course")
    assert r.status_code == 302 and r.headers["location"] == f"/simulator/routes/{route.id}#prep"


# ── the owner's own October ─────────────────────────────────────────────────

async def test_owner_october_aujourdhui(db_session: AsyncSession, test_user: User):
    route = await _seed_owner(db_session, test_user)
    page = await sante.health_page(db_session, test_user.id, today=D)
    a = page["auj"]
    v = a["verdict"]
    # R4: the 100-mile race took ≥ 10 h, J+5: easy only, intensity from J+10 (12/10)
    assert (v["rule"], v["tone"], v["headline"]) == ("race", "easy", "Footing facile seulement")
    assert v["text"] == "Pas d'intensité avant le 12/10." and len(v["text"]) <= 90
    assert v["chips"] == [{"glyph": "⚑", "word": "Transjeju 100M · J+5", "href": f"/simulator/routes/{route.id}#prep",
                           "aria": "Transjeju 100M, 5 jours après"}]
    # no short-night line on 7 Oct: 5h50 + nap 2h20 = 8h10 over 24 h
    assert "Nuit courte" not in str(v) and nt.day_tst24((await nt.load_nights(db_session, test_user.id, D))[0], D) == 490
    # the nightly tiles have no status: every night sits in J-7 → J+7; the sleep tile still shows its value
    assert [t["key"] for t in a["tiles"]] == ["sleep"]
    sleep = a["tiles"][0]
    assert (sleep["label"], sleep["value"], sleep["word"]) == ("Sommeil · 7 jours", "7h35", None)
    assert a["line"] == "2 nuits mesurées sur 7 : je juge sur tes séances et ton ressenti."
    assert not a["building"]  # said once (the line already explains), the count lives in Sommeil


async def test_owner_october_sommeil(db_session: AsyncSession, test_user: User):
    await _seed_owner(db_session, test_user)
    s = (await sante.health_page(db_session, test_user.id, today=D))["som"]
    # every night within 14 days: « 3 mois » would draw them again (UX13), and no « 1 an » yet
    assert s["state"] == "ok" and s["r"] == "14" and [k for k, _ in s["ranges"]] == ["14"]
    assert s["coverage"] == "5 nuits mesurées sur 14"  # 25/09 (a nap only) is never a night
    assert s["building"] == "Ta normale se construit : 0 nuit sur 7 hors course."  # provisional from 7 (H)
    c = s["nights"]
    col = c["cols"][-1]  # 7 Oct: the stacked bar and the in-axis nap after its 64-min gap
    assert col["night"] and col["nap"] and not col["short"] and len(col["nap_segs"]) == 1
    assert c["read"][1].replace(" ", " ") == "Nuit 5h50 · sieste 2h20 · 8h10 sur 24 h"
    assert c["read"][2].startswith("23:35 → 05:40 · sieste 06:40 → 09:05")
    assert s["hyps"] == []  # COROS: no stage timeline, no hypnogram
    assert s["amount_line"] is None and s["timing_line"] is None  # race nights: no regularity comment
    marks = {r["date"]: r["marks"] for r in s["rows"]}
    assert "fuseau changé" not in marks["mer. 7 oct."] and "autour de la course" in marks["mer. 7 oct."]
    assert [r["date"] for r in s["rows"]][:2] == ["mer. 7 oct.", "mar. 6 oct."]  # newest first
    assert s["heart"]["read"][1].replace(" ", " ") == "VFC 95 ms · FC 37 bpm"  # per-night values, no judgement


async def test_owner_october_korea_time_zone_tags(db_session: AsyncSession, test_user: User):
    """A session in France before the trip, one in Korea (UTC+9) after: the first nights in Korea are « fuseau
    changé » (the night it shows and the next 2, H), and say it in the nights' table."""
    from app.models.activity import Activity

    await _seed_owner(db_session, test_user)
    for i, (day, off) in enumerate(((date(2026, 9, 26), 7200), (date(2026, 9, 28), 32400))):
        db_session.add(Activity(user_id=test_user.id, strava_activity_id=9100 + i, sport_type="Run", name=f"r{i}",
                                start_date=datetime(day.year, day.month, day.day, 8, tzinfo=timezone.utc)
                                - timedelta(seconds=off), distance=8000, moving_time=2700, elapsed_time=2800,
                                raw_data={"utc_offset": off}))
    await db_session.flush()
    rows = {r["iso"]: r["marks"] for r in (await sante.health_page(db_session, test_user.id, today=D))["som"]["rows"]}
    assert "◇ fuseau changé" in rows["2026-09-29"] and "◇ fuseau changé" in rows["2026-09-30"]
    assert "fuseau" not in rows["2026-10-06"] and "fuseau" not in rows["2026-10-07"]


async def test_a_short_night_turns_the_sleep_tile_to_24_hours(db_session: AsyncSession, test_user: User):
    from tests.test_nights import night_rows

    rows = night_rows(range(1, 20), asleep=450)
    rows["sleep"].update(night_rows([0], asleep=290, start=(23, 50), end=(6, 40))["sleep"])  # 4h50, no nap
    for metric, per_day in rows.items():
        for d, (v, det, src) in per_day.items():
            _add(db_session, test_user, metric, d, v, det, src)
    await db_session.flush()
    a = (await sante.health_page(db_session, test_user.id, today=D))["auj"]
    # a usual wake-up (06:40): not an early wake, the ladder goes on, the slot says it; 13 untagged nights make
    # a « provisoire » normal (H), in which HR and HRV sit: « séance prévue »
    assert a["verdict"]["rule"] == "plan" and a["verdict"]["text"] == sante_today.SHORT_LATE
    assert "sleep" in a["verdict"]["drivers"]
    assert {t["key"]: t["word"] for t in a["tiles"]}.get("hr") == "dans ta normale (provisoire)"
    tile = next(t for t in a["tiles"] if t["key"] == "sleep")
    assert (tile["label"], tile["value"], tile["glyph"], tile["word"]) == ("Sommeil · 24 h", "4h50", "▼", "moins de 6 h")
    assert a["tiles"][0]["key"] == "sleep"  # the driver first


async def test_owner_page_html(as_user: AsyncClient, db_session: AsyncSession, test_user: User, on_owner_day):
    await _link(db_session, test_user, last_sync_at=datetime.now(timezone.utc) - timedelta(minutes=25))
    await _seed_owner(db_session, test_user)
    page = (await as_user.get("/sante")).text
    assert page.count('role="tab"') == 2 and "Aujourd&#39;hui" in page and ">Sommeil<" in page
    for gone in ("Entraînement", "Tendances", ">Course<"):
        assert gone not in page, gone
    for brand in BRAND:
        assert brand not in page, brand
    assert "COROS</b> · synchro il y a 25 min" in page and "cette nuit : reçue" not in page
    assert 'aria-live="polite" aria-labelledby="h-today"' in page and "Footing facile seulement" in page
    assert 'class="pf-dchip" href="/simulator/routes/' in page
    assert re.search(r'<section id="sommeil"[^>]*hidden', page)  # server-rendered, the other view hidden
    assert re.search(r'src="/static/js/pf-viz.js\?v=\w*"', page)
    # Sommeil: the figures, then the two closed folds; numbers once (the 7-day mean is Aujourd'hui's)
    som = (await as_user.get("/sante?vue=sommeil")).text
    assert re.search(r'<section id="aujourdhui"[^>]*hidden', som) and 'id="nuits"' in som and 'id="coeur"' in som
    assert "<summary>Les chiffres de chaque nuit</summary>" in som and "<summary>Comment je lis tes nuits</summary>" in som
    assert "<details open" not in som
    assert som.count("7h35") == 1  # the sleep tile's mean, printed once in the whole page
    assert "Horaires détectés par la montre, approximatifs." in som
    assert 'data-viz-group="sommeil"' in som and 'hx-get="/sante/sommeil?r=90"' not in som  # one range: no toggle


async def test_the_range_swaps_in_place(as_user: AsyncClient, db_session: AsyncSession, test_user: User,
                                        on_owner_day):
    from tests.test_nights import night_rows

    await _link(db_session, test_user)
    await _seed_owner(db_session, test_user)
    alone = await as_user.get("/sante/sommeil?r=90", headers={"HX-Request": "true"})
    assert "3 mois" not in alone.text and "5 nuits mesurées sur 14" in alone.text  # nothing older: 14 nuits only
    for metric, per_day in night_rows(range(30, 33), source="COROS", hr_method="coros_sleep_summary").items():
        for d, (v, det, src) in per_day.items():
            _add(db_session, test_user, metric, d, v, det, src)
    await db_session.flush()
    r = await as_user.get("/sante/sommeil?r=90", headers={"HX-Request": "true"})
    assert r.status_code == 200 and r.text.lstrip().startswith("{#") is False
    assert '<div id="sommeil-range" data-viz-scope>' in r.text and re.search(r'aria-pressed="true"[^>]*>3 mois', r.text)
    assert "8 nuits mesurées sur 3 mois" in r.text
    assert 'id="range-90"' in r.text and 'id="range-14"' in r.text  # htmx gives the pressed button its focus back
    r = await as_user.get("/sante/sommeil?r=90")  # without htmx: the full page for that range
    assert r.status_code == 303 and r.headers["location"] == "/sante?vue=sommeil&r=90"
    page = (await as_user.get("/sante?vue=sommeil&r=90")).text
    assert re.search(r'aria-pressed="true"[^>]*>3 mois', page)


async def test_the_check_in_swaps_the_view_and_moves_the_decision(as_user: AsyncClient, db_session: AsyncSession,
                                                                  test_user: User, on_owner_day):
    await _link(db_session, test_user)
    for k in range(1, 8):  # a few nights, no race: the default rung
        _add(db_session, test_user, "sleep", D - timedelta(days=k), 450, {"bedtime": "23:00", "wake": "07:00"})
    await db_session.flush()
    page = (await as_user.get("/sante")).text
    assert "Ce matin ?" in page and 'id="why-legs"' not in page
    # « moins bien »: the reasons appear, the decision is swapped into the live region
    r = await as_user.post("/sante/feel", data={"feel": "3"}, headers={"HX-Request": "true"})
    assert r.status_code == 200 and r.text.lstrip().startswith('<div id="sante-today-rest">')
    assert '<div id="sante-decision" hx-swap-oob="innerHTML">' in r.text and "Garde ta séance facile" in r.text
    assert 'id="why-legs"' in r.text and "<details class=\"pf-feel-fold\" open>" in r.text
    r = await as_user.post("/sante/feel", data={"toggle": "legs"}, headers={"HX-Request": "true"})
    assert "Endurance facile aujourd&#39;hui" in r.text and 'id="why-legs" name="toggle" value="legs" class="pf-chip" ' \
        'aria-pressed="true"' in r.text
    rows = (await db_session.execute(select(HealthMetric).where(
        HealthMetric.user_id == test_user.id, HealthMetric.metric == "feel"))).scalars().all()
    assert len(rows) == 1 and rows[0].value == 3
    assert rows[0].details == {"why": ["legs"], "alcohol": False, "legs_heavy": True}
    page = (await as_user.get("/sante")).text  # folded once answered
    assert "Noté : moins bien · jambes" in page and "— modifier" in page and "Ce matin ?" not in page
    # without scripts: a plain form, back to the page
    r = await as_user.post("/sante/feel", data={"toggle": "legs"})
    assert r.status_code == 303 and r.headers["location"] == "/sante"
    r = await as_user.post("/sante/feel", data={"feel": "3", "why": ["sick", "fatigue", "nope"], "alcohol": "1"})
    await db_session.refresh(rows[0])
    assert rows[0].details == {"why": ["fatigue", "sick"], "alcohol": True, "legs_heavy": False}
    assert "Pas d&#39;intensité aujourd&#39;hui" in (await as_user.get("/sante")).text  # « malade »
    await as_user.post("/sante/feel", data={"feel": "1"})  # « mieux »: no reason left, the alcohol chip stays
    await db_session.refresh(rows[0])
    assert rows[0].value == 1 and rows[0].details == {"why": [], "alcohol": True, "legs_heavy": False}


async def test_alcohol_alone_is_not_a_reply(as_user: AsyncClient, db_session: AsyncSession, test_user: User,
                                            on_owner_day):
    await _link(db_session, test_user)
    _add(db_session, test_user, "sleep", D - timedelta(days=1), 450, {"bedtime": "23:00", "wake": "07:00"})
    await db_session.flush()
    r = await as_user.post("/sante/feel", data={"alcohol": "1"}, headers={"HX-Request": "true"})
    assert "Ce matin ?" in r.text and 'id="feel-alcohol" name="alcohol" value="0" class="pf-chip pf-chip-ctx" ' \
        'aria-pressed="true"' in r.text
    assert 'id="feel-2" name="feel" value="2" class="pf-chip" aria-pressed="false"' in r.text
    row = (await db_session.execute(select(HealthMetric).where(HealthMetric.metric == "feel"))).scalar_one()
    assert row.details["answered"] is False and row.details["alcohol"] is True


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
    # while it runs, the status keeps waiting
    conn.sync_claimed_at = datetime.now(timezone.utc)
    await db_session.flush()
    r = await as_user.get(url)
    assert "Mise à jour de tes données…" in r.text and "n=1" in r.text and "HX-Refresh" not in r.headers
    # done but nothing new: no reload, the usual button
    conn.sync_claimed_at = None
    conn.last_sync_at = datetime.now(timezone.utc)
    await db_session.flush()
    r = await as_user.get(url)
    assert "HX-Refresh" not in r.headers and "Synchroniser maintenant" in r.text
    # a sync stuck « en cours » (a crashed worker): the page stops waiting after 2 min
    conn.sync_claimed_at = datetime.now(timezone.utc)
    await db_session.flush()
    r = await as_user.get(url.replace("n=0", "n=40"))
    assert "Synchroniser maintenant" in r.text and "sync-status" not in r.text
    conn.sync_claimed_at = None
    # done with a new night: one reload
    _add(db_session, test_user, "sleep", date.today(), 450, {"bedtime": "23:00", "wake": "07:00"})
    await db_session.flush()
    r = await as_user.get(url)
    assert r.headers.get("HX-Refresh") == "true"
    # synced within the hour: no new sync, the usual button
    started.clear()
    page = (await as_user.get("/sante")).text
    assert started == [] and "Synchroniser maintenant" in page
    # stale but failed last time: no automatic retry (the button says why)
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
    # last night isn't there yet: synced 30 min ago is already too old, and the header says it
    page = (await as_user.get("/sante")).text
    assert started == [test_user.id] and "cette nuit pas encore reçue" in page
    # it arrived: no new sync within the hour
    started.clear()
    db_session.add(HealthMetric(user_id=test_user.id, date=today, metric="sleep", value=440,
                                source="COROS", n_samples=1, details={"bedtime": "23:10", "wake": "06:50"}))
    await db_session.flush()
    await as_user.get("/sante")
    assert started == []
    # missing again but synced 5 min ago: no retry yet
    conn.last_sync_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    await db_session.execute(delete(HealthMetric).where(HealthMetric.date == today))
    await db_session.flush()
    await as_user.get("/sante")
    assert started == []


# ── the decision ladder ─────────────────────────────────────────────────────

T = date(2026, 10, 6)  # a Tuesday


def _ctx(**kw):
    base = {"today": T, "next_race": None, "post": None, "feel": None, "alert": None, "reprise": None, "short": None,
            "legs": {"big": None}, "hr": None, "hrv": None, "easy": None, "sessions42": 8, "has_watch": True,
            "has_sessions": True}
    return {**base, **kw}


def _feel(value=3, why=(), alcohol=False, answered=True):
    return {"value": value, "why": list(why), "alcohol": alcohol, "answered": answered}


def _session(day, minutes, dplus=0, sid=7, sport="TrailRun", workout_type=0):
    return Session(id=sid, start=datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc), day=day,
                   sport=sport, minutes=minutes, dplus=dplus, km=minutes / 6, speed=2.5, hr=140, hr_peak=170,
                   suffer=None, workout_type=workout_type, temp=None)


ALERT = {"days": [T - timedelta(days=1), T], "values": [52, 53], "threshold": 50, "resp_up": None}


def test_every_sentence_is_one_and_short():
    """At most one sentence under the headline, ≤ 90 characters — except the
    alert's, the brief's own wording (it replaces « tu es malade »)."""
    cases = [_ctx(next_race={"days": d, "name": "Marathon", "href": "/r#prep"}) for d in (0, 1, 2, 5)]
    cases += [_ctx(feel=_feel(why=["sick"])), _ctx(feel=_feel()), _ctx(feel=_feel(why=["legs"])),
              _ctx(short={"early": True, "tip": "16:00"}), _ctx(sessions42=0, has_sessions=False, has_watch=False),
              _ctx(reprise={"since": T, "days": 3, "cause": "malade", "see_doctor": False,
                            "gates": {"no_sick": True, "easy_hr": False, "night_hr": True}})]
    for c in cases:
        v = decide(c)
        assert v["text"] is None or (len(v["text"]) <= 90 and v["text"].count(". ") == 0), v["text"]
        assert len(v["chips"]) <= 2


def test_r1_race_in_0_to_2_days():
    for days, head in ((0, "Jour de course"), (1, "Course demain : repos ou 20 min faciles"),
                       (2, "Course après-demain : court et facile")):
        v = decide(_ctx(next_race={"days": days, "name": "Marathon de Paris", "href": "/simulator/routes/3#prep"}))
        assert (v["headline"], v["tone"], v["rule"]) == (head, "ok", "race")
        assert v["chips"][0]["href"] == "/simulator/routes/3#prep" and v["chips"][0]["glyph"] == "⚑"
    assert decide(_ctx(next_race={"days": 0, "name": "Semi"}))["chips"][0]["word"] == "Semi · jour J"


def test_r2_illness_the_chip_or_the_alert_even_in_race_week():
    v = decide(_ctx(feel=_feel(why=["sick", "fatigue"])))
    assert (v["tone"], v["headline"], v["rule"]) == ("rest", "Pas d'intensité aujourd'hui", "ill")
    assert v["text"] == "Repos tant que tu as de la fièvre ou des courbatures partout."
    v = decide(_ctx(alert=ALERT, next_race={"days": 5, "name": "UTMB", "href": "/r#prep"}))
    assert v["rule"] == "ill" and v["tone"] == "rest" and v["drivers"] == ["hr"]
    # the brief's wording (Altini & Plews 2021; Quer 2021), never « tu es malade »
    assert v["headline"] + " · " + v["text"] == (
        "Pas d'intensité aujourd'hui · FC de nuit nettement au-dessus de ta normale 2 nuits de suite : ça arrive "
        "avant un rhume, après de l'alcool ou une grosse journée.")
    assert v["chips"][0]["href"] == "/sante?vue=sommeil#coeur" and "malade" not in v["text"]


def test_r3_reprise_and_the_doctor_after_2_weeks():
    rp = {"since": T - timedelta(days=4), "days": 4, "cause": "malade", "see_doctor": False,
          "gates": {"no_sick": True, "easy_hr": False, "night_hr": True}}
    v = decide(_ctx(reprise=rp))
    assert (v["headline"], v["tone"]) == ("Reprise en douceur", "easy")
    assert v["text"] == "Footings faciles ; l'intensité quand ta FC en footing est revenue."
    assert [c["word"] for c in v["chips"]] == ["FC en footing · 14 j"] and "easy" in v["drivers"]
    v = decide(_ctx(reprise={**rp, "days": 15, "see_doctor": True}))
    assert v["text"] == "Toujours pas reparti après 2 semaines : vois un médecin."


def test_r4_after_the_race_intensity_from_j10_after_10_hours():
    route = Route(id=11, name="Transjeju 100M", race_date="2026-10-02", result_json={"total_actual_s": 60780})
    for today, head in ((date(2026, 10, 3), "Récupère"), (date(2026, 10, 5), "Récupère"),
                        (D, "Footing facile seulement"), (date(2026, 10, 11), "Footing facile seulement")):
        post = sante._post_race(route, [], today)
        assert post["free"] == date(2026, 10, 12) and post["race_name"] == "Transjeju 100M"
        v = decide(_ctx(today=today, post={**post, "href": "/simulator/routes/11#prep"}))
        assert (v["headline"], v["text"]) == (head, "Pas d'intensité avant le 12/10.")
    assert sante._post_race(route, [], date(2026, 10, 12)) is None  # J+10: intensity is back
    short = Route(id=12, name="10 km", race_date="2026-10-04", result_json={"total_actual_s": 2700})
    post = sante._post_race(short, [], T)
    assert post["free"] == date(2026, 10, 7) and decide(_ctx(post=post))["headline"] == "Footing facile seulement"
    # an exceptional outing (not a Route): its own chip, to the activity
    big = _session(T - timedelta(days=2), 420, 3000, sid=99)
    post = sante._post_race(None, [_session(T - timedelta(days=20), 150, sid=1), big], T)
    v = decide(_ctx(post=post))
    assert v["chips"][0] == {"glyph": "◆", "word": "sortie de dim.", "href": "/activity/99", "aria": "sortie de dim., 7h00"}


def test_r5_short_night_early_wake_or_the_ladder_goes_on():
    v = decide(_ctx(short={"early": True, "tip": "15:55"}))
    assert (v["headline"], v["tone"], v["drivers"]) == ("Séance dure ce matin, sinon facile", "easy", ["sleep"])
    assert v["text"] == "Une sieste de 20 à 90 min avant 15:55 aide ; laisse 30 min avant de courir."
    assert v["chips"][0]["href"] == "/sante?vue=sommeil#nuits"
    v = decide(_ctx(short={"early": False, "tip": "16:00"}))  # late bedtime or neither: softer, the plan stands
    assert v["headline"] == "Séance prévue : rien ne s'y oppose"
    assert v["text"] == "Nuit courte : place ta séance dure plutôt le matin." and v["drivers"] == ["sleep"]
    v = decide(_ctx(short={"early": False, "tip": "16:00"}, feel=_feel()))  # a rung with its own sentence keeps it
    assert v["rule"] == "feel" and [c["word"] for c in v["chips"]] == ["Sommeil · 24 h"]


def test_r5_reads_the_24_hour_total_of_any_main_episode():
    from tests.test_nights import night_rows

    rows = night_rows([0], asleep=160, start=(23, 50), end=(2, 30))  # a 2h40 night (travel): the rule fires
    nights = nt.build_nights(rows, D)
    assert nt.day_tst24(nights, D) == 160 < nt.SHORT_DAY_MIN
    usual = sante._usual(nights, D - timedelta(days=1))
    assert usual["wake"] is None and nt.early_wake(nights[D], usual)  # no median: an early wake (H)
    nap_only = nt.build_nights({"nap": {D: (82, {"windows": [["2026-10-07T01:23", "2026-10-07T02:52"]]}, "COROS")}},
                               D)
    assert nt.day_tst24(nap_only, D) is None  # no main episode: unknown, no rule


def test_r6_legs_after_3_hours_or_1500_m_is_a_heuristic():
    big = _session(T - timedelta(days=1), 250, 2100)
    v = decide(_ctx(legs={"big": big}))
    assert (v["headline"], v["rule"], v["tone"]) == ("Endurance facile aujourd'hui", "legs", "easy")
    assert v["text"] == "Séance dure possible demain." and "fatigue" not in v["text"]
    assert decide(_ctx(legs={"big": _session(T - timedelta(days=1), 320, 2600)}))["text"] == "Séance dure possible jeudi."
    assert v["chips"][0] == {"glyph": "◆", "word": "sortie d'hier", "href": "/activity/7", "aria": "sortie d'hier, 4h10"}
    v = decide(_ctx(feel=_feel(why=["legs"])))
    assert v["rule"] == "legs" and v["text"] == "Jambes lourdes : c'est toi qui sais, on regarde demain."
    assert sante_today.LEGS_MIN == 180 and sante_today.LEGS_DPLUS == 1500


def test_r7_hrv_below_needs_nightly_hr_up_or_feeling_worse():
    v = decide(_ctx(hrv="below", hr="above"))
    assert (v["headline"], v["rule"], v["drivers"]) == ("Garde ta séance facile", "hrv", ["hrv", "hr"])
    assert [c["word"] for c in v["chips"]] == ["VFC · 7 nuits", "FC de nuit · 7 nuits"]
    assert decide(_ctx(hrv="below", feel=_feel(why=["stress"])))["rule"] == "hrv"
    # a single signal is shown on its tile, never moves the action
    assert decide(_ctx(hrv="below", hr="in"))["rule"] == "plan"
    assert decide(_ctx(hr="above", hrv="in"))["rule"] == "plan"
    # race week: never
    v = decide(_ctx(hrv="below", hr="above", next_race={"days": 5, "name": "UTMB", "href": "/r#prep"}))
    assert v["rule"] == "race_week" and v["headline"] == "Semaine de course : séance prévue, sans en rajouter"


def test_r8_r9_feeling_worse_is_the_athletes_own_call():
    v = decide(_ctx(feel=_feel(why=["fatigue"]), easy={"flag": True}))
    assert (v["rule"], v["text"]) == ("easy_hr", "Cœur plus haut en footing et tu te sens moins bien.")
    assert v["chips"][0]["href"] == "/activities#fc-facile"
    assert decide(_ctx(easy={"flag": True}))["rule"] == "plan"  # the flag alone: shown, no move
    v = decide(_ctx(feel=_feel()))
    assert (v["rule"], v["text"]) == ("feel", "Tu te sens moins bien : c'est toi qui sais, on regarde demain.")
    assert decide(_ctx(feel=_feel(value=1)))["rule"] == "plan"  # « mieux » never lifts anything either
    assert decide(_ctx(feel=_feel(answered=False)))["rule"] == "plan"


def test_r10_to_r12_race_week_default_nothing():
    v = decide(_ctx(next_race={"days": 6, "name": "Marathon", "href": "/simulator/routes/4#prep"}))
    assert v["rule"] == "race_week" and v["chips"][0]["word"] == "Marathon · J−6" and v["text"] is None
    v = decide(_ctx())
    assert (v["headline"], v["tone"], v["text"], v["glyph"]) == ("Séance prévue : rien ne s'y oppose", "ok", None, "●")
    v = decide(_ctx(sessions42=2, hrv="in", hr="in"))  # nights in the normal are enough
    assert v["rule"] == "plan"
    v = decide(_ctx(sessions42=2))
    assert (v["headline"], v["text"], v["glyph"]) == ("Pas encore d'avis", "Il me faut 6 séances sur 6 semaines.", "?")
    v = decide(_ctx(sessions42=0, has_sessions=False, has_watch=False))
    assert v["text"] == "Connecte Strava ou ta montre dans Réglages."


# ── tiles ───────────────────────────────────────────────────────────────────

def _stat(label, text, status=None, band=(40, 46)):
    return {"label": label, "text": text, "unit": "bpm", "spoken": f"{text} battements par minute", "status": status,
            "means": [None] * 10 + [44, 45, 46, 47 if text else None], "band": band, "href": "/sante?vue=sommeil#coeur"}


def test_tiles_drivers_first_then_out_of_band_three_at_most():
    stats = {"hr": _stat("FC de nuit · 7 nuits", "47", "above"), "hrv": _stat("VFC · 7 nuits", "60", "in"),
             "sleep": _stat("Sommeil · 7 jours", "7h01", "below")}
    tiles = make_tiles(stats, ["hrv"], False)
    assert [t["key"] for t in tiles] == ["hrv", "hr", "sleep"] and tiles[0]["driver"]
    assert (tiles[1]["glyph"], tiles[1]["word"], tiles[1]["tone"]) == ("▲", "au-dessus", "warn")
    assert (tiles[2]["glyph"], tiles[2]["word"]) == ("▼", "plus court")
    assert tiles[0]["word"] == "dans ta normale" and tiles[0]["aria"].endswith("Facteur de la décision")
    # FC en footing, flagged, takes the last slot
    stats["easy"] = {**_stat("FC en footing · 14 j", "+4"), "word": ("▲", "à surveiller", "warn"), "flag": True}
    assert [t["key"] for t in make_tiles(stats, [], False)] == ["hr", "sleep", "easy"]
    # without a band: no status word
    stats = {"hr": _stat("FC de nuit · 7 nuits", "47", None, None), "hrv": _stat("VFC · 7 nuits", None, None, None)}
    tiles = make_tiles(stats, [], False)
    assert [t["key"] for t in tiles] == ["hr"] and tiles[0]["word"] is None and tiles[0]["band"] is None


def test_the_missing_nightly_tiles_say_why_once():
    assert sparse_line([], {"measured": 7}) is None
    assert sparse_line(["hr", "hrv"], {"measured": 0}) == ("Pas de nuit mesurée ces 7 jours : je juge sur tes séances et "
                                                           "ton ressenti.")
    assert sparse_line(["hr", "hrv"], {"measured": 4, "race": 4}) == ("Nuits autour de ta course : FC et VFC de nuit "
                                                                      "pas jugées.")
    assert sparse_line(["hr"], {"measured": 5, "race": 0, "nap": 3}) == ("FC de nuit des jours avec sieste : pas "
                                                                         "encore comptée (COROS).")
    assert sparse_line(["hrv"], {"measured": 5, "race": 0, "tag": "fuseau changé"}) == ("Nuits ◇ fuseau changé : VFC "
                                                                                        "pas jugée.")


def test_easy_tile_reads_the_rule_activites_draws_and_prints_its_number_once():
    """« FC en footing · 14 j » and Activités › FC en footing read one model
    (sante_training.easy_watch): same « à surveiller », the bpm on the tile only."""
    from app.services import sante_training as st
    from app.services import training_view as tv

    def run(k, hr, sid):
        d = D - timedelta(days=k)
        return Session(id=sid, start=datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc), day=d, sport="Run",
                       minutes=50, dplus=20, km=10, speed=3.3, hr=hr, hr_peak=180, suffer=None, workout_type=0,
                       temp=15)
    base = [run(k, 140, k) for k in range(8, 120, 3)]
    up = base + [run(5, 144, 100), run(2, 143.5, 101)]
    t = sante_today.easy_stats(st.easy_model(up, D, 190), D, reprise=False)
    a3 = tv.footing(up, D, 190)
    assert t["flag"] and a3["flagged"]  # the two pages agree
    assert t["text"] == "+4" and t["word"][1] == "à surveiller" and t["means"][-1] == pytest.approx(3.75)
    assert "+4" not in a3["line"] and "bpm" not in a3["line"]  # the number is the tile's
    ok = base + [run(5, 144, 100), run(2, 141, 101)]
    assert not sante_today.easy_stats(st.easy_model(ok, D, 190), D, reprise=False)["flag"]
    assert not tv.footing(ok, D, 190)["flagged"]
    assert sante_today.easy_stats(st.easy_model(base[:5], D, 190), D, reprise=False) is None  # under 8 runs
    gap = sante_today.easy_stats(None, D, reprise=True)  # « Reprise » without a footing: the tile says so
    assert gap["text"] is None and gap["gap"] == "pas de footing mesuré"


async def test_dedupe_matches_activites_when_strava_writes_json_null(db_session: AsyncSession, test_user: User):
    from app.models.activity import Activity
    from app.services import sante_training as st

    t = datetime(2026, 10, 1, 7, tzinfo=timezone.utc)
    for i, (dt, km, sec, splits) in enumerate([(0, 10.0, 3000, [{"split": 1}]), (60, 10.2, 3300, None)]):
        db_session.add(Activity(user_id=test_user.id, strava_activity_id=7000 + i, sport_type="Run", name=f"r{i}",
                                start_date=t + timedelta(seconds=dt), distance=km * 1000, moving_time=sec,
                                elapsed_time=sec, raw_data={}, splits_metric=splits))
    await db_session.flush()
    ss = await st.load_sessions(db_session, test_user.id, date(2026, 10, 6))
    assert [s.minutes for s in ss] == [50]  # the copy with splits, as Activités keeps it


# ── review fixes (v3) ───────────────────────────────────────────────────────

def test_r2_comes_before_r1_malade_or_the_alert_in_the_last_2_days_before_a_race():
    """F5: « malade » at J-2, the alert at J-1: no accelerations, the race chip stays."""
    race = {"days": 2, "name": "Marathon", "href": "/simulator/routes/3#prep"}
    v = decide(_ctx(next_race=race, feel=_feel(why=["sick"])))
    assert (v["rule"], v["tone"], v["headline"]) == ("ill", "rest", "Pas d'intensité aujourd'hui")
    assert "accélérations" not in (v["text"] or "") and v["chips"][-1]["word"] == "Marathon · J−2"
    v = decide(_ctx(next_race={**race, "days": 1}, alert=ALERT))
    assert v["rule"] == "ill" and "accélérations" not in v["text"]
    assert [c["word"] for c in v["chips"]] == ["FC de nuit · 2 nuits", "Marathon · J−1"]
    assert decide(_ctx(next_race=race))["rule"] == "race"  # without them, R1 as before


def _nights_of(rows):
    from app.services import nights as nt_
    return nt_.build_nights(rows, D)


def test_illness_nights_never_drive_r7_once_reprise_closed():
    """F7 / L-F6: « malade » D-6 and D-5, sick nights D-6 → D-2 (51 bpm, 44 ms),
    back to 46 / 59 on D-1 and D: Reprise closed, the tiles' status (what
    decides) leaves the episode out, so R7 does not fire."""
    from tests.test_nights import night_rows

    rows = night_rows(range(7, 61), hr=45.0, hrv=lambda k: 57 + k % 7)
    sick_rows = night_rows(range(2, 7), hr=51.0, hrv=44.0)
    back = night_rows([0, 1], hr=46.0, hrv=59.0)
    nights = _nights_of({m: {**rows[m], **sick_rows[m], **back[m]} for m in rows})
    feel = {D - timedelta(days=6): _feel(why=["sick"]), D - timedelta(days=5): _feel(why=["sick"])}
    nt.tag_nights(nights, (), [], feel)
    nt.tag_alerts(nights, D)
    assert nt.reprise(nights, feel, D) is None
    a = sante._today_view(nights, feel, [], 190, None, None, [], D, True)
    assert a["verdict"]["rule"] != "hrv" and "Garde ta séance facile" not in a["verdict"]["headline"]
    assert all(t["word"] not in ("au-dessus", "en dessous") for t in a["tiles"])
    # during the episode (R3) the tiles still show its nights, never deciding on them
    during = sante._today_view(nights, feel, [], 190, None, None, [], D - timedelta(days=2), True)
    assert during["verdict"]["rule"] in ("reprise", "ill")
    assert any(t["key"] == "hr" and t["value"] for t in during["tiles"])


def test_the_hr_tile_is_judged_on_the_unrounded_mean():
    """F8 / L-F4: band median 44.5 (top 47.5), 7-night mean 46.6: « dans ta
    normale », never « au-dessus » by rounding; R7 does not fire on it."""
    from tests.test_nights import night_rows

    rows = night_rows(range(7, 61), hr=lambda k: 44.0 + k % 2)
    last = night_rows(range(0, 7), hr=lambda k: 47.0 if k % 3 else 46.0)
    nights = _nights_of({m: {**rows[m], **last[m]} for m in rows})
    st = sante._night_stats(nights, "hr", D)
    assert st["text"] == "47" and 46.5 < st["value"] < 47 and st["status"] == "in"
    assert decide(_ctx(hrv="below", hr=st["status"]))["rule"] == "plan"


def test_a_rendormi_morning_is_no_early_wake():
    """F9: main sleep 23:30 → 04:30 (4h50), back asleep 05:30 → 06:20: R5 fires
    on the 24-h total, never as an early wake (no « Séance dure ce matin », no nap tip)."""
    from tests.test_nights import night_rows

    rows = night_rows(range(1, 30), start=(23, 0), end=(7, 0), asleep=450)
    rows["sleep"][D] = (290, {"main_start": "2026-10-06T23:30", "main_end": "2026-10-07T04:30"}, "Garmin")
    rows["nap"][D] = (45, {"windows": [["2026-10-07T05:30", "2026-10-07T06:20"]]}, "Garmin")
    nights = _nights_of(rows)
    assert nights[D].resettled and nt.day_tst24(nights, D) == 335
    a = sante._today_view(nights, {}, [], 190, None, None, [], D, True)
    v = a["verdict"]
    assert v["headline"] != "Séance dure ce matin, sinon facile" and "sieste" not in (v["text"] or "")
    assert v["text"] == sante_today.SHORT_LATE and "sleep" in v["drivers"]


def test_one_morning_has_one_24_hour_total():
    """L-F8: yesterday's 15:00 nap counts for R5 (day_tst24) but the tile prints
    the total Sommeil draws for that day (Night.tst24): one number, once."""
    from tests.test_nights import night_rows

    rows = night_rows(range(1, 20), asleep=450)
    rows["sleep"][D] = (240, {"main_start": "2026-10-07T01:00", "main_end": "2026-10-07T05:30"}, "Garmin")
    rows["nap"][D - timedelta(days=1)] = (30, {"windows": [["2026-10-06T15:00", "2026-10-06T15:30"]]}, "Garmin")
    nights = _nights_of(rows)
    assert nt.day_tst24(nights, D) == 270 and nights[D].tst24 == 240
    a = sante._today_view(nights, {}, [], 190, None, None, [], D, True)
    tile = next(t for t in a["tiles"] if t["key"] == "sleep")
    assert (tile["label"], tile["value"]) == ("Sommeil · 24 h", "4h00")
    from app.services import sante_sleep as sl
    s = sl.sleep_view(nights, D, "14")
    assert s["nights"]["read"][1] == "Nuit 4h00 sur 24 h"  # the same number on Sommeil


async def test_a_race_marked_on_strava_only_is_a_race_for_the_nights(db_session: AsyncSession, test_user: User):
    """F4: a marathon logged as a race on Strava (no Route) 3 days ago: its
    recovery nights are « autour de la course », never the illness alert; R4."""
    from app.models.activity import Activity
    from tests.test_nights import night_rows

    base = night_rows(range(4, 70), hr=lambda k: 47 + k % 3, hrv=82.0)
    after = night_rows([0, 1, 2], hr=lambda k: {2: 60.0, 1: 56.0, 0: 55.0}[k], hrv=60.0)
    for metric in base:
        for d, (v, det, src) in {**base[metric], **after[metric]}.items():
            _add(db_session, test_user, metric, d, v, det, src)
    db_session.add(Activity(user_id=test_user.id, strava_activity_id=4242, sport_type="Run", name="Marathon de Lyon",
                            start_date=datetime(2026, 10, 4, 6, tzinfo=timezone.utc), distance=42195,
                            moving_time=200 * 60, elapsed_time=200 * 60, average_heartrate=160,
                            raw_data={"workout_type": 1, "utc_offset": 7200}))
    await db_session.flush()
    page = await sante.health_page(db_session, test_user.id, today=D)
    v = page["auj"]["verdict"]
    assert v["rule"] == "race" and v["headline"] == "Récupère"
    marks = {r["iso"]: r["marks"] for r in page["som"]["rows"]}
    assert all("autour de la course" in marks[(D - timedelta(days=k)).isoformat()] for k in range(3))
    shown = str({**page, "auj": {k: x for k, x in page["auj"].items() if k != "method"}})  # the fold names « malade »
    assert "malade" not in shown and "FC de nuit haute" not in shown


async def test_the_old_course_link_keeps_the_recovery_first(as_user: AsyncClient, db_session: AsyncSession,
                                                            test_user: User):
    """R3: a race run 5 days ago and the next one 39 or 145 days out: the old
    Course view led with the recovery; « #prep » only where the page shows it."""
    today = date.today()

    async def race(days, name):
        r = Route(user_id=test_user.id, name=name, total_distance_km=42, start_hour=8, start_minute=0,
                  race_date=(today + timedelta(days=days)).isoformat())
        db_session.add(r)
        await db_session.flush()
        return r
    last = await race(-5, "Marathon d'automne")
    far = await race(145, "Ultra de printemps")
    r = await as_user.get("/sante?vue=course")
    assert r.headers["location"] == f"/simulator/routes/{last.id}#prep"
    await db_session.delete(last)
    await db_session.flush()
    r = await as_user.get("/sante?vue=course")
    assert r.headers["location"] == f"/simulator/routes/{far.id}"  # J-145: no preparation shown there yet
    last = await race(-5, "Marathon d'automne")
    near = await race(10, "Semi de novembre")
    r = await as_user.get("/sante?vue=course")
    assert r.headers["location"] == f"/simulator/routes/{near.id}#prep"  # 14 days or less: the next race first


async def test_a_check_in_whose_redraw_fails_reloads_the_page(as_user: AsyncClient, db_session: AsyncSession,
                                                              test_user: User, on_owner_day, monkeypatch):
    """R5: the answer is saved; a failing redraw answers HX-Refresh (never a 500
    htmx would not swap), so a second tap can never silently undo it."""
    await _link(db_session, test_user)
    _add(db_session, test_user, "sleep", D - timedelta(days=1), 450, {"bedtime": "23:00", "wake": "07:00"})
    await db_session.flush()
    real = sante.health_page

    async def boom(db, user_id, *a, parts=("today", "sleep"), **k):
        if parts == ("today",):
            raise RuntimeError("redraw")
        return await real(db, user_id, *a, parts=parts, **k)
    monkeypatch.setattr("app.routers.sante.health_page", boom)
    r = await as_user.post("/sante/feel", data={"feel": "3"}, headers={"HX-Request": "true"})
    assert r.status_code == 200 and r.headers.get("HX-Refresh") == "true"
    r = await as_user.post("/sante/feel", data={"toggle": "legs"}, headers={"HX-Request": "true"})
    assert r.headers.get("HX-Refresh") == "true"
    row = (await db_session.execute(select(HealthMetric).where(HealthMetric.metric == "feel"))).scalar_one()
    assert row.value == 3 and row.details["why"] == ["legs"]


async def test_the_hypnograms_are_read_for_the_last_90_days_only(db_session: AsyncSession, test_user: User):
    """R4: the stage intervals of older nights are never fetched, each interval
    goes to its own night, and « 1 an » reads none."""
    from app.models.health import HealthSample
    from tests.test_nights import night_rows

    rows = night_rows(range(0, 200))
    nights = _nights_of(rows)
    for k in (0, 1, 150):
        n = nights[D - timedelta(days=k)]
        for j, kind in enumerate(("core", "deep", "rem")):
            a = n.start + timedelta(hours=j)
            db_session.add(HealthSample(user_id=test_user.id, metric="sleep", source="Garmin", kind=kind, start_at=a,
                                        end_at=a + timedelta(minutes=50), value=50))
    await db_session.flush()
    got = await sante._timelines(db_session, test_user.id, nights, D)
    assert sorted(got) == [D - timedelta(days=1), D] and all(len(v) == 3 for v in got.values())
    assert [k for k, _, _ in got[D]] == ["core", "deep", "rem"]
