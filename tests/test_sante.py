"""The Santé page: one decision, the last 14 nights, fitness — owner-like
sparse data, full data, not connected, connected without data, errors,
navigation; the decision ladder, the night rules and the strip."""

from datetime import date, timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.health import HealthMetric
from app.models.user import User
from app.services.health import compute_form
from app.services.sante import _nights, _strip, _verdict, clock_m, load_key, m_clock
from tests import test_coros
from tests.test_coros import _link

# the COROS test fixtures (a linked athlete, commits turned into flushes)
as_user, no_commit = test_coros.as_user, test_coros.no_commit

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
        _add(db, user, "hrv", d, 83 + k % 3)
        _add(db, user, "hrv_norm", d, 77, {"lo": 70, "hi": 84})
        _add(db, user, "rhr", d, 39 + k % 2)
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
    assert "Connecte ta montre COROS" in page and 'href="/settings#coros"' in page
    assert "/coros/connect" not in page and "Synchroniser maintenant" not in page  # connecting happens in Réglages


async def test_connected_without_data_yet(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    await _link(db_session, test_user)
    page = (await as_user.get("/sante")).text
    assert "Synchroniser maintenant" in page and "Pas encore de données de COROS" in page
    assert "Connecter COROS" not in page


async def test_owner_like_sparse_data(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    """The owner's case: few nights long ago, but load every day."""
    await _link(db_session, test_user)
    today = date.today()
    await _seed_owner(db_session, test_user, today)
    page = (await as_user.get("/sante")).text
    assert "Connecter COROS" not in page and "Synchroniser maintenant" in page
    # one decision from the load alone, said as an action, with what it rests on
    assert "Séance prévue, sans en rajouter" in page and "n&#39;ajoute ni volume ni intensité" in page
    assert ("Avis basé sur ta charge seulement : il faut 14 nuits sur 2 mois pour connaître ta normale "
            "(tu en as 3).") in page
    assert "Charge · 7 j" in page and "1,36" in page and "forte hausse" in page
    # gone: the watch's ring and advice, the period switch, the load chart, alarm words
    for gone in (">84<", "Récupération complète", "Séance modérée possible", "?jours=", "Surcharge",
                 "risque de blessure", "Court terme", "pf-health-chart"):
        assert gone not in page, gone
    # the nights section stays, says since when and asks for the watch at night once
    since = (today - timedelta(days=40)).strftime("%d/%m")
    assert "Tes nuits" in page and f"Aucune nuit avec ta montre depuis le {since} (9h00)." in page
    assert page.count("Porte-la la nuit") + page.count("Porte ta montre la nuit") == 1
    assert 'class="pf-nights"' not in page  # no empty strip
    # fitness: VO2max and threshold pace only (no level index, no road predictions)
    assert "3:20" in page and "1:10:54" not in page and "2:25:03" not in page and ">97<" not in page


async def test_full_data(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    await _link(db_session, test_user)
    today = date.today()
    await _seed_owner(db_session, test_user, today)
    for k in range(67):
        d = today - timedelta(days=k)
        if k not in (40, 41, 55):
            _add(db_session, test_user, "hrv", d, 80 + (k % 4) * 2)
            _add(db_session, test_user, "rhr", d, 42 + k % 2)
            _add(db_session, test_user, "sleep", d, 450 + k % 30, {"bedtime": "23:10", "wake": f"07:{k % 30:02d}"})
    await db_session.flush()
    page = (await as_user.get("/sante")).text
    assert "VFC · 7 j" in page and "FC au repos · 7 j" in page and "d&#39;habitude" in page
    assert 'class="pf-nights"' in page and page.count('class="pf-n-ok"') == 14
    assert "Sommeil · 7 nuits" in page and "Horaires · 14 nuits" in page and "besoin 8 h · 7 nuits sur 7" in page
    assert "Porte ta montre" not in page and "Porte-la" not in page
    assert "Avis basé sur ta charge" not in page


async def test_race_week_banks_sleep(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    from app.models.route import Route

    await _link(db_session, test_user)
    today = date.today()
    for k in range(20):
        _add(db_session, test_user, "sleep", today - timedelta(days=k), 450, {"bedtime": "23:00", "wake": "07:00"})
    db_session.add(Route(user_id=test_user.id, name="Trail des Glaciers", total_distance_km=42,
                         race_date=(today + timedelta(days=3)).isoformat(), start_hour=5, start_minute=0))
    await db_session.flush()
    page = (await as_user.get("/sante")).text
    assert "Trail des Glaciers dans 3 jours : vise 9 h par nuit d&#39;ici là, au lit vers 21:15." in page
    assert "Départ à 05:00 : si tu te lèves 2 h avant, réveil vers 03:00, 4h00 plus tôt que d&#39;habitude." in page
    assert "bande : 9 h avant ton lever habituel" in page and "besoin 9 h" in page
    assert "réveil à 07:00" in page


async def test_a_failure_is_not_shown_as_not_connected(as_user: AsyncClient, db_session: AsyncSession,
                                                       test_user: User, monkeypatch):
    await _link(db_session, test_user)

    async def boom(*a, **k):
        raise ValueError("bad row")
    monkeypatch.setattr("app.routers.sante.health_page", boom)
    page = (await as_user.get("/sante")).text
    assert "Impossible d'afficher tes données" in page and "Connecter COROS" not in page


# ── the decision ────────────────────────────────────────────────────────────

def _form(status="unknown", reasons=(), **kw):
    base = {"status": status, "reasons": list(reasons), "nights_base": 20, "nights_recent": 5,
            "rhr_baseline": None, "rhr_sd": None}
    return {**base, **kw}


LOAD = {"good": {"key": "good"}, "over": {"key": "over"}, "keep": {"key": "keep"}, "taper": {"key": "taper"}}
T = date(2026, 10, 6)


def test_ill_needs_two_nights_strictly_above_the_limit():
    form = _form("fatigue", ["FC au repos +5 bpm"], rhr_baseline=44.0, rhr_sd=1.5)
    # 44 + max(2 × 1.5, 5) = 49: 49 and 48 are not above it (the rich-data edge case)
    v = _verdict(form, None, {}, {T: 49, T - timedelta(days=1): 48}, T)
    assert v["headline"] == "Pas d'intensité aujourd'hui"
    v = _verdict(form, None, {}, {T: 50, T - timedelta(days=1): 49.5}, T)
    assert v["headline"] == "Reste tranquille aujourd'hui" and "souvent" in v["text"]
    v = _verdict(form, None, {}, {T: 53}, T)  # one night only: never ill
    assert v["headline"] == "Pas d'intensité aujourd'hui"


def test_fatigue_with_a_normal_load_says_the_load_is_not_the_cause():
    v = _verdict(_form("fatigue", ["VFC nettement sous ta normale"]), LOAD["good"], {}, {}, T)
    assert v["tone"] == "rest" and "ta VFC revient à son niveau habituel" in v["text"]
    assert "Ta charge n'est pas en cause" in v["text"]
    v = _verdict(_form("fatigue", ["FC au repos +6 bpm"]), LOAD["over"], {}, {}, T)
    assert "ta FC au repos redescend" in v["text"] and "pas en cause" not in v["text"]


def test_easy_headlines_by_trigger():
    short = {"short_night": "Nuit courte : place ta séance de qualité ce matin plutôt que ce soir, ou passe-la en facile."}
    assert _verdict(_form("ok"), None, short, {}, T)["headline"] == "Séance dure le matin seulement"
    assert _verdict(_form("unknown"), LOAD["over"], {}, {}, T)["headline"] == "Séance prévue, sans en rajouter"
    v = _verdict(_form("watch", ["VFC un peu sous ta normale"]), LOAD["over"], short, {}, T)
    assert v["headline"] == "Garde ta séance facile" and v["text"].count(".") == 2  # two clauses at most
    v = _verdict(_form("fresh"), LOAD["over"], {}, {}, T)
    assert "haute en pleine charge" in v["text"]  # a high HRV in a heavy block is no green light


def test_ok_and_unknown_with_load():
    v = _verdict(_form("ok"), LOAD["taper"], {}, {}, T)
    assert v["headline"] == "Séance dure possible" and "tu t'affûtes" in v["text"] and v["note"] is None
    v = _verdict(_form("unknown", nights_base=3), LOAD["keep"], {}, {}, T)
    assert v["headline"] == "Entraînement prévu OK" and "(tu en as 3)" in v["note"]
    v = _verdict(_form("unknown", nights_base=0), None, {}, {}, T)
    assert v["headline"] == "Pas encore d'avis" and v["note"].startswith("Il faut 14 nuits")


def test_compute_form_gives_the_resting_hr_sd():
    rhr = {T - timedelta(days=k): 44 + (k % 3) for k in range(7, 30)} | {T - timedelta(days=k): 45 for k in range(4)}
    form = compute_form({"rhr": rhr}, T)
    assert form["rhr_sd"] is not None and form["rhr_sd"] >= 1.5


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

async def test_opening_sante_syncs_a_stale_link_and_reloads_once(as_user: AsyncClient, db_session: AsyncSession,
                                                                 test_user: User, monkeypatch):
    from datetime import datetime, timezone

    from app.services import coros as coros_service

    conn = await _link(db_session, test_user)
    started = []
    monkeypatch.setattr(coros_service, "schedule_sync", lambda uid: started.append(uid) or True)
    # never synced: the page starts a sync and waits for it
    page = (await as_user.get("/sante")).text
    assert started == [test_user.id] and "Synchro en cours…" in page and 'hx-get="/sante/sync-status"' in page
    # while it runs, the status keeps waiting; once done with something new, one reload
    conn.sync_claimed_at = datetime.now(timezone.utc)
    await db_session.flush()
    r = await as_user.get("/sante/sync-status")
    assert "Synchro en cours…" in r.text and "HX-Refresh" not in r.headers
    conn.sync_claimed_at = None
    conn.last_sync_at = datetime.now(timezone.utc)
    await db_session.flush()
    r = await as_user.get("/sante/sync-status")
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
    r = await as_user.get("/sante/sync-status")  # nothing running, nothing new: the button
    assert "HX-Refresh" not in r.headers and "Synchroniser maintenant" in r.text
