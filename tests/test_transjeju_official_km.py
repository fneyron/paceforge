"""On the 2025 trace (145,6 km) the Transjeju checkpoints sit at the roadbook km, stop times kept."""
import importlib.util
import pathlib

import pytest
import sqlalchemy as sa

from app.database import Base

MIG = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions" / "o9d0e1f2a3b4_transjeju_checkpoints_official_km.py"


def _load():
    spec = importlib.util.spec_from_file_location("mig_tj_km", MIG)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def engine():
    import app.models  # noqa: F401
    eng = sa.create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return eng


def test_checkpoints_pinned_to_the_roadbook_km(engine):
    mig = _load()
    drifted = [7.1, 20.2, 28.6, 40.8, 58.5, 72.8, 88.4, 103.2, 114.1, 124.2, 134.5]  # what the real export showed
    with engine.begin() as c:
        c.execute(sa.text("INSERT INTO routes (id, user_id, name, course_json, total_distance_km, total_elevation_gain, total_elevation_loss, sport_type, created_at) VALUES (1, 1, 'Transjeju 100M', '{}', 145.6, 5078, 5084, 'trail', CURRENT_TIMESTAMP)"))
        for i, km in enumerate(drifted):
            c.execute(sa.text("INSERT INTO route_checkpoints (route_id, name, distance_km, kind, crew, drop_bag, stop_s) VALUES (1, 'x', :k, 'full', 0, 0, :s)"), {"k": km, "s": 600 if i == 9 else None})
    with engine.begin() as c:
        assert mig.apply(c)["routes"] == 1
        rows = c.execute(sa.text("SELECT name, distance_km, stop_s FROM route_checkpoints WHERE route_id = 1 ORDER BY distance_km")).all()
    kms = {n: k for n, k, _ in rows}
    assert kms["Meochewat Forest Trail"] == 106.4 and kms["Eseungak Oreum"] == 115.8 and kms["Seogwipo Student Camping site"] == 127.6
    assert rows[9][2] == 600 and [r[1] for r in rows][-1] == 137.1
