"""Transjeju 100M: the official 2026 trace, checkpoints placed on it

The route carried the 2025 GPX (145,6 km), whose finish is 3 km short of the
2026 course. The organisation's 2026 file (« 2026 TransJeju 100M 최종_260927 »,
147,9 km, ≈ 4 800 m D+) replaces the geometry; name, objective, conditions,
nutrition and stop times are untouched.

The file measures 0,5 % less than the roadbook's 148,7 km, evenly: each
checkpoint goes at its roadbook km scaled to the file, then onto the nearby
spot (± 600 m) whose altitude matches the roadbook. Every point lands within
2 m of its official altitude, and the two Healing Forest passages 116 m apart.
Idempotent.

Revision ID: p0e1f2a3b4c5
Revises: o9d0e1f2a3b4
"""
import gzip
import json
import pathlib
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = 'p0e1f2a3b4c5'
down_revision: Union[str, None] = 'o9d0e1f2a3b4'
branch_labels = None
depends_on = None

ROUTE_MATCH = "%transjeju%"
GPX = pathlib.Path(__file__).resolve().parents[1] / "data" / "transjeju_2026_100m.gpx.gz"
OFFICIAL_TOTAL_KM = 148.7
WINDOW_KM = 0.6
# name, roadbook km, altitude, cutoff, kind, drop bag
OFFICIAL = [
    ("Healing Forest", 7.1, 419, "22:40", "water", False),
    ("Yeongsil Entrance", 20.2, 1211, "02:40", "full", False),
    ("Eorimok Entrance", 28.6, 953, "05:10", "full", False),
    ("Gwanumsa Entrance", 40.8, 572, "07:10", "full", False),
    ("Seongpanak Entrance", 58.5, 762, "12:40", "full", False),
    ("Jeongseok Aviation Pavilion", 74.3, 351, "15:40", "water", False),
    ("Gasiri", 86.9, 273, "18:40", "base", True),
    ("Meochewat Forest Trail", 106.4, 239, "22:40", "full", False),
    ("Eseungak Oreum", 115.8, 413, "01:00", "water", False),
    ("Seogwipo Student Camping site", 127.6, 489, "04:00", "base", True),
    ("Healing Forest", 137.1, 419, "07:00", "water", False),
]


def build_course() -> dict:
    """The 2026 file as the app stores a course (same builder as an upload)."""
    from app.services.gpx import build_course_profile, parse_gpx

    points, _ = parse_gpx(gzip.decompress(GPX.read_bytes()))
    return json.loads(build_course_profile(points, name="2026 Transjeju 100M").model_dump_json())


def place_checkpoints(course: dict) -> list[dict]:
    """Each roadbook point at its scaled km, nudged onto the matching altitude."""
    total = float(course["total_distance_km"])
    prof = [(float(c[2]), float(c[3])) for c in course.get("route_coords") or [] if len(c) > 3 and c[3] is not None]
    scale = total / OFFICIAL_TOTAL_KM
    out, prev = [], 0.0
    for name, km, alt, cutoff, kind, bag in OFFICIAL:
        target = km * scale
        near = [(x, e) for x, e in prof if abs(x - target) <= WINDOW_KM and x > prev + 0.3]
        pos = min(near, key=lambda p: abs(p[1] - alt) / 10 + abs(p[0] - target) / 0.3)[0] if near else target
        pos = round(min(pos, total - 0.5), 2)
        prev = pos
        out.append({"name": name, "distance_km": pos, "elevation": float(alt), "kind": kind, "crew": bag, "drop_bag": bag, "cutoff_clock": cutoff})
    return out


def apply(bind, course: dict | None = None) -> dict:
    routes = sa.table(
        "routes", sa.column("id", sa.Integer), sa.column("name", sa.String), sa.column("sport_type", sa.String),
        sa.column("course_json", sa.JSON().with_variant(JSONB, "postgresql")), sa.column("total_distance_km", sa.Float),
        sa.column("total_elevation_gain", sa.Float), sa.column("total_elevation_loss", sa.Float),
    )
    cps = sa.table(
        "route_checkpoints", sa.column("id", sa.Integer), sa.column("route_id", sa.Integer), sa.column("name", sa.String),
        sa.column("distance_km", sa.Float), sa.column("elevation", sa.Float), sa.column("kind", sa.String),
        sa.column("crew", sa.Boolean), sa.column("drop_bag", sa.Boolean), sa.column("cutoff_clock", sa.String), sa.column("stop_s", sa.Integer),
    )
    ids = [r.id for r in bind.execute(sa.select(routes.c.id).where(sa.func.lower(routes.c.name).like(ROUTE_MATCH), routes.c.sport_type != "bike"))]
    if not ids:
        return {"routes": 0}
    course = course or build_course()
    new = place_checkpoints(course)
    for rid in ids:
        bind.execute(routes.update().where(routes.c.id == rid).values(
            course_json=course, total_distance_km=course["total_distance_km"],
            total_elevation_gain=course["total_elevation_gain"], total_elevation_loss=course["total_elevation_loss"],
        ))
        old = [row.stop_s for row in bind.execute(sa.select(cps.c.stop_s).where(cps.c.route_id == rid).order_by(cps.c.distance_km))]
        stops = old if len(old) == len(new) else [None] * len(new)
        bind.execute(cps.delete().where(cps.c.route_id == rid))
        for cp, st in zip(new, stops):
            bind.execute(cps.insert().values(route_id=rid, stop_s=st, **cp))
    return {"routes": len(ids)}


def upgrade() -> None:
    apply(op.get_bind())


def downgrade() -> None:
    pass
