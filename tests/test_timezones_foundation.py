"""The audit foundation: one migration (single head, round trip) and the time-zone helper."""

import importlib.util
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import sqlalchemy as sa

from app.services.timezones import athlete_today, local_date, zone_from_strava, zone_of

ROOT = Path(__file__).resolve().parents[1]
MIG = ROOT / "alembic" / "versions" / "g7d8e9f0a1b2_audit_foundation.py"


def test_the_foundation_is_the_single_head_and_round_trips():
    from alembic.config import Config
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic.script import ScriptDirectory

    spec = importlib.util.spec_from_file_location("mig_foundation", MIG)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    assert mig.down_revision == "f6c7d8e9f0a1" and len(script.get_heads()) == 1
    eng = sa.create_engine("sqlite://")
    with eng.begin() as c:
        c.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(255))"))
        c.execute(sa.text("CREATE TABLE mobile_devices (id INTEGER PRIMARY KEY, user_id INTEGER, token_hash VARCHAR(64))"))
        c.execute(sa.text("CREATE TABLE mobile_daily (id INTEGER PRIMARY KEY, user_id INTEGER, metric VARCHAR(12))"))
        c.execute(sa.text("INSERT INTO users (id, email) VALUES (1, 'a@b.c')"))
        with Operations.context(MigrationContext.configure(c)):
            mig.upgrade()
            cols = {col["name"] for col in sa.inspect(c).get_columns("users")}
            assert {"timezone", "weight_set_at", "session_version"} <= cols
            assert c.execute(sa.text("SELECT session_version FROM users")).scalar() == 0
            assert {"last_seen_at", "prev_token_hash", "prev_expires_at"} <= {col["name"] for col in sa.inspect(c).get_columns("mobile_devices")}
            assert "device_id" in {col["name"] for col in sa.inspect(c).get_columns("mobile_daily")}
            mig.downgrade()
            assert "timezone" not in {col["name"] for col in sa.inspect(c).get_columns("users")}
            assert c.execute(sa.text("SELECT email FROM users")).scalar() == "a@b.c"


def test_the_athletes_day_is_read_in_his_zone():
    paris = SimpleNamespace(timezone=None)
    tokyo = SimpleNamespace(timezone="Asia/Tokyo")
    late = datetime(2026, 10, 24, 22, 30, tzinfo=timezone.utc)  # 00:30 in Paris (CEST), 07:30 in Tokyo
    assert athlete_today(paris, late) == date(2026, 10, 25) and athlete_today(tokyo, late) == date(2026, 10, 25)
    after_dst = datetime(2026, 10, 25, 23, 30, tzinfo=timezone.utc)  # 00:30 in Paris (CET, +1 after the change)
    assert athlete_today(paris, after_dst) == date(2026, 10, 26)
    assert local_date(datetime(2026, 10, 24, 22, 30), zone_of(None)) == date(2026, 10, 25)  # naive = UTC
    assert zone_of("Not/AZone").key == "Europe/Paris"


def test_stravas_zone_label_gives_the_iana_name():
    assert zone_from_strava("(GMT+01:00) Europe/Paris") == "Europe/Paris"
    assert zone_from_strava("(GMT-08:00) America/Los_Angeles") == "America/Los_Angeles"
    assert zone_from_strava("Asia/Seoul") == "Asia/Seoul"
    assert zone_from_strava("(GMT+00:00) Nowhere/Land") is None and zone_from_strava(None) is None
