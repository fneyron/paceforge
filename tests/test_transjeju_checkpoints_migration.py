"""The official Transjeju checkpoints replace the GPX waypoints on the owner's route only."""
import importlib.util
import pathlib

import pytest
import sqlalchemy as sa

from app.database import Base

MIG = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions" / "j4e5f6a7b8c9_transjeju_official_checkpoints.py"


def _load():
    spec = importlib.util.spec_from_file_location("mig_transjeju_cps", MIG)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def engine():
    import app.models  # noqa: F401
    eng = sa.create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return eng


def test_official_checkpoints_keep_snapped_km_and_scale_the_rest():
    mig = _load()
    existing = [
        {"name": "Healing Forest", "distance_km": 7.1}, {"name": "Yeongsil Entrance", "distance_km": 20.2},
        {"name": "Red oreum", "distance_km": 66.4}, {"name": "Gasiri", "distance_km": 88.4},
        {"name": "Seogwipo Student cultural Center Camping site", "distance_km": 114.2},  # misplaced in the GPX
        {"name": "Eseungak Oreum", "distance_km": 115.0}, {"name": "Healing Forest", "distance_km": 135.9},
    ]
    cps = mig.official_checkpoints(existing, 145.62)
    by = {(c["name"], i): c for i, c in enumerate(cps)}
    kms = [c["distance_km"] for c in cps]
    assert kms == sorted(kms) and len(cps) == 11
    assert cps[0]["distance_km"] == 7.1 and cps[1]["distance_km"] == 20.2  # snapped km kept
    assert cps[6]["name"] == "Gasiri" and cps[6]["distance_km"] == 88.4 and cps[6]["kind"] == "base" and cps[6]["drop_bag"]
    assert cps[9]["name"] == "Seogwipo Student Camping site" and 124 < cps[9]["distance_km"] < 126  # scaled, no longer at 114
    assert cps[10]["name"] == "Healing Forest" and cps[10]["distance_km"] == 135.9 and cps[10]["cutoff_clock"] == "07:00"
    assert all(c["name"] != "Red oreum" for c in cps)
    assert cps[5]["name"] == "Jeongseok Aviation Pavilion" and 72 < cps[5]["distance_km"] < 74


def test_migration_touches_the_owner_route_only(engine):
    mig = _load()
    with engine.begin() as c:
        c.execute(sa.text("INSERT INTO users (id, email, email_verified, created_at) VALUES (1, 'Florent.Neyron@gmail.com', 1, CURRENT_TIMESTAMP)"))
        c.execute(sa.text("INSERT INTO users (id, email, email_verified, created_at) VALUES (2, 'someone@example.com', 1, CURRENT_TIMESTAMP)"))
        for uid, name in ((1, "Transjeju 100M"), (1, "UTMB"), (2, "Transjeju 100M")):
            c.execute(sa.text("INSERT INTO routes (user_id, name, course_json, total_distance_km, total_elevation_gain, total_elevation_loss, sport_type, created_at) VALUES (:u, :n, '{}', 145.6, 5000, 5000, 'trail', CURRENT_TIMESTAMP)"), {"u": uid, "n": name})
        for rid in (1, 2, 3):
            c.execute(sa.text("INSERT INTO route_checkpoints (route_id, name, distance_km, kind, crew, drop_bag) VALUES (:r, 'Red oreum', 66.4, 'water', 0, 0)"), {"r": rid})
    with engine.begin() as c:
        out = mig.apply(c)
        assert out["routes"] == 1
        rows = c.execute(sa.text("SELECT route_id, name FROM route_checkpoints ORDER BY route_id, distance_km")).all()
    r1 = [n for rid, n in rows if rid == 1]
    assert len(r1) == 11 and "Red oreum" not in r1 and r1[0] == "Healing Forest"
    assert [n for rid, n in rows if rid == 2] == ["Red oreum"] and [n for rid, n in rows if rid == 3] == ["Red oreum"]
    with engine.begin() as c:
        assert mig.apply(c)["routes"] == 1  # idempotent
        assert c.execute(sa.text("SELECT count(*) FROM route_checkpoints WHERE route_id = 1")).scalar() == 11
