"""Health data: raw samples (a timeline only, never aggregated: 2026-10-14), the fitness signal, migrations."""

import importlib.util
import pathlib
import re
from datetime import date, datetime, timedelta
from html import unescape

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user
from app.models.health import HealthMetric
from app.models.user import User
from app.services import health
from app.services.health import (
    Sample,
    compute_form,
    current_form,
    store_samples,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
MIG = ROOT / "alembic" / "versions" / "s3b4c5d6e7f8_add_apple_health.py"


@pytest.fixture
async def as_user(client: AsyncClient, test_user: User):
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user  # type: ignore[attr-defined]
    return client


# ── raw samples: a timeline only (2026-10-14: no daily value is made from them) ──────────────

async def _stored(db, user, samples) -> tuple[dict, list]:
    result = await store_samples(db, user.id, samples)
    rows = (await db.execute(sa.select(HealthMetric).where(HealthMetric.user_id == user.id))).scalars().all()
    return result, rows


async def test_hrv_night_window_then_daytime_fallback(db_session: AsyncSession, test_user: User):
    """Was: HRV samples aggregated into a day (the night window, else the daytime). Their only producer (the Apple
    import PaceForge had before) is gone and a night's HRV comes whole (store_daily): HRV samples are kept, idempotently,
    and never make a daily value."""
    samples = [Sample("hrv", "", datetime(2026, 9, 29, 23, 30), datetime(2026, 9, 29, 23, 30), 40, "export"),
               Sample("hrv", "", datetime(2026, 9, 30, 4, 0), datetime(2026, 9, 30, 4, 0), 60, "export")]
    result, rows = await _stored(db_session, test_user, samples)
    assert (result["inserted"], result["days"], rows) == (2, {}, [])
    again, _ = await _stored(db_session, test_user, samples)
    assert (again["inserted"], again["unchanged"]) == (0, 2)
    assert not hasattr(health, "aggregate_days") and not hasattr(health, "reaggregate")


async def test_sleep_one_source_per_night_and_naps_left_out(db_session: AsyncSession, test_user: User):
    """Was: one source's samples per night, naps left out. Sleep samples are Garmin's stage intervals, the
    hypnogram's timeline: never a night of their own (its main window is the night, store_daily), never a nap."""
    t = lambda h, m=0, day=30: datetime(2026, 9, day, h, m)  # noqa: E731
    samples = [Sample("sleep", "core", t(23, 0, 29), t(2, 0), 180, "Garmin"),
               Sample("sleep", "rem", t(3, 0), t(6, 0), 180, "Garmin"),
               Sample("sleep", "core", t(14, 0), t(15, 0), 60, "Garmin")]
    result, rows = await _stored(db_session, test_user, samples)
    assert (result["by_metric"], result["days"], rows) == ({"sleep": 3}, {}, [])
    assert not hasattr(health, "main_episode") and not hasattr(health, "sleep_episodes")


async def test_last_of_day_for_weight(db_session: AsyncSession, test_user: User):
    """Was: the day's last weight sample. The weight is the phone's (MobileDaily) or the typed one
    (athlete_weight): a weight sample makes no daily row."""
    samples = [Sample("weight", "", datetime(2026, 9, 30, 7), datetime(2026, 9, 30, 7), 72.4, "Balance"),
               Sample("weight", "", datetime(2026, 9, 30, 21), datetime(2026, 9, 30, 21), 73.0, "Balance")]
    result, rows = await _stored(db_session, test_user, samples)
    assert (result["inserted"], result["days"], rows) == (2, {}, [])
    assert not hasattr(health, "METRICS") and not hasattr(health, "_RANGES")


# ── fitness signal ──────────────────────────────────────────────────────────

def _series(today, hrv_base, hrv_recent, rhr_base, rhr_recent, sleep_base=450, sleep_recent=450, days=67):
    out = {"hrv": {}, "rhr": {}, "sleep": {}}
    for k in range(days):
        d = today - timedelta(days=k)
        recent = k < 7
        wobble = (k % 4) - 1.5  # zero-mean day-to-day noise, deterministic
        out["hrv"][d] = (hrv_recent if recent else hrv_base) + wobble * 6  # ~11 % SD
        out["rhr"][d] = (rhr_recent if recent else rhr_base) + wobble * 0.6
        out["sleep"][d] = (sleep_recent if recent else sleep_base) + wobble * 15
    return out


def test_compute_form_statuses():
    today = date(2026, 9, 30)
    ok = compute_form(_series(today, 60, 60, 50, 50), today)
    assert ok["status"] == "ok" and abs(ok["hrv_delta_pct"]) < 5 and ok["days_of_data"] == 67
    tired = compute_form(_series(today, 60, 45, 50, 56, sleep_recent=330), today)
    assert tired["status"] == "fatigue" and tired["hrv_delta_pct"] < -20 and tired["rhr_delta_bpm"] > 5
    assert tired["sleep_avg_min"] < 360 and "nuits courtes" in tired["reasons"]
    watch = compute_form(_series(today, 60, 60, 50, 53.5), today)
    assert watch["status"] == "watch"
    fresh = compute_form(_series(today, 60, 66, 50, 49), today)
    assert fresh["status"] == "fresh"
    young = compute_form(_series(today, 60, 45, 50, 56, days=12), today)
    assert young["status"] == "unknown" and young["days_of_data"] == 12


def test_sleep_average_shows_from_one_night():
    """It used to need 3 nights: an athlete with 2 nights this week read
    "Sommeil — · pas de mesure cette semaine" (the owner's case)."""
    today = date(2026, 10, 5)
    form = compute_form({"sleep": {today - timedelta(days=5): 526, today - timedelta(days=4): 588}}, today)
    assert form["sleep_avg_min"] == 557
    assert form["reasons"] == [] and form["sleep_delta_min"] is None  # a signal still needs 3 nights
    short = compute_form({"sleep": {today - timedelta(days=k): 300 for k in range(2)}}, today)
    assert "nuits courtes" not in short["reasons"]
    assert form["nights_recent"] == 0 and form["nights_base"] == 0


async def test_current_form_and_sante_verdict(as_user: AsyncClient, db_session: AsyncSession, test_user: User, same_today):
    r = await as_user.get("/sante")
    assert "Connecter COROS" in r.text  # empty state
    today = same_today
    # Seven recent nights at +7 bpm remain above the lagged personal reference;
    # the acute HR alert must stay visible under the new descriptive labels.
    for metric, series in _series(today, 60, 45, 50, 57).items():
        for d, v in series.items():  # PaceForge's nightly HR is what the fitness signal reads as « rhr »
            db_session.add(HealthMetric(user_id=test_user.id, date=d, metric="hr_night" if metric == "rhr" else metric,
                                        value=v, n_samples=1))
    await db_session.flush()
    form = await current_form(db_session, test_user.id, today)
    assert form["status"] == "fatigue" and form["hrv_delta_pct"] < 0 and form["rhr_delta_bpm"] > 0
    assert set(form) >= {"status", "hrv_delta_pct", "rhr_delta_bpm", "sleep_avg_min", "days_of_data"}
    assert form["nights_recent"] == 7 and form["nights_base"] == 60
    r = await as_user.get("/sante")
    assert "Signaux inhabituels" in r.text and "À ménager" not in r.text
    assert "Ta FC de nuit est nettement plus haute que d'habitude." in unescape(r.text)
    assert r.text.index('class="pf-dials"') < r.text.index('id="fc"') and "Ce matin ?" not in r.text
    # The alert remains visible once on the nightly chart, alongside the main signal warning.
    fc = re.search(r'<article id="fc"[^>]*>(.*?)</article>', r.text, re.S).group(1)
    # An alert must remain above the optional reference explanation.
    fc = fc.split('<details', 1)[0]
    assert "Élevée sur 2 nuits" in fc and 'pf-card-status is-danger' in fc



# ── migration ───────────────────────────────────────────────────────────────

def test_migration_is_the_head_and_round_trips():
    from alembic.config import Config
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic.script import ScriptDirectory

    spec = importlib.util.spec_from_file_location("mig_health", MIG)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    assert mig.down_revision == "r2a3b4c5d6e7"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    assert mig.revision in {r.revision for r in script.walk_revisions()}  # in the chain up to the head

    eng = sa.create_engine("sqlite://")
    with eng.begin() as c:
        c.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(255))"))
        c.execute(sa.text("INSERT INTO users (id, email) VALUES (1, 'a@b.c')"))
        with Operations.context(MigrationContext.configure(c)):
            mig.upgrade()
        insp = sa.inspect(c)
        assert {"health_samples", "health_metrics"} <= set(insp.get_table_names())
        assert "health_key_hash" in [col["name"] for col in insp.get_columns("users")]
        c.execute(sa.text("INSERT INTO health_metrics (user_id, date, metric, value, n_samples, updated_at) "
                          "VALUES (1, '2026-09-30', 'hrv', 52.0, 3, '2026-09-30 08:00:00')"))
        with pytest.raises(sa.exc.IntegrityError):
            c.execute(sa.text("INSERT INTO health_metrics (user_id, date, metric, value, n_samples, updated_at) "
                              "VALUES (1, '2026-09-30', 'hrv', 50.0, 1, '2026-09-30 09:00:00')"))
        with Operations.context(MigrationContext.configure(c)):
            mig.downgrade()
        insp = sa.inspect(c)
        assert "health_metrics" not in insp.get_table_names()
        assert "health_key_hash" not in [col["name"] for col in insp.get_columns("users")]
