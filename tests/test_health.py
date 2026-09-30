"""Apple Health: personal key, Shortcut push (French-formatted text), idempotent
storage, daily aggregation, export.zip import, the fitness signal, migration."""

import importlib.util
import io
import pathlib
import random
import zipfile
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user
from app.models.health import HealthMetric, HealthSample
from app.models.user import User
from app.routers import health as health_router
from app.services import health
from app.services.health import (
    aggregate_days,
    compute_form,
    current_form,
    hash_api_key,
    new_api_key,
    normalize_payload,
    parse_export,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
MIG = ROOT / "alembic" / "versions" / "s3b4c5d6e7f8_add_apple_health.py"


@pytest.fixture
async def as_user(client: AsyncClient, test_user: User):
    client._transport.app.dependency_overrides[get_current_user] = lambda: test_user  # type: ignore[attr-defined]
    return client


@pytest.fixture(autouse=True)
def _no_rate_limit_carry_over():
    health_router._pushes.clear()
    yield
    health_router._pushes.clear()


async def _key_for(db: AsyncSession, user: User) -> str:
    key, key_hash, prefix = new_api_key()
    user.health_key_hash, user.health_key_prefix = key_hash, prefix
    await db.flush()
    return key


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S+02:00")


def _shortcut_payload(day: date) -> dict:
    """What the Shortcut sends: one text per field, one item per line, comma decimals."""
    prev = day - timedelta(days=1)
    at = lambda d, h, m=0: datetime(d.year, d.month, d.day, h, m)  # noqa: E731
    return {
        "hrv": "45,5\n54,5\n80",
        "hrv_dates": "\n".join(_iso(t) for t in (at(day, 2, 10), at(day, 5, 40), at(day, 15, 0))),
        "rhr": "52",
        "rhr_dates": _iso(at(day, 9)),
        "sleep": "Au lit\nPrincipal\nProfond\nSommeil paradoxal\nÉveillé\nPrincipal",
        "sleep_start": "\n".join(_iso(t) for t in (at(prev, 22, 50), at(prev, 23, 0), at(day, 1, 0),
                                                   at(day, 2, 0), at(day, 3, 0), at(day, 3, 10))),
        "sleep_end": "\n".join(_iso(t) for t in (at(day, 7, 0), at(day, 1, 0), at(day, 2, 0),
                                                 at(day, 3, 0), at(day, 3, 10), at(day, 6, 40))),
        "weight": "72,4 kg\n72,1 kg",
        "weight_dates": f"{day.strftime('%d/%m/%Y')} 07:01\n{day.strftime('%d/%m/%Y')} 21:30",
        "vo2max": "48,5",
        "vo2max_dates": _iso(at(day, 12)),
    }


# ── key ─────────────────────────────────────────────────────────────────────

def test_key_generation_and_hash():
    key, key_hash, prefix = new_api_key()
    assert key.startswith("pfh_") and len(key) >= 30
    assert key_hash == hash_api_key(key) and len(key_hash) == 64 and key not in key_hash
    assert key.startswith(prefix) and len(prefix) < len(key) // 2
    assert new_api_key()[0] != key


async def test_generate_key_shown_once_and_stored_hashed(as_user: AsyncClient, test_user: User):
    # the session cookie is Secure outside DEBUG: talk https to the same app
    async with AsyncClient(transport=as_user._transport, base_url="https://test") as c:
        r = await c.post("/settings/health/key")
        assert r.status_code == 303 and r.headers["location"] == "/settings#apple-sante"
        assert test_user.health_key_hash and "pfh_" not in r.text
        page = await c.get("/settings")
        key = next(w for w in page.text.replace('"', " ").split() if w.startswith("pfh_") and len(w) > 20)
        assert test_user.health_key_hash == hash_api_key(key)
        assert "ne sera plus jamais affichée" in page.text
        again = await c.get("/settings")  # a reload never shows it again, nor mints a new one
        assert key not in again.text and test_user.health_key_prefix in again.text
        assert test_user.health_key_hash == hash_api_key(key)
        assert "Rechercher des échantillons de santé" in again.text and "/api/health/samples" in again.text

        r = await c.post("/settings/health/key/revoke")
        assert r.status_code == 303 and test_user.health_key_hash is None
        assert "Clé révoquée" in (await c.get("/settings")).text


async def test_push_rejects_missing_or_unknown_key(client: AsyncClient, db_session: AsyncSession, test_user: User):
    body = {"rhr": "52", "rhr_dates": "2026-09-30T08:00:00+02:00"}
    r = await client.post("/api/health/samples", json=body)
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    r = await client.post("/api/health/samples", json=body, headers={"Authorization": "Bearer pfh_nope"})
    assert r.status_code == 401
    key = await _key_for(db_session, test_user)
    r = await client.post("/api/health/samples", json=body, headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200
    # the bare key without "Bearer" is a common Shortcut slip: accepted
    r = await client.post("/api/health/samples", json=body, headers={"Authorization": key})
    assert r.status_code == 200
    test_user.health_key_hash = None
    await db_session.flush()
    r = await client.post("/api/health/samples", json=body, headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 401


async def test_push_size_and_rate_limits(client: AsyncClient, db_session: AsyncSession, test_user: User, monkeypatch):
    key = await _key_for(db_session, test_user)
    auth = {"Authorization": f"Bearer {key}"}
    monkeypatch.setattr(health_router, "MAX_BODY_BYTES", 200)
    r = await client.post("/api/health/samples", content=b'{"hrv": "' + b"4" * 400 + b'"}', headers=auth)
    assert r.status_code == 413
    monkeypatch.setattr(health_router, "MAX_BODY_BYTES", 5 * 1024 * 1024)
    health_router._pushes.clear()
    monkeypatch.setattr(health_router, "PUSHES_PER_HOUR", 2)
    codes = [(await client.post("/api/health/samples", json={}, headers=auth)).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


# ── ingestion ───────────────────────────────────────────────────────────────

async def test_shortcut_push_parses_french_text_and_aggregates(client: AsyncClient, db_session: AsyncSession, test_user: User):
    key = await _key_for(db_session, test_user)
    day = date.today() - timedelta(days=1)
    r = await client.post("/api/health/samples", json=_shortcut_payload(day),
                          headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] and out["received"] == 13 and out["ignored"] == 0
    assert out["message"].startswith("PaceForge : 13 mesures reçues")
    assert test_user.health_last_push_count == 13 and test_user.health_last_push_at is not None

    rows = (await db_session.execute(
        select(HealthMetric).where(HealthMetric.user_id == test_user.id, HealthMetric.date == day)
    )).scalars().all()
    by = {m.metric: m for m in rows}
    assert by["hrv"].value == 50.0 and by["hrv"].details["window"] == "nuit"  # 15:00 reading left out
    assert by["rhr"].value == 52
    assert by["weight"].value == 72.1  # last of the day
    assert by["vo2max"].value == 48.5
    # 23:00 → 03:00 + 03:10 → 06:40 asleep; the 10-min awake gap and in-bed don't count
    assert by["sleep"].value == 7 * 60 + 30
    assert by["sleep"].details["deep"] == 60 and by["sleep"].details["rem"] == 60
    assert by["sleep"].details["awake"] == 10 and by["sleep"].details["in_bed"] == 490
    assert by["sleep"].details["bedtime"] == "23:00" and by["sleep"].details["wake"] == "06:40"


async def test_push_is_idempotent_and_updates(client: AsyncClient, db_session: AsyncSession, test_user: User):
    key = await _key_for(db_session, test_user)
    auth = {"Authorization": f"Bearer {key}"}
    day = date.today() - timedelta(days=2)
    body = _shortcut_payload(day)
    first = (await client.post("/api/health/samples", json=body, headers=auth)).json()
    n = (await db_session.execute(select(func.count(HealthSample.id)).where(HealthSample.user_id == test_user.id))).scalar()
    second = (await client.post("/api/health/samples", json=body, headers=auth)).json()
    n2 = (await db_session.execute(select(func.count(HealthSample.id)).where(HealthSample.user_id == test_user.id))).scalar()
    assert first["new"] == 13 and second["new"] == 0 and second["updated"] == 0 and n == n2 == 13

    body["rhr"] = "55"  # same reading re-sent with a corrected value → updated, not duplicated
    third = (await client.post("/api/health/samples", json=body, headers=auth)).json()
    assert third["updated"] == 1 and third["new"] == 0
    rhr = (await db_session.execute(select(HealthMetric.value).where(
        HealthMetric.user_id == test_user.id, HealthMetric.metric == "rhr", HealthMetric.date == day))).scalar()
    assert rhr == 55


def test_payload_shapes_and_rejections():
    samples, errors = normalize_payload([
        {"type": "HKQuantityTypeIdentifierRestingHeartRate", "value": "51", "unit": "count/min",
         "startDate": "2026-09-30 08:00:00 +0200", "sourceName": "Apple Watch"},
        {"type": "Poids", "value": "160", "unit": "lb", "start": "30 sept. 2026 à 07:00"},
        {"type": "sommeil", "value": "Profond", "start": "2026-09-30T01:00:00+02:00", "end": "2026-09-30T01:45:00+02:00"},
        {"type": "pas", "value": "12000", "start": "2026-09-30"},
        {"type": "hrv", "value": "abc", "start": "2026-09-30"},
        {"type": "hrv", "value": "900", "start": "2026-09-30"},
    ])
    assert [(s.metric, s.kind) for s in samples] == [("rhr", ""), ("weight", ""), ("sleep", "deep")]
    assert samples[0].source == "Apple Watch" and samples[0].start == datetime(2026, 9, 30, 8, 0)
    assert abs(samples[1].value - 72.575) < 0.01 and samples[2].value == 45
    assert len(errors) == 3
    nested, errs = normalize_payload({"hrv": {"values": [48, 52.5], "dates": ["2026-09-29T03:00:00Z", "2026-09-30T03:00:00Z"]}})
    assert [s.value for s in nested] == [48, 52.5] and not errs
    mismatch, errs = normalize_payload({"hrv": "40\n41\n42", "hrv_dates": "2026-09-30T03:00:00+02:00"})
    assert len(mismatch) == 1 and "3 valeurs pour 1 dates" in errs[0]


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


# ── export.zip ──────────────────────────────────────────────────────────────

EXPORT_HEAD = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE HealthData [
<!ELEMENT HealthData (ExportDate,Me,(Record|Workout)*)>
<!ATTLIST HealthData locale CDATA #REQUIRED>
]>
<HealthData locale="fr_FR">
 <ExportDate value="{now} +0200"/>
 <Me HKCharacteristicTypeIdentifierDateOfBirth="1985-01-01"/>
"""


def _record(rtype, start, end=None, value="", unit=None, source="Apple Watch de Flo"):
    unit_attr = f' unit="{unit}"' if unit else ""
    end = end or start
    return (f' <Record type="{rtype}" sourceName="{source}" sourceVersion="10.0"{unit_attr} '
            f'creationDate="{end} +0200" startDate="{start} +0200" endDate="{end} +0200" value="{value}">\n'
            f'  <MetadataEntry key="HKTimeZone" value="Europe/Paris"/>\n </Record>\n')


def _synthetic_export(days: int, extra_heart_rate: int = 0) -> bytes:
    today = date.today()
    parts = [EXPORT_HEAD.format(now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))]
    rng = random.Random(3)
    for k in range(days, 0, -1):
        d = today - timedelta(days=k)
        prev = d - timedelta(days=1)
        f = lambda dd, h, m=0: f"{dd.isoformat()} {h:02d}:{m:02d}:00"  # noqa: E731
        parts.append(_record("HKQuantityTypeIdentifierHeartRateVariabilitySDNN", f(d, 3, 12), value=f"{rng.uniform(40, 70):.3f}", unit="ms"))
        parts.append(_record("HKQuantityTypeIdentifierRestingHeartRate", f(d, 9), value=str(rng.randint(48, 54)), unit="count/min"))
        parts.append(_record("HKCategoryTypeIdentifierSleepAnalysis", f(prev, 23), f(d, 3), "HKCategoryValueSleepAnalysisAsleepCore"))
        parts.append(_record("HKCategoryTypeIdentifierSleepAnalysis", f(d, 3), f(d, 6, 30), "HKCategoryValueSleepAnalysisAsleepREM"))
        parts.append(_record("HKCategoryTypeIdentifierSleepAnalysis", f(prev, 22, 45), f(d, 7), "HKCategoryValueSleepAnalysisInBed", source="iPhone"))
        if k % 7 == 0:
            parts.append(_record("HKQuantityTypeIdentifierBodyMass", f(d, 7), value="72.3", unit="kg", source="Balance"))
            parts.append(_record("HKQuantityTypeIdentifierVO2Max", f(d, 18), value="49.1", unit="mL/min·kg"))
        for i in range(extra_heart_rate):  # the bulk of a real export: ignored
            parts.append(_record("HKQuantityTypeIdentifierHeartRate", f(d, 8 + i // 60, i % 60), value="61", unit="count/min"))
        parts.append(f' <Workout workoutActivityType="HKWorkoutActivityTypeRunning" duration="45" startDate="{f(d, 18)} +0200" endDate="{f(d, 19)} +0200"><WorkoutEvent type="HKWorkoutEventTypeSegment" date="{f(d, 18)} +0200"/></Workout>\n')
    old = today - timedelta(days=500)
    parts.append(_record("HKQuantityTypeIdentifierRestingHeartRate", f"{old.isoformat()} 09:00:00", value="60", unit="count/min"))
    parts.append("</HealthData>\n")
    return "".join(parts).encode()


def _zip(xml: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("apple_health_export/exportation.xml", xml)
        zf.writestr("apple_health_export/exportation_cda.xml", "<ClinicalDocument/>")
        zf.writestr("apple_health_export/workout-routes/route_1.gpx", "<gpx/>")
    return buf.getvalue()


def test_parse_export_streams_supported_types_of_the_last_year(tmp_path):
    path = tmp_path / "export.zip"
    path.write_bytes(_zip(_synthetic_export(20, extra_heart_rate=30)))
    samples, counts = parse_export(str(path))
    assert counts["hrv"] == 20 and counts["rhr"] == 20 and counts["sleep"] == 60  # the 500-day-old one is dropped
    assert counts["weight"] == 2 and counts["vo2max"] == 2
    assert not any(s.metric not in health.METRICS for s in samples)

    bad = tmp_path / "bad.zip"
    bad.write_bytes(_zip(b"<HealthData><Record type='x'")[:200])
    with pytest.raises(health.ExportError):
        parse_export(str(bad))


async def test_export_upload_counts_and_reimport_is_idempotent(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    body = _zip(_synthetic_export(30))
    r = await as_user.post("/settings/health/import", content=body,
                           headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 200 and "Import terminé" in r.text, r.text
    assert "VFC" in r.text and ">30</b> mesures" in r.text
    days = (await db_session.execute(select(func.count(HealthMetric.id)).where(
        HealthMetric.user_id == test_user.id, HealthMetric.metric == "sleep"))).scalar()
    assert days == 30
    sleep = (await db_session.execute(select(HealthMetric).where(
        HealthMetric.user_id == test_user.id, HealthMetric.metric == "sleep"))).scalars().first()
    assert sleep.value == 450 and sleep.source == "Apple Watch de Flo"  # watch stages beat the phone's in-bed

    n = (await db_session.execute(select(func.count(HealthSample.id)).where(HealthSample.user_id == test_user.id))).scalar()
    r = await as_user.post("/settings/health/import", files={"file": ("export.zip", body, "application/zip")})
    assert r.status_code == 200 and "déjà connue" in r.text
    n2 = (await db_session.execute(select(func.count(HealthSample.id)).where(HealthSample.user_id == test_user.id))).scalar()
    assert n == n2

    r = await as_user.post("/settings/health/import", content=b"not a zip at all",
                           headers={"Content-Type": "application/octet-stream"})
    assert "illisible" in r.text or "pas un export" in r.text


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


async def test_current_form_and_dashboard_card(as_user: AsyncClient, db_session: AsyncSession, test_user: User):
    r = await as_user.get("/activities")
    assert "Connecter Apple Santé" in r.text  # empty state
    today = date.today()
    for metric, series in _series(today, 60, 45, 50, 56).items():
        for d, v in series.items():
            db_session.add(HealthMetric(user_id=test_user.id, date=d, metric=metric, value=v, n_samples=1))
    await db_session.flush()
    form = await current_form(db_session, test_user.id, today)
    assert form["status"] == "fatigue" and form["hrv_delta_pct"] < 0 and form["rhr_delta_bpm"] > 0
    assert set(form) >= {"status", "hrv_delta_pct", "rhr_delta_bpm", "sleep_avg_min", "days_of_data"}
    r = await as_user.get("/activities")
    assert "Fatigue probable" in r.text and "<svg viewBox=\"0 0 120 30\"" in r.text
    assert r.text.count("<title>") >= 90  # one hover target per day on each sparkline


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
