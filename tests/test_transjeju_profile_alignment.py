"""Official Transjeju checkpoints are placed on the trace by altitude and cumulative climb, not by scaled km."""
import importlib.util
import json
import math
import pathlib

import pytest
import sqlalchemy as sa

from app.database import Base

MIG = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions" / "m7b8c9d0e1f2_transjeju_checkpoints_on_profile.py"

# the roadbook as a profile: (km, altitude, cumulative climb) at each point, start and finish included
GRID = [(0.0, 80, 0), (7.1, 419, 341), (20.2, 1211, 1176), (28.6, 953, 1656), (40.8, 572, 1814), (58.5, 762, 3171),
        (74.3, 351, 3216), (86.9, 273, 3494), (106.4, 239, 3797), (115.8, 413, 4066), (127.6, 489, 4449),
        (137.1, 419, 4773), (148.7, 78, 4851)]
LOSS = [0, 3, 45, 783, 1323, 2489, 2946, 3300, 3636, 3733, 4040, 4434, 4853]


def _load():
    spec = importlib.util.spec_from_file_location("mig_tj_profile", MIG)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _warp(km):  # the user's GPX: 145,6 km, shortened unevenly
    return km * 145.6 / 148.7 + 1.5 * math.sin(math.pi * km / 148.7) * math.sin(3 * math.pi * km / 148.7)


def _trace():
    """Inside each section: climb its D+ then lose its D−, a point every 100 m, distances warped."""
    pts = []
    for i in range(len(GRID) - 1):
        (k0, a0, g0), (k1, _, g1) = GRID[i], GRID[i + 1]
        up, down = g1 - g0, LOSS[i + 1] - LOSS[i]
        n = max(2, int((k1 - k0) * 10))
        split = up / (up + down) if up + down else 0.5
        for j in range(n):
            f = j / n
            e = a0 + up * (f / split) if f < split else a0 + up - down * ((f - split) / (1 - split))
            pts.append((_warp(k0 + (k1 - k0) * f), e))
    pts.append((_warp(148.7), 78.0))
    return pts


def test_alignment_finds_each_checkpoint_on_an_unevenly_shortened_trace():
    mig = _load()
    pts = _trace()
    out = mig.align_on_profile(pts, pts[-1][0])
    assert len(out) == 11 and [c["distance_km"] for c in out] == sorted(c["distance_km"] for c in out)
    for (name, km, *_), cp in zip(mig.OFFICIAL, out):
        assert abs(cp["distance_km"] - _warp(km)) <= 0.4, (name, cp["distance_km"], round(_warp(km), 1))
    scaled_err = max(abs(km * pts[-1][0] / 148.7 - _warp(km)) for _, km, *_ in mig.OFFICIAL)
    assert scaled_err > 1.0  # what the previous placement got wrong


@pytest.fixture
def engine():
    import app.models  # noqa: F401
    eng = sa.create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return eng


def test_migration_realigns_and_keeps_a_stop_time_set_on_a_point(engine):
    mig = _load()
    pts = _trace()
    course = {"route_coords": [[33.3, 126.5, km, e] for km, e in pts], "elevation_points": []}
    with engine.begin() as c:
        c.execute(sa.text("INSERT INTO routes (id, user_id, name, course_json, total_distance_km, total_elevation_gain, total_elevation_loss, sport_type, created_at) VALUES (1, 1, 'Transjeju 100M', :cj, :km, 5000, 5000, 'trail', CURRENT_TIMESTAMP)"),
                  {"cj": json.dumps(course), "km": pts[-1][0]})
        c.execute(sa.text("INSERT INTO routes (id, user_id, name, course_json, total_distance_km, total_elevation_gain, total_elevation_loss, sport_type, created_at) VALUES (2, 1, 'UTMB', '{}', 170, 10000, 10000, 'trail', CURRENT_TIMESTAMP)"))
        for i, (name, km, *_) in enumerate(mig.OFFICIAL):
            c.execute(sa.text("INSERT INTO route_checkpoints (route_id, name, distance_km, kind, crew, drop_bag, stop_s) VALUES (1, :n, :k, 'full', 0, 0, :s)"),
                      {"n": name, "k": round(km * pts[-1][0] / 148.7, 1), "s": 600 if i == 6 else None})
        c.execute(sa.text("INSERT INTO route_checkpoints (route_id, name, distance_km, kind, crew, drop_bag) VALUES (2, 'Courmayeur', 80, 'base', 1, 1)"))
    with engine.begin() as c:
        assert mig.apply(c)["routes"] == 1
        rows = c.execute(sa.text("SELECT name, distance_km, elevation, stop_s, kind FROM route_checkpoints WHERE route_id = 1 ORDER BY distance_km")).all()
        other = c.execute(sa.text("SELECT name FROM route_checkpoints WHERE route_id = 2")).all()
    assert len(rows) == 11 and rows[6][0] == "Gasiri" and rows[6][3] == 600 and rows[6][4] == "base"
    assert abs(rows[8][1] - _warp(115.8)) <= 0.4 and rows[8][2] == 413.0  # Eseungak on its hill, official altitude
    assert [r[0] for r in other] == ["Courmayeur"]
