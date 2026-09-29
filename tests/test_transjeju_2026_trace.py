"""The official 2026 trace replaces the 2025 one; checkpoints land on their roadbook altitude."""
import importlib.util
import json
import pathlib

import pytest
import sqlalchemy as sa

from app.database import Base

MIG = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions" / "p0e1f2a3b4c5_transjeju_2026_trace.py"


def _load():
    spec = importlib.util.spec_from_file_location("mig_tj_2026", MIG)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mig():
    return _load()


@pytest.fixture(scope="module")
def course(mig):
    return mig.build_course()


def test_the_2026_file_is_the_full_course(course):
    assert 147.5 < course["total_distance_km"] < 148.5
    assert 4700 < course["total_elevation_gain"] < 4900


def test_checkpoints_sit_on_their_official_altitude(mig, course):
    cps = mig.place_checkpoints(course)
    prof = [(c[2], c[3]) for c in course["route_coords"]]
    kms = [cp["distance_km"] for cp in cps]
    assert kms == sorted(kms) and len(cps) == 11
    for cp, (_, km, alt, *_r) in zip(cps, mig.OFFICIAL):
        trace_alt = min(prof, key=lambda p: abs(p[0] - cp["distance_km"]))[1]
        assert abs(trace_alt - alt) < 10, cp["name"]
        assert abs(cp["distance_km"] - km) < 1.0, cp["name"]
    # the two Healing Forest passages are the same place
    coords = course["route_coords"]
    a, b = (min(coords, key=lambda c: abs(c[2] - k)) for k in (kms[0], kms[-1]))
    assert abs(a[0] - b[0]) < 0.003 and abs(a[1] - b[1]) < 0.003


def test_apply_replaces_the_trace_and_keeps_stop_times(mig, course):
    import app.models  # noqa: F401
    eng = sa.create_engine("sqlite://")
    Base.metadata.create_all(eng)
    with eng.begin() as c:
        c.execute(sa.text("INSERT INTO routes (id, user_id, name, course_json, total_distance_km, total_elevation_gain, total_elevation_loss, sport_type, target_time_s, created_at) VALUES (1, 1, 'Transjeju 100M', '{}', 145.6, 5078, 5084, 'trail', 66600, CURRENT_TIMESTAMP)"))
        c.execute(sa.text("INSERT INTO routes (id, user_id, name, course_json, total_distance_km, total_elevation_gain, total_elevation_loss, sport_type, created_at) VALUES (2, 1, 'UTMB', '{}', 171, 10000, 10000, 'trail', CURRENT_TIMESTAMP)"))
        for i, km in enumerate([7.1, 20.2, 28.6, 40.8, 58.5, 74.3, 86.9, 106.4, 115.8, 127.6, 137.1]):
            c.execute(sa.text("INSERT INTO route_checkpoints (route_id, name, distance_km, kind, crew, drop_bag, stop_s) VALUES (1, 'x', :k, 'full', 0, 0, :s)"), {"k": km, "s": 600 if i == 9 else None})
    with eng.begin() as c:
        assert mig.apply(c, course)["routes"] == 1
        r = c.execute(sa.text("SELECT total_distance_km, target_time_s, course_json FROM routes WHERE id = 1")).one()
        rows = c.execute(sa.text("SELECT name, distance_km, stop_s FROM route_checkpoints WHERE route_id = 1 ORDER BY distance_km")).all()
        other = c.execute(sa.text("SELECT total_distance_km FROM routes WHERE id = 2")).scalar()
    cj = r[2] if isinstance(r[2], dict) else json.loads(r[2])
    assert r[0] == course["total_distance_km"] and r[1] == 66600 and len(cj["route_coords"]) > 1000
    assert rows[9][0] == "Seogwipo Student Camping site" and rows[9][2] == 600
    assert other == 171
