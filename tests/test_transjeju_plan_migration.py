"""The Transjeju data migration ticks the owner's products on his plan and leaves other users alone."""
import importlib.util
import json
import pathlib

import pytest
import sqlalchemy as sa

from app.database import Base

MIG = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions" / "h2c3d4e5f6a7_transjeju_nutrition_plan.py"


def _load():
    spec = importlib.util.spec_from_file_location("mig_transjeju", MIG)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def engine():
    import app.models  # noqa: F401  (register every table)
    eng = sa.create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return eng


def test_transjeju_migration_applies_to_owner_only(engine):
    mig = _load()
    with engine.begin() as c:
        c.execute(sa.text("INSERT INTO users (id, email, email_verified, created_at) VALUES (1, 'Florent.Neyron@gmail.com', 1, CURRENT_TIMESTAMP)"))
        c.execute(sa.text("INSERT INTO users (id, email, email_verified, created_at) VALUES (2, 'someone@example.com', 1, CURRENT_TIMESTAMP)"))
        c.execute(sa.text("INSERT INTO nutrition_products (user_id, name, kind, carbs_g, sodium_mg, created_at) VALUES (1, 'Gel Baouw', 'gel', 30, 0, CURRENT_TIMESTAMP)"))
        c.execute(sa.text("INSERT INTO nutrition_products (user_id, name, kind, carbs_g, sodium_mg, created_at) VALUES (1, 'Maurten Drink Mix 320', 'drink', 80, 200, CURRENT_TIMESTAMP)"))
        for uid, name in ((1, "Transjeju 100M"), (1, "UTMB"), (2, "Transjeju 100M")):
            c.execute(sa.text("INSERT INTO routes (user_id, name, course_json, total_distance_km, total_elevation_gain, total_elevation_loss, nutrition_json, created_at) VALUES (:u, :n, '{}', 145.6, 5000, 5000, :nj, CURRENT_TIMESTAMP)"),
                      {"u": uid, "n": name, "nj": '{"targets": {"carbs_g_per_h": 60, "fluid_ml_per_h": 700, "sodium_mg_per_h": 400}, "items": [{"product_id": 2, "per_hour": 0.5}], "flask_capacity_ml": 1000}'})
    with engine.begin() as c:
        out = mig.apply(c)
    assert out["user"] == 1 and out["routes"] == 1
    assert set(out["created"]) == {"Precision Fuel PF 90 Gel", "Maurten Gel 160", "Maurten Gel 100 CAF 100", "Precision Hydration PH 1500 (pastille, 500 ml)"}
    with engine.connect() as c:
        names = {r[0] for r in c.execute(sa.text("SELECT name FROM nutrition_products WHERE user_id = 1"))}
        assert "Baouw Gel" not in names  # his existing Baouw is reused, not duplicated
        assert c.execute(sa.text("SELECT count(*) FROM nutrition_products WHERE user_id = 2")).scalar() == 0
        nj = json.loads(c.execute(sa.text("SELECT nutrition_json FROM routes WHERE user_id = 1 AND name = 'Transjeju 100M'")).scalar())
        ids = {r[0]: r[1] for r in c.execute(sa.text("SELECT name, id FROM nutrition_products WHERE user_id = 1"))}
        by_pid = {it["product_id"]: it["per_hour"] for it in nj["items"]}
        assert by_pid[ids["Gel Baouw"]] == 0.5 and by_pid[ids["Precision Fuel PF 90 Gel"]] == 0.5 and by_pid[ids["Precision Hydration PH 1500 (pastille, 500 ml)"]] == 1.0
        assert ids["Maurten Drink Mix 320"] not in by_pid  # replaced by the race-day list
        assert nj["targets"]["carbs_g_per_h"] == 60 and nj["caffeine"]["enabled"] is True and nj["caffeine"]["dose_mg"] == 100
        other = json.loads(c.execute(sa.text("SELECT nutrition_json FROM routes WHERE name = 'UTMB'")).scalar())
        assert other["items"] == [{"product_id": 2, "per_hour": 0.5}]
    # running it twice changes nothing more
    with engine.begin() as c:
        out2 = mig.apply(c)
    assert out2["created"] == [] and out2["routes"] == 1
