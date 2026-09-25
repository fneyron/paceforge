"""Transjeju 100M: checkpoints pinned to the roadbook km

The profile search of the two previous passes still left Meochewat 3,2 km,
Camping 3,4 km and Eseungak 1,7 km short of the roadbook on the real trace,
which stretched some legs and shrank others (Eseungak → Camping planned 20 %
faster than the 2025 winner, Camping → finish 26 % slower). The 145,6 km GPX
is the 2025 course, identical to 2026 (km, D+, D−) up to the Camping site, so
the roadbook km are the right positions on it: no search, no scaling. Names,
kinds, cutoffs and the official altitude from the roadbook; a stop time set
on a point is kept. Idempotent.

Revision ID: o9d0e1f2a3b4
Revises: n8c9d0e1f2a3
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = 'o9d0e1f2a3b4'
down_revision: Union[str, None] = 'n8c9d0e1f2a3'
branch_labels = None
depends_on = None

ROUTE_MATCH = "%transjeju%"
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


def official_checkpoints(total_km: float) -> list[dict]:
    """The roadbook, each point at its official km (kept inside the trace)."""
    out = []
    for name, km, alt, cutoff, kind, bag in OFFICIAL:
        pos = km if not total_km or km < total_km - 0.2 else round(total_km - 0.5, 1)
        out.append({"name": name, "distance_km": float(pos), "elevation": float(alt), "kind": kind, "crew": bag, "drop_bag": bag, "cutoff_clock": cutoff})
    return out


def apply(bind) -> dict:
    routes = sa.table("routes", sa.column("id", sa.Integer), sa.column("name", sa.String), sa.column("total_distance_km", sa.Float), sa.column("sport_type", sa.String))
    cps = sa.table(
        "route_checkpoints", sa.column("id", sa.Integer), sa.column("route_id", sa.Integer), sa.column("name", sa.String),
        sa.column("distance_km", sa.Float), sa.column("elevation", sa.Float), sa.column("kind", sa.String),
        sa.column("crew", sa.Boolean), sa.column("drop_bag", sa.Boolean), sa.column("cutoff_clock", sa.String), sa.column("stop_s", sa.Integer),
    )
    updated = 0
    for r in bind.execute(sa.select(routes.c.id, routes.c.total_distance_km).where(sa.func.lower(routes.c.name).like(ROUTE_MATCH), routes.c.sport_type != "bike")):
        new = official_checkpoints(float(r.total_distance_km or 0))
        old = [dict(row._mapping) for row in bind.execute(sa.select(cps.c.stop_s).where(cps.c.route_id == r.id).order_by(cps.c.distance_km))]
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
