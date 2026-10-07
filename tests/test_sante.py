"""The Santé page: one decision, the last 14 nights, fitness — owner-like
sparse data, full data, not connected, connected without data, errors,
navigation; the decision ladder, the night rules and the strip."""

from datetime import date, timedelta

from httpx import AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.health import HealthMetric
from app.models.user import User
from app.services.health import compute_form
from app.services.sante import _nights, _strip, clock_m, load_key, m_clock
from app.services.sante_today import decide, gauge, order_rows, rhr_row
from app.services.sante_training import Session
from tests import test_coros
from tests.test_coros import _link

# the COROS test fixtures (a linked athlete, commits turned into flushes)
as_user, no_commit = test_coros.as_user, test_coros.no_commit

PF_HRV = {"n": 30, "method": "ln_mean_main"}  # PaceForge's own nightly HRV
FIT = {"vo2max": 61, "level": 97, "threshold_s": 200,
       "pred": {"5k": 950, "10k": 1954, "half": 4254, "marathon": 8703}}


def _add(db: AsyncSession, user: User, metric: str, d: date, value: float, details=None):
    db.add(HealthMetric(user_id=user.id, date=d, metric=metric, value=value, source="COROS",
                        details=details, n_samples=1))


async def _seed_owner(db: AsyncSession, user: User, today: date):
    """What the owner's watch gives: every day load, heart rate, stress and
    steps; recovery and fitness today; HRV, resting HR and sleep on 3 nights
    in 60 days only — the last ones 40 days ago."""
    for k in range(60):
        d = today - timedelta(days=k)
        if k % 9 == 4:
            continue  # a day off the wrist
        _add(db, user, "steps", d, 9000 + 300 * (k % 7), {"kcal": 500, "exercise": 40})
        _add(db, user, "stress", d, 20 + k % 15)
        _add(db, user, "hr_day", d, 60 + k % 10, {"min": 38, "max": 140})
        _add(db, user, "load", d, 150 + k % 20, {"long": 110, "ratio": round((150 + k % 20) / 110, 2),
                                                 "comment": "Excessive"})
    _add(db, user, "recovery", today, 84, {"level": "Moderate training recommended", "full_h": 45})
    _add(db, user, "fitness", today, 97, FIT)
    _add(db, user, "vo2max", today, 61)
    for k in (40, 41, 55):
        d = today - timedelta(days=k)
        _add(db, user, "hrv", d, 83 + k % 3, PF_HRV)
        _add(db, user, "hrv_norm", d, 77, {"lo": 70, "hi": 84})
        _add(db, user, "hr_night", d, 39 + k % 2)
        _add(db, user, "sleep", d, 540, {"bedtime": "23:50", "wake": "09:00"})
    await db.flush()


# ── routes ──────────────────────────────────────────────────────────────────

async def test_sante_requires_login(client: AsyncClient):
    r = await client.get("/sante")
    assert r.status_code == 307 and r.headers["location"] == "/"


async def test_nav_item_and_activities_link(as_user: AsyncClient):
    page = (await as_user.get("/activities")).text
    assert page.count('href="/sante"') == 2  # top bar and tab bar only: Activités no longer talks about health
    assert "7 derniers jours comparés" not in page and "récupération" not in page
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


