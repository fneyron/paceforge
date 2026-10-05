"""The Santé page: owner-like sparse data, full data, not connected, connected
without data, errors, navigation; the page helpers (load wording, charts)."""

from datetime import date, timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.health import HealthMetric
from app.models.user import User
from app.services import sante
from app.services.sante import chart, load_status, recovery_level
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
    assert page.count('href="/sante"') >= 3  # sidebar, mobile nav, the line that replaced the card
    assert "Santé" in page and "7 derniers jours comparés" not in page  # the card moved
    page = (await as_user.get("/sante")).text
    assert page.count('href="/sante" aria-current="page"') == 2


async def test_not_connected(as_user: AsyncClient):
    page = (await as_user.get("/sante")).text
    assert "Connecte ta montre COROS" in page and 'href="/coros/connect?region=monde"' in page
    assert 'href="/coros/connect?region=europe"' in page and "Synchroniser maintenant" not in page


async def test_connected_without_data_yet(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    await _link(db_session, test_user)
    page = (await as_user.get("/sante")).text
    assert "Synchroniser maintenant" in page and "Pas encore de données de COROS" in page
    assert "Connecter COROS" not in page


async def test_owner_like_sparse_data(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    """The owner's case: few nights, but rich daily data — the page is full."""
    await _link(db_session, test_user)
    today = date.today()
    await _seed_owner(db_session, test_user, today)
    page = (await as_user.get("/sante")).text
    assert "Connecter COROS" not in page and "Synchroniser maintenant" in page
    # today
    assert ">84<" in page and "Séance modérée possible" in page and "Récupération complète dans 45 h" in page
    assert "Surcharge" in page and "risque de blessure" in page and ">150<" in page and ">110<" in page
    # trends: the daily ones drawn, the night ones explained (none in 30 days)
    for title in ("Charge d&#39;entraînement", "Stress", "FC moyenne du jour", "Pas"):
        assert f'aria-label="{title}, 30 derniers jours"' in page, title
    assert 'aria-label="VFC (variabilité cardiaque), 30 derniers jours"' not in page
    assert "FC au repos, VFC, Sommeil</span> : Porte ta montre la nuit" in page
    # verdict: what it waits for
    assert "Pas encore assez de données" in page and "tu en as 0 et 3" in page
    # fitness
    assert "3:20" in page and "1:10:54" in page and "2:25:03" in page and ">97<" in page
    # 90 days: the old nights show, with HRV's normal range
    page = (await as_user.get("/sante?jours=90")).text
    assert 'aria-label="VFC (variabilité cardiaque), 90 derniers jours"' in page
    assert "zone normale 70–84 ms" in page and '<a href="/sante?jours=90" aria-current="true"' in page


async def test_full_data(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    await _link(db_session, test_user)
    today = date.today()
    await _seed_owner(db_session, test_user, today)
    for k in range(67):
        d = today - timedelta(days=k)
        if k not in (40, 41, 55):
            _add(db_session, test_user, "hrv", d, 80 + (k % 4) * 2)
            _add(db_session, test_user, "rhr", d, 42 + k % 2)
            _add(db_session, test_user, "sleep", d, 450 + k % 30)
    await db_session.flush()
    page = (await as_user.get("/sante")).text
    assert "Forme normale" in page or "Bien récupéré" in page or "À surveiller" in page
    assert "pf-health-missing" not in page
    assert page.count('class="pf-health-chart') == 7


async def test_a_failure_is_not_shown_as_not_connected(as_user: AsyncClient, db_session: AsyncSession,
                                                       test_user: User, monkeypatch):
    await _link(db_session, test_user)

    async def boom(*a, **k):
        raise ValueError("bad row")
    monkeypatch.setattr("app.routers.sante.health_page", boom)
    page = (await as_user.get("/sante")).text
    assert "Impossible d'afficher tes données" in page and "Connecter COROS" not in page


# ── helpers ─────────────────────────────────────────────────────────────────

def test_load_status_words():
    assert load_status("Excessive", 1.64)[1] == "Surcharge"
    assert load_status("Optimized", 1.04)[1] == "Optimal"
    assert load_status("Maintaining", 0.92)[1] == "Maintien"
    assert load_status("Performance", 0.63)[1] == "Récupération"
    assert load_status(None, 1.7)[1] == "Surcharge" and load_status("Bizarre", 0.9)[1] == "Maintien"
    assert load_status(None, None)[0] == "unknown"
    assert recovery_level("Moderate training recommended") == "Séance modérée possible"
    assert recovery_level("Rest recommended") == "Repos conseillé"
    assert recovery_level("Quelque chose") == "Quelque chose"


def test_chart_draws_isolated_days_as_dots():
    today = date(2026, 10, 5)
    g = chart([{today - timedelta(days=10): 40.0, today - timedelta(days=3): 39.0,
                today - timedelta(days=2): 41.0}], today, 30)
    s = g["series"][0]
    assert len(s["dots"]) == 1 and s["d"].count("M") == 2 and s["d"].count("L") == 1
    assert len(g["hits"]) == 30 and g["hits"][-1][2] == "05/10 · —"
    assert chart([{today - timedelta(days=40): 40.0}], today, 30) is None  # nothing in the window
    bars = chart([{today: 1487.0, today - timedelta(days=1): 18055.0}], today, 30, bars=True,
                 fmt=sante.fmt_int)
    assert len(bars["bars"]) == 2 and bars["hits"][-1][2] == "05/10 · 1 487"
