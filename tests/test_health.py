"""Health data: daily aggregation, the fitness signal, migrations."""

import importlib.util
import pathlib
import re
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user
from app.models.health import HealthMetric
from app.models.user import User
from app.services.health import (
    aggregate_days,
    compute_form,
    current_form,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
MIG = ROOT / "alembic" / "versions" / "s3b4c5d6e7f8_add_apple_health.py"


@pytest.fixture
async def as_user(client: AsyncClient, test_user: User):
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user  # type: ignore[attr-defined]
    return client


# ── daily aggregation ───────────────────────────────────────────────────────

def _row(start, end=None, value=0.0, source="", kind=""):
    return SimpleNamespace(start_at=start, end_at=end or start, value=value, source=source, kind=kind)


def test_hrv_night_window_then_daytime_fallback():
    d = date(2026, 9, 30)
    rows = [_row(datetime(2026, 9, 29, 23, 30), value=40), _row(datetime(2026, 9, 30, 4, 0), value=60),
            _row(datetime(2026, 9, 30, 4, 0, 30), value=60, source="export"),  # same reading, other source
            _row(datetime(2026, 9, 30, 18, 0), value=100),
            _row(datetime(2026, 10, 1, 14, 0), value=30), _row(datetime(2026, 10, 1, 16, 0), value=50)]
    out = aggregate_days("hrv", rows, [d, d + timedelta(days=1)])
    assert out[d]["value"] == 50 and out[d]["n"] == 2
    assert out[d + timedelta(days=1)]["value"] == 40 and out[d + timedelta(days=1)]["details"]["window"] == "journée"


def test_sleep_one_source_per_night_and_naps_left_out():
    d = date(2026, 9, 30)
    t = lambda h, m=0, day=30: datetime(2026, 9, day, h, m)  # noqa: E731
    watch = [_row(t(23, 0, 29), t(2, 0), source="Watch", kind="core"),
             _row(t(1, 30), t(3, 0), source="Watch", kind="deep"),  # overlaps core: counted once
             _row(t(3, 0), t(6, 0), source="Watch", kind="rem")]
    phone = [_row(t(22, 30, 29), t(7, 0), source="iPhone", kind="in_bed"),
             _row(t(23, 0, 29), t(6, 30), source="iPhone", kind="asleep")]  # longer, but no stages
    nap = [_row(t(14, 0), t(15, 0), source="Watch", kind="core")]
    out = aggregate_days("sleep", watch + phone + nap, [d])[d]
    assert out["value"] == 7 * 60 and out["source"] == "Watch"
    phone_only = aggregate_days("sleep", [phone[0]], [d])[d]
    assert phone_only["value"] == 510 and phone_only["details"]["from_in_bed"]


def test_last_of_day_for_weight():
    d = date(2026, 9, 30)
    out = aggregate_days("weight", [_row(datetime(2026, 9, 30, 7), value=72.4),
                                    _row(datetime(2026, 9, 30, 21), value=73.0)], [d])
    assert out[d]["value"] == 73.0 and out[d]["n"] == 2


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


async def test_current_form_and_sante_verdict(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    r = await as_user.get("/sante")
    assert "Connecter COROS" in r.text  # empty state
    today = date.today()
    for metric, series in _series(today, 60, 45, 50, 56).items():
        for d, v in series.items():  # PaceForge's nightly HR is what the fitness signal reads as « rhr »
            db_session.add(HealthMetric(user_id=test_user.id, date=d, metric="hr_night" if metric == "rhr" else metric,
                                        value=v, n_samples=1))
    await db_session.flush()
    form = await current_form(db_session, test_user.id, today)
    assert form["status"] == "fatigue" and form["hrv_delta_pct"] < 0 and form["rhr_delta_bpm"] > 0
    assert set(form) >= {"status", "hrv_delta_pct", "rhr_delta_bpm", "sleep_avg_min", "days_of_data"}
    assert form["nights_recent"] == 7 and form["nights_base"] == 60
    r = await as_user.get("/sante")
    # nightly HR ~6 bpm over the usual two nights running: the HR alert, « Récupération faible » (v4.3), its sentence
    assert "Récupération faible" in r.text and "À ménager" not in r.text
    assert "FC de nuit nettement au-dessus de ta normale 2 nuits de suite" in r.text
    assert r.text.index('class="pf-card pf-facts"') < r.text.index('id="fc"') and "Ce matin ?" not in r.text
    assert re.search(r'<a class="pf-fact is-danger" href="#fc">.*?nettement au-dessus, 2 nuits</span></a>', r.text)


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