async def test_owner_like_sparse_data(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    """The owner's case: few nights long ago, the watch's load every day, no Strava."""
    await _link(db_session, test_user)
    today = date.today()
    await _seed_owner(db_session, test_user, today)
    page = (await as_user.get("/sante")).text
    assert "Connecter COROS" not in page and "Synchroniser maintenant" in page
    # four tabs, the decision first; the watch's load, recovery and scores are never read any more
    assert page.count('role="tab"') == 4 and 'aria-selected="true"' in page
    assert "Pas encore d&#39;avis" in page
    for brand in ("×1,36", "forte hausse", "Récup COROS", "calculée par COROS", "Charge <small>", "Body Battery",
                  "score"):
        assert brand not in page, brand
    # the missing nights are said once, with since when
    assert page.count("Porte ta montre") <= 1  # said once at most (this page is rebuilt in Santé v3)
    assert "pas de nuit mesurée (montre pas portée ?)" in page  # steps came today, no night
    assert 'class="pf-nights' not in page  # no empty strip
    for gone in ("risque de blessure", "Surcharge", "pf-word-danger\">forte hausse"):
        assert gone not in page, gone


async def test_full_data(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    await _link(db_session, test_user)
    today = date.today()
    await _seed_owner(db_session, test_user, today)
    for k in range(67):
        d = today - timedelta(days=k)
        if k not in (40, 41, 55):
            _add(db_session, test_user, "hrv", d, 80 + (k % 4) * 2, PF_HRV)
            _add(db_session, test_user, "hr_night", d, 42 + k % 2)
            _add(db_session, test_user, "sleep", d, 450 + k % 30, {"bedtime": "23:10", "wake": f"07:{k % 30:02d}"})
            _add(db_session, test_user, "sleep_score", d, 70 + k % 10)
    await db_session.flush()
    page = (await as_user.get("/sante")).text
    assert "VFC" in page and "FC au repos" in page and "ta normale" in page and "28 nuits" in page
    assert 'class="pf-nights"' in page and page.count('class="pf-n-ok"') == 14  # no watch score column
    assert "Sommeil · 7 nuits" in page and "Horaires · 14 nuits" in page and "besoin 8 h · 7 nuits sur 7" in page
    assert "cette nuit : reçue" in page
    assert "Porte ta montre" not in page


async def test_race_week_points_to_course(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    from app.models.route import Route

    await _link(db_session, test_user)
    today = date.today()
    for k in range(20):
        _add(db_session, test_user, "sleep", today - timedelta(days=k), 450, {"bedtime": "23:00", "wake": "07:00"})
    db_session.add(Route(user_id=test_user.id, name="Trail des Glaciers", total_distance_km=42,
                         race_date=(today + timedelta(days=3)).isoformat(), start_hour=5, start_minute=0))
    await db_session.flush()
    page = (await as_user.get("/sante")).text
    assert "Semaine de course : ton plan de sommeil (heures, coucher, lever) est dans Course." in page
    assert "bande : 9 h avant ton lever habituel" in page and "besoin 9 h" in page


async def test_a_failure_is_not_shown_as_not_connected(as_user: AsyncClient, db_session: AsyncSession,
                                                       test_user: User, monkeypatch):
    await _link(db_session, test_user)

    async def boom(*a, **k):
        raise ValueError("bad row")
    monkeypatch.setattr("app.routers.sante.health_page", boom)
    page = (await as_user.get("/sante")).text
    assert "Impossible d'afficher tes données" in page and "Connecter COROS" not in page


async def test_the_check_in_is_stored_once_a_day_and_moves_the_decision(as_user: AsyncClient,
                                                                        db_session: AsyncSession, test_user: User):
    from sqlalchemy import select

    await _link(db_session, test_user)
    today = date.today()
    await _seed_owner(db_session, test_user, today)
    page = (await as_user.get("/sante")).text
    assert "Ce matin, tu te sens ?" in page
    r = await as_user.post("/sante/feel", data={"feel": "3"}, headers={"HX-Request": "true"})
    assert r.headers.get("HX-Refresh") == "true"
    r = await as_user.post("/sante/feel", data={"legs": "1"})
    assert r.status_code == 303
    rows = (await db_session.execute(select(HealthMetric).where(
        HealthMetric.user_id == test_user.id, HealthMetric.metric == "feel"))).scalars().all()
    assert len(rows) == 1 and rows[0].value == 3 and rows[0].details == {"legs_heavy": True}
    page = (await as_user.get("/sante")).text
    assert "Ressenti du jour : fatigué, jambes lourdes · changer" in page
    assert "Endurance facile aujourd&#39;hui" in page  # heavy legs: easy today


# ── the decision ────────────────────────────────────────────────────────────

T = date(2026, 10, 6)


def _form(status="unknown", reasons=(), **kw):
    base = {"status": status, "reasons": list(reasons), "nights_base": 20, "nights_recent": 5,
            "rhr_baseline": 44.0, "rhr_sd": 1.5, "hrv_baseline": 80.0, "hrv_z": None, "rhr_delta_bpm": None}
    out = {**base, **kw}
    if out["rhr_delta_bpm"] is not None and "rhr_7d" not in kw:  # the 7 nights the delta was read on
        out["rhr_7d"] = out["rhr_baseline"] + out["rhr_delta_bpm"]
    return out


def _ctx(**kw):
    base = {"form": _form(), "tr": None, "legs": None, "easy": None, "feel": None, "next_race": None,
            "post_race": None, "today": T, "ill": False, "hard48": None, "short_night": None, "jump": False,
            "watch_load": None}
    return {**base, **kw}


TR = {"pct": 4, "key": "balanced", "word": "équilibré", "history": {}}


def test_illness_first():
    v = decide(_ctx(ill=True, form=_form("fatigue", hrv_z=-1.5, rhr_delta_bpm=6)))
    assert v["tone"] == "rest" and v["headline"] == "Reste tranquille aujourd'hui" and v["word"] == "repos"


def test_red_needs_hrv_down_and_resting_hr_up_together():
    both = decide(_ctx(form=_form("fatigue", hrv_z=-0.8, rhr_delta_bpm=3.5), tr=TR))
    assert both["tone"] == "rest" and both["headline"] == "Pas d'intensité aujourd'hui"
    assert "sommeil, stress, virus" in both["text"] and both["drivers"] == ["hrv", "rhr"]
    # one strong signal alone: an easy day, not a stop
    alone = decide(_ctx(form=_form("fatigue", hrv_z=-1.4, rhr_delta_bpm=0.5), tr=TR))
    assert alone["tone"] == "easy" and alone["headline"] == "Garde ta séance facile"
    assert "ne suffit pas à tout arrêter" in alone["text"] and "Dis-moi comment tu te sens" in alone["text"]
    # the dip after a long outing is expected: not flagged
    long_ = Session(id=1, start=None, day=T - timedelta(days=1), sport="TrailRun", minutes=250, dplus=1800, km=30,
                    speed=2.0, hr=140, hr_peak=170, suffer=200, workout_type=0, temp=None)
    v = decide(_ctx(form=_form("fatigue", hrv_z=-1.4, rhr_delta_bpm=0.5), tr=TR, hard48=long_))
    assert v["headline"] != "Garde ta séance facile"
    # race week: a lower HRV alone is normal, the rest of the ladder still applies
    v = decide(_ctx(form=_form("watch", hrv_z=-0.7, rhr_delta_bpm=1), next_race={"days": 5, "name": "UTMB"}, tr=TR))
    assert v["headline"] == "Entraînement prévu OK" and v["hrv_note"].startswith("Avant une course")
    v = decide(_ctx(form=_form("watch", hrv_z=-0.7, rhr_delta_bpm=1), next_race={"days": 5, "name": "UTMB"}, tr=TR,
                    feel={"value": 3, "legs_heavy": False}))
    assert v["headline"] == "Garde ta séance facile"
    # two mild signals: easy, never greener than one
    v = decide(_ctx(form=_form("fatigue", ["FC au repos +4 bpm", "nuits courtes"], hrv_z=0.1, rhr_delta_bpm=3.5),
                    tr=TR))
    assert v["headline"] == "Garde ta séance facile" and v["drivers"] == ["rhr", "sleep"]


def test_race_days_and_recovery():
    v = decide(_ctx(next_race={"days": 2, "name": "Marathon"}))
    assert v["headline"] == "Course après-demain : court et facile" and v["tone"] == "ok"
    pr = {"day": T - timedelta(days=3), "days": 3, "minutes": 702, "known": True, "name": "Grand Trail", "limit": 10,
          "long": True}
    v = decide(_ctx(post_race=pr))
    assert v["headline"] == "Récupère" and "Grand Trail (11h42)" in v["text"] and v["tone"] == "easy"
    assert v["resume"] == "Pas d'intensité avant le 14/10." and v["word"] == "récup"
    v = decide(_ctx(post_race={**pr, "day": T - timedelta(days=6), "days": 6}))
    assert v["headline"] == "Footing facile seulement" and v["tone"] == "easy"
    v = decide(_ctx(post_race={**pr, "minutes": 50, "long": False, "limit": 2, "days": 1, "day": T - timedelta(days=1)}))
    assert v["headline"] == "Footing facile seulement" and v["resume"] == "Reprends l'intensité le 08/10."


def test_legs_then_accumulated_then_jump():
    big = Session(id=7, start=None, day=T - timedelta(days=1), sport="TrailRun", minutes=250, dplus=2100, km=30,
                  speed=2.0, hr=140, hr_peak=170, suffer=200, workout_type=0, temp=None)
    v = decide(_ctx(tr=TR, legs={"key": "loaded", "big": big, "minutes": 300, "dplus": 2200}))
    assert v["headline"] == "Endurance facile aujourd'hui"
    assert v["text"].startswith("Ta sortie d'hier (4h10, +2\u202f100 m)")
    assert v["resume"] == "Séance dure possible à partir de demain."
    hist = {T - timedelta(days=k): 35 for k in range(10)}
    v = decide(_ctx(tr={**TR, "pct": 35, "key": "loaded", "history": hist}))
    assert v["headline"] == "Semaine plus légère conseillée" and "10" in v["text"]
    v = decide(_ctx(tr={**TR, "pct": 22, "key": "build"}, jump=True))
    assert v["headline"] == "Séance prévue, sans en rajouter" and "risque" not in v["text"]


def test_checkin_and_the_quiet_days():
    v = decide(_ctx(tr=TR, feel={"value": 3, "legs_heavy": False}))
    assert v["headline"] == "Garde ta séance facile" and "c'est toi qui sais" in v["text"]
    v = decide(_ctx(tr=TR, form=_form("ok", hrv_z=0.1, rhr_delta_bpm=0.5)))
    assert v["headline"] == "Séance dure possible" and v["word"] == "feu vert"
    v = decide(_ctx(tr=TR, form=_form("unknown", nights_recent=0)))
    assert v["headline"] == "Entraînement prévu OK" and v["note"].startswith("Sans nuit mesurée")
    v = decide(_ctx())
    assert v["headline"] == "Pas encore d'avis" and v["word"] == "?"
    # « en forme » never lifts a stop
    v = decide(_ctx(form=_form("fatigue", hrv_z=-0.8, rhr_delta_bpm=3.5), feel={"value": 1, "legs_heavy": False}))
    assert v["tone"] == "rest"


def test_rows_order_and_the_watch_last():
    rows = [{"key": "stress", "dev": 0.2}, {"key": "watch", "dev": 0}, {"key": "hrv", "dev": 3},
            {"key": "fatigue", "dev": 0.1}, {"key": "sleep", "dev": 1}, {"key": "easy_hr", "dev": 0.9},
            {"key": "legs", "dev": 0}]
    visible, more = order_rows(rows, ["easy_hr"])
    assert [r["key"] for r in visible] == ["easy_hr", "hrv", "fatigue", "legs", "watch"]  # a driver takes a place
    assert [r["key"] for r in more] == ["sleep", "stress"]


def test_gauge_is_in_percent_and_clamped():
    g = gauge(36, -40, 60, band=(-5, 10), edges=[(0, "ton fond")])
    assert g["dot"] == 76.0 and g["band"] == {"l": 35.0, "w": 15.0} and g["edges"][0]["x"] == 40.0
    assert gauge(90, -40, 60)["dot"] == 100.0


def test_compute_form_reads_hrv_on_ln_and_gives_its_band():
    hrv = {T - timedelta(days=k): 80 + (k % 4) * 2 for k in range(7, 60)} | {T - timedelta(days=k): 70 for k in range(4)}
    f = compute_form({"hrv": hrv}, T)
    lo, hi = f["hrv_band"]
    assert lo < f["hrv_baseline"] < hi and f["hrv_z"] < -0.5 and f["status"] in ("watch", "fatigue")
    rhr = {T - timedelta(days=k): 44 + (k % 3) for k in range(7, 30)} | {T - timedelta(days=k): 45 for k in range(4)}
    assert compute_form({"rhr": rhr}, T)["rhr_sd"] >= 1.5


# ── the nights ──────────────────────────────────────────────────────────────

def test_clock_wraps_midnight():
    assert clock_m("22:00") == 240 and clock_m("23:34") == 334 and clock_m("05:31") == 691
    assert clock_m("00:10") == 370 and clock_m("bad") is None
    assert m_clock(334) == "23:34" and m_clock(691) == "05:31" and m_clock(1450) == "18:10"


def _sleep(nights: dict[int, tuple[float, str, str]]):
    """{days ago: (minutes asleep, bedtime, wake)} → series, details."""
    series = {T - timedelta(days=k): v for k, (v, _, _) in nights.items()}
    det = {T - timedelta(days=k): {"bedtime": b, "wake": w} for k, (_, b, w) in nights.items()}
    return series, det


def test_few_nights_ask_for_the_watch_once_and_hide_the_averages():
    series, det = _sleep({1: (420, "23:00", "07:00"), 3: (400, "23:20", "06:50")})
    n = _nights(series, det, T, None, None)
    assert n["n7"] == 2 and n["avg7_txt"] is None and n["takeaway"] is None
    assert n["coverage"] == ("2 nuits mesurées sur les 7 dernières : il en faut 3 pour ta moyenne. "
                             "Porte ta montre la nuit.")
    assert n["rows"][0]["pending"] and n["strip"]["bars"][0]["title"].endswith("pas encore reçue")
    assert n["strip"]["bars"][2]["title"].endswith("pas de montre")


def test_missing_nights_are_never_bars_or_zeros():
    series, det = _sleep({k: (450, "23:00", "07:00") for k in (0, 2, 4)})
    series[T - timedelta(days=1)] = 0  # a broken night with no window
    n = _nights(series, det, T, None, None)
    bars = n["strip"]["bars"]
    assert [b.get("missing", False) for b in bars[:5]] == [False, True, False, True, False]
    assert all("w" not in b for b in bars if b.get("missing"))
    assert n["avg7_txt"] == "7h30"  # the missing night is not a 0


def test_regularity_takes_the_worse_of_bed_and_wake_from_5_nights():
    series, det = _sleep({k: (440, "23:00", w) for k, w in zip(range(4), ("05:30", "07:35", "06:00", "07:00"))})
    assert _nights(series, det, T, None, None)["regularity"] is None  # 4 of 14
    series, det = _sleep({k: (440, "23:00", w) for k, w in
                          zip(range(5), ("05:30", "07:35", "06:00", "07:00", "05:40"))})
    reg = _nights(series, det, T, None, None)["regularity"]
    assert reg["range"] == "réveil entre 05:30 et 07:35" and reg["sd"] > 30


def test_sleep_rules():
    # < 6 h on average this week
    series, det = _sleep({k: (340, "00:30", "06:30") for k in range(5)} |
                         {k: (470, "23:00", "07:00") for k in range(7, 14)})
    n = _nights(series, det, T, None, None)
    assert n["takeaway"].startswith("Moins de 6 h par nuit cette semaine") and "rhume" in n["takeaway"]
    assert n["short_night"].startswith("Nuit courte")
    # debt ≥ 60 min with a known wake time: a bedtime target
    series, det = _sleep({k: (400, "23:20", "06:30") for k in range(6)})  # 6 of 14: no 2-week rule yet
    n = _nights(series, det, T, None, None)
    assert n["takeaway"].startswith("Il te manque environ 1h20 par nuit sur tes 8 h")
    assert "Au lit vers 21:45." in n["takeaway"]  # 06:30 − 8 h − 30 min awake in the window − 15 min
    # over 2 weeks under 7 h: the injury rule comes first, with the debt
    series, det = _sleep({k: (400, "23:20", "06:30") for k in range(10)})
    assert _nights(series, det, T, None, None)["takeaway"] == (
        "Moins de 7 h par nuit sur 2 semaines : chez les coureurs d'endurance, c'est lié à plus de blessures. "
        "Il te manque environ 1h20 par nuit : au lit vers 21:45.")
    # enough, regular
    series, det = _sleep({k: (480, "23:00", "07:05") for k in range(10)})
    assert _nights(series, det, T, None, None)["takeaway"] == "Assez de sommeil, horaires réguliers : rien à changer."
    # a few measured nights: the averages may flatter
    series, det = _sleep({k: (480, "23:00", "07:05") for k in range(4)})
    assert _nights(series, det, T, None, None)["selective"] is True


def test_strip_geometry():
    series, det = _sleep({k: (450, "23:30", "07:00") for k in range(10)})
    n = _nights(series, det, T, None, None)
    st = n["strip"]
    assert st["height"] == 280 and st["half"] == 140 and len(st["bars"]) == 14
    edge = [t["label"] for t in st["ticks"] if t["edge"]]
    assert edge == ["23:00", "07:00"]  # the band: 8 h of sleep (+ the usual 0 min awake) before 07:00, labelled
    assert all(0 <= t["x"] <= 100 for t in st["ticks"])
    bar = st["bars"][0]
    assert 0 < bar["x"] < bar["x"] + bar["w"] <= 100 and bar["tone"] == "ok"
    assert _strip([{"day": T, "label": "cette nuit", "value": None, "bed": None, "wake": None, "pending": True}],
                  None, None, 480)["band"] is None


def test_load_words_follow_the_watch_then_the_ratio():
    assert load_key("Excessive", 1.2) == "over" and load_key(None, 1.64) == "over"
    assert load_key(None, 1.2) == "good" and load_key("Detraining", None) == "low" and load_key(None, None) is None


# ── opening Santé syncs a stale link ────────────────────────────────────────

async def test_opening_sante_syncs_a_stale_link_and_reloads_only_with_news(as_user: AsyncClient,
                                                                          db_session: AsyncSession,
                                                                          test_user: User, monkeypatch):
    import re
    from datetime import datetime, timezone

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
    from datetime import datetime, timezone

    from app.services import coros as coros_service

    conn = await _link(db_session, test_user)
    conn.last_sync_at = datetime.now(timezone.utc) - timedelta(minutes=40)
    started = []
    monkeypatch.setattr(coros_service, "schedule_sync", lambda uid: started.append(uid) or True)
    today = datetime.now(timezone.utc).date()
    db_session.add(HealthMetric(user_id=test_user.id, date=today - timedelta(days=1), metric="sleep", value=450,
                                source="COROS", n_samples=1, details={"bedtime": "23:00", "wake": "07:00"}))
    await db_session.flush()
    # last night isn't there yet: synced 30 min ago is already too old
    await as_user.get("/sante")
    assert started == [test_user.id]
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


def test_a_race_day_beats_recovery_and_estimates_are_never_printed():
    pr = {"day": T - timedelta(days=1), "days": 1, "minutes": 2995, "known": False, "name": "Trail X", "limit": 10,
          "long": True, "race": True}
    v = decide(_ctx(post_race=pr))
    assert v["text"].startswith("Après Trail X, cœur") and "(" not in v["text"].split(",")[0]
    v = decide(_ctx(post_race={**pr, "long": False, "limit": 2}, next_race={"days": 0, "name": "Semi"}))
    assert v["headline"] == "Jour de course"
    # after a race the low HRV is expected: the row gets the note
    v = decide(_ctx(post_race={**pr, "known": True, "minutes": 642},
                    form=_form("watch", hrv_z=-0.8, rhr_delta_bpm=1)))
    assert v["text"].startswith("Après Trail X (10h42)") and v["hrv_note"].startswith("Basse après ta course")


def test_after_a_race_or_on_its_eve_low_hrv_and_high_rhr_get_their_note():
    pr = {"day": T - timedelta(days=1), "days": 1, "minutes": 300, "known": True, "name": "Trail X", "limit": 10,
          "long": True, "race": True}
    both = _form("fatigue", hrv_z=-1.2, rhr_delta_bpm=4)
    v = decide(_ctx(post_race=pr, form=both))
    assert v["rule"] == "race" and v["hrv_note"].startswith("Basse après ta course")
    assert v["rhr_note"].startswith("Haute après ta course")
    assert "Une VFC basse" not in v["text"]  # said once, on the row
    row = rhr_row(both, None, note=v["rhr_note"])
    assert row["tone"] == "muted" and row["sub"] == v["rhr_note"]
    # a training outing is not called a race
    outing = {**pr, "race": False, "name": "ta sortie de dimanche"}
    v = decide(_ctx(post_race=outing, form=both))
    assert v["hrv_note"] == "Basse après ta sortie de dimanche : normal, ça revient en quelques jours."
    # on the eve of a race too
    v = decide(_ctx(next_race={"days": 1, "name": "UTMB"}, form=both))
    assert v["hrv_note"].startswith("Avant une course") and v["rhr_note"].startswith("Avant une course")
    # its time is not printed twice when the Jambes row already shows it
    legs = {"key": "loaded", "minutes": 300.2, "big": None}
    v = decide(_ctx(post_race=pr, legs=legs))
    assert v["text"].startswith("Après Trail X, cœur")


def test_resting_hr_counts_from_three_bpm_exactly():
    v = decide(_ctx(form=_form("watch", hrv_z=-0.7, rhr_delta_bpm=2.4), tr=TR))
    assert v["rule"] == "mild" and v["drivers"] == ["hrv"]  # 46 against 44 is not +3: no red
    v = decide(_ctx(form=_form("fatigue", hrv_z=-0.7, rhr_delta_bpm=3.0), tr=TR))
    assert v["rule"] == "red"
    # the numbers the athlete reads decide: 47,3 against 44,4 prints 47 and 44, +3
    v = decide(_ctx(form=_form("fatigue", hrv_z=-0.7, rhr_baseline=44.4, rhr_7d=47.3, rhr_delta_bpm=2.9), tr=TR))
    assert v["rule"] == "red"
    row = rhr_row(_form(rhr_baseline=44.4, rhr_7d=47.3, rhr_delta_bpm=2.9), None)
    assert (row["value"], row["ref"], row["word"]) == ("47 bpm", "d'habitude 44", "un peu haute")


def test_the_legs_numbers_are_printed_once():
    big = Session(id=7, start=None, day=T - timedelta(days=1), sport="TrailRun", minutes=250, dplus=2100, km=30,
                  speed=2.0, hr=140, hr_peak=170, suffer=200, workout_type=0, temp=None)
    v = decide(_ctx(tr=TR, legs={"key": "loaded", "big": big, "minutes": 250, "dplus": 2100}))
    assert v["text"].startswith("Ta sortie d'hier pèse encore")  # the Jambes row shows 4h10 · +2 100 m
    v = decide(_ctx(tr=TR, legs={"key": "loaded", "big": big, "minutes": 310, "dplus": 2200}))
    assert v["text"].startswith("Ta sortie d'hier (4h10")


async def test_dedupe_matches_activites_when_strava_writes_json_null(db_session: AsyncSession, test_user: User):
    from datetime import datetime, timezone

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
