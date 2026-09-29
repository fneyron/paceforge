"""Terrain from the trace itself: gradient every 25 m and the OpenStreetMap surface."""
import importlib.util
import json
import pathlib

import sqlalchemy as sa

from app.database import Base
from app.schemas.simulator import AthleteGradientProfile, CourseProfile, CourseSegment
from app.services import race_simulator as rs
from app.services.surface import classify_way, km_multiplier, overpass_query, surface_by_km


def _profile():
    return AthleteGradientProfile(flat_pace_s_per_km=330, gradient_factors=rs._DEFAULT_GRADIENT_FACTORS, data_points=0, sport_types_used=[])


def _course(elev_at):
    """2 km, one 1 km segment each, [lat, lon, km, ele] every 10 m."""
    coords = [[45.0 + i * 0.00009, 6.0, i * 0.01, elev_at(i * 0.01)] for i in range(201)]
    segs = []
    for k in range(2):
        a, b = elev_at(k), elev_at(k + 1)
        segs.append(CourseSegment(index=k, start_km=k, end_km=k + 1, distance_m=1000, elevation_gain=max(0, b - a),
                                  elevation_loss=max(0, a - b), avg_gradient_pct=(b - a) / 10, min_elevation=min(a, b), max_elevation=max(a, b)))
    return CourseProfile(name="t", total_distance_km=2, total_elevation_gain=0, total_elevation_loss=0, segments=segs,
                         elevation_points=[{"distance_km": 0, "elevation": elev_at(0)}], route_coords=coords)


def test_classify_way():
    assert classify_way({"highway": "residential"}) == "road"
    assert classify_way({"highway": "path"}) == "trail"
    assert classify_way({"highway": "path", "surface": "asphalt"}) == "road"
    assert classify_way({"highway": "track"}) == "track"
    assert classify_way({"highway": "service", "surface": "gravel"}) == "track"


def test_a_wall_costs_more_than_the_same_average():
    # km 0: steady +5 %; km 1: flat for 800 m then a 25 % wall (same +50 m)
    def elev(x):
        if x <= 1:
            return 100 + 50 * x
        return 150 if x <= 1.8 else 150 + (x - 1.8) * 250
    fine = rs._fine_terrain(_course(elev), _profile())
    assert fine is not None and fine[1] > fine[0] * 1.1


def test_surface_reshapes_without_moving_the_total():
    c = _course(lambda x: 100.0)
    base = rs._fine_terrain(c, _profile())
    c.surface_km = [[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, 0.0, 1.0]]
    shaped = rs._fine_terrain(c, _profile())
    assert shaped[1] > shaped[0]
    assert abs(sum(shaped) / 2 - sum(base) / 2) < 0.02  # normalised over the course
    assert km_multiplier([0, 0, 1]) > km_multiplier([0, 1, 0]) > km_multiplier([1, 0, 0])


def test_surface_by_km_matches_the_nearest_way():
    coords = [[45.0 + i * 0.00009, 6.0, i * 0.01, 100.0] for i in range(201)]  # 2 km due north
    road = {"tags": {"highway": "residential"}, "geometry": [{"lat": 45.0, "lon": 6.0}, {"lat": 45.0 + 100 * 0.00009, "lon": 6.0}]}
    shares = surface_by_km(coords, [road])
    assert shares[0][0] > 0.9          # first km on the road
    assert shares[1][2] > 0.9          # second km: nothing mapped → trail
    assert "way[highway]" in overpass_query(coords)


def test_prediction_without_trace_keeps_the_km_model():
    c = _course(lambda x: 100 + 50 * x)
    c.route_coords = []
    out = rs.predict_course(c, _profile())
    assert out.predicted_total_time_s > 0


def test_transjeju_surface_migration():
    mig = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions" / "q1f2a3b4c5d6_transjeju_surface.py"
    spec = importlib.util.spec_from_file_location("mig_tj_surface", mig)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    import app.models  # noqa: F401
    eng = sa.create_engine("sqlite://")
    Base.metadata.create_all(eng)
    n = len(json.loads(mod.DATA.read_text()))
    coords = [[33.0, 126.0, k * 0.5, 100.0] for k in range(2 * (n - 1) + 2)]  # last km index = n - 1
    with eng.begin() as c:
        c.execute(sa.text("INSERT INTO routes (id, user_id, name, course_json, total_distance_km, total_elevation_gain, total_elevation_loss, sport_type, created_at) VALUES (1, 1, 'Transjeju 100M', :cj, 147.9, 4785, 4816, 'trail', CURRENT_TIMESTAMP)"), {"cj": json.dumps({"route_coords": coords})})
        c.execute(sa.text("INSERT INTO routes (id, user_id, name, course_json, total_distance_km, total_elevation_gain, total_elevation_loss, sport_type, created_at) VALUES (2, 1, 'Transjeju 2025', :cj, 145.6, 5000, 5000, 'trail', CURRENT_TIMESTAMP)"), {"cj": json.dumps({"route_coords": coords[:100]})})
    with eng.begin() as c:
        assert mod.apply(c)["routes"] == 1
        cj = json.loads(c.execute(sa.text("SELECT course_json FROM routes WHERE id = 1")).scalar())
    assert len(cj["surface_km"]) == n and cj["route_coords"]
