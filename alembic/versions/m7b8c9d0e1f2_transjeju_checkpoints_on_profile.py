"""Transjeju 100M: official checkpoints positioned on the trace's profile, not by distance

The GPX measures 145,6 km where the roadbook says 148,7, and the gap is not
spread evenly: a checkpoint put at « official km × ratio » lands on the wrong
hill (Eseungak read 548 m on the trace for 413 m in the roadbook). Each
checkpoint is now searched near its scaled km for the spot where the trace's
altitude and cumulative climb match the roadbook's. Names, kinds, cutoffs and
the official altitude come from the roadbook; a stop time set on a point is
kept. Idempotent.

Revision ID: m7b8c9d0e1f2
Revises: l6a7b8c9d0e1
"""
import json
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = 'm7b8c9d0e1f2'
down_revision: Union[str, None] = 'l6a7b8c9d0e1'
branch_labels = None
depends_on = None

ROUTE_MATCH = "%transjeju%"
OFFICIAL_TOTAL_KM = 148.7
OFFICIAL_TOTAL_GAIN = 4851
# name, km, altitude, cumulative D+, cutoff, kind, drop bag — the organisation's table
OFFICIAL = [
    ("Healing Forest", 7.1, 419, 341, "22:40", "water", False),
    ("Yeongsil Entrance", 20.2, 1211, 1176, "02:40", "full", False),
    ("Eorimok Entrance", 28.6, 953, 1656, "05:10", "full", False),
    ("Gwanumsa Entrance", 40.8, 572, 1814, "07:10", "full", False),
    ("Seongpanak Entrance", 58.5, 762, 3171, "12:40", "full", False),
    ("Jeongseok Aviation Pavilion", 74.3, 351, 3216, "15:40", "water", False),
    ("Gasiri", 86.9, 273, 3494, "18:40", "base", True),
    ("Meochewat Forest Trail", 106.4, 239, 3797, "22:40", "full", False),
    ("Eseungak Oreum", 115.8, 413, 4066, "01:00", "water", False),
    ("Seogwipo Student Camping site", 127.6, 489, 4449, "04:00", "base", True),
    ("Healing Forest", 137.1, 419, 4773, "07:00", "water", False),
]
WINDOW_KM = 8.0


def _profile(course: dict) -> list[tuple[float, float]]:
    """(km, altitude) along the trace, as fine as the stored course allows."""
    coords = course.get("route_coords") or []
    pts = [(float(c[2]), float(c[3])) for c in coords if len(c) > 3 and c[3] is not None]
    if len(pts) < 50:
        pts = [(float(p["distance_km"]), float(p["elevation"])) for p in course.get("elevation_points") or []]
    return sorted(pts)


def _cum_gain(pts: list[tuple[float, float]], hysteresis: float = 3.0) -> list[float]:
    """Cumulative climb with a small dead band, so GPS noise does not count as D+."""
    out, total, ref = [], 0.0, pts[0][1] if pts else 0.0
    for _, e in pts:
        if e > ref + hysteresis:
            total += e - ref
            ref = e
        elif e < ref - hysteresis:
            ref = e
        out.append(total)
    return out


def align_on_profile(pts: list[tuple[float, float]], total_km: float) -> list[dict]:
    """Each official checkpoint at the trace spot whose km, altitude and cumulative
    climb best match the roadbook, in race order."""
    if len(pts) < 2:
        return []
    gains = _cum_gain(pts)
    dscale = (total_km / OFFICIAL_TOTAL_KM) if total_km else 1.0
    gscale = (gains[-1] / OFFICIAL_TOTAL_GAIN) if gains[-1] else 1.0
    out, prev = [], 0.0
    for name, km, alt, cgain, cutoff, kind, bag in OFFICIAL:
        target = km * dscale
        best, best_cost = None, None
        for (x, e), g in zip(pts, gains):
            if x <= prev + 0.3 or abs(x - target) > WINDOW_KM or x >= total_km - 0.2:
                continue
            cost = ((x - target) / 2.5) ** 2 + ((g - cgain * gscale) / 120.0) ** 2 + ((e - alt) / 50.0) ** 2
            if best_cost is None or cost < best_cost:
                best, best_cost = x, cost
        pos = round(best if best is not None else min(max(target, prev + 0.5), total_km - 0.5), 1)
        prev = pos
        out.append({"name": name, "distance_km": pos, "elevation": float(alt), "kind": kind, "crew": bag, "drop_bag": bag, "cutoff_clock": cutoff})
    return out


def apply(bind) -> dict:
    routes = sa.table("routes", sa.column("id", sa.Integer), sa.column("name", sa.String), sa.column("total_distance_km", sa.Float),
                      sa.column("sport_type", sa.String), sa.column("course_json", sa.JSON))
    cps = sa.table(
        "route_checkpoints", sa.column("id", sa.Integer), sa.column("route_id", sa.Integer), sa.column("name", sa.String),
        sa.column("distance_km", sa.Float), sa.column("elevation", sa.Float), sa.column("kind", sa.String),
        sa.column("crew", sa.Boolean), sa.column("drop_bag", sa.Boolean), sa.column("cutoff_clock", sa.String), sa.column("stop_s", sa.Integer),
    )
    updated = 0
    for r in bind.execute(sa.select(routes.c.id, routes.c.total_distance_km, routes.c.course_json).where(sa.func.lower(routes.c.name).like(ROUTE_MATCH), routes.c.sport_type != "bike")):
        course = r.course_json if isinstance(r.course_json, dict) else json.loads(r.course_json or "{}")
        new = align_on_profile(_profile(course or {}), float(r.total_distance_km or 0))
        if not new:
            continue
        # a stop time the athlete set on a point survives (matched by order: same official list)
        old = [dict(row._mapping) for row in bind.execute(sa.select(cps.c.name, cps.c.stop_s).where(cps.c.route_id == r.id).order_by(cps.c.distance_km))]
        stops = [o.get("stop_s") for o in old] if len(old) == len(new) else [None] * len(new)
        bind.execute(cps.delete().where(cps.c.route_id == r.id))
        for cp, st in zip(new, stops):
            bind.execute(cps.insert().values(route_id=r.id, stop_s=st, **cp))
        updated += 1
    return {"routes": updated}


def upgrade() -> None:
    apply(op.get_bind())


def downgrade() -> None:
    pass
