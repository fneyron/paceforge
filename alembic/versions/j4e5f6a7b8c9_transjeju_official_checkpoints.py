"""Transjeju 100M: the official checkpoints (data migration)

Replaces the checkpoints of the owner's Transjeju route(s) with the race's
official list: names, cutoffs, altitudes, kind of poste. The km along the
trace is kept from the existing checkpoint of the same name when there is
one (it was snapped to the GPX), otherwise the official km is scaled to the
trace length (the GPX measures 145,6 km where the organisation says 148,7).
Idempotent; a no-op on any other database.

Revision ID: j4e5f6a7b8c9
Revises: i3d4e5f6a7b8
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = 'j4e5f6a7b8c9'
down_revision: Union[str, None] = 'i3d4e5f6a7b8'
branch_labels = None
depends_on = None

OWNER_EMAIL = "florent.neyron@gmail.com"
ROUTE_MATCH = "%transjeju%"
OFFICIAL_TOTAL_KM = 148.7

# name, official km, altitude, cutoff (clock), kind, drop bag — from the organisation's table
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


def _key(name: str) -> str:
    return " ".join((name or "").lower().replace("-", " ").split()[:1])


def official_checkpoints(existing: list[dict], total_km: float) -> list[dict]:
    """The official list positioned on the trace: same-name existing km when close, else scaled official km."""
    scale = (total_km / OFFICIAL_TOTAL_KM) if total_km and total_km > 0 else 1.0
    out = []
    for name, km, alt, cutoff, kind, bag in OFFICIAL:
        scaled = round(km * scale, 1)
        same = [cp for cp in existing if _key(cp["name"]) == _key(name) and abs(float(cp["distance_km"]) - scaled) <= 6.0]
        pos = min(same, key=lambda cp: abs(float(cp["distance_km"]) - scaled))["distance_km"] if same else scaled
        pos = round(float(pos), 1)
        if total_km and pos >= total_km - 0.1:
            pos = round(total_km - 0.5, 1)
        out.append({"name": name, "distance_km": pos, "elevation": float(alt), "kind": kind, "crew": bag, "drop_bag": bag, "cutoff_clock": cutoff})
    return out


def apply(bind) -> dict:
    users = sa.table("users", sa.column("id", sa.Integer), sa.column("email", sa.String))
    uid = bind.execute(sa.select(users.c.id).where(sa.func.lower(users.c.email) == OWNER_EMAIL)).scalar()
    if not uid:
        return {"user": None, "routes": 0}
    routes = sa.table("routes", sa.column("id", sa.Integer), sa.column("user_id", sa.Integer), sa.column("name", sa.String), sa.column("total_distance_km", sa.Float), sa.column("sport_type", sa.String))
    cps = sa.table(
        "route_checkpoints", sa.column("id", sa.Integer), sa.column("route_id", sa.Integer), sa.column("name", sa.String),
        sa.column("distance_km", sa.Float), sa.column("elevation", sa.Float), sa.column("kind", sa.String),
        sa.column("crew", sa.Boolean), sa.column("drop_bag", sa.Boolean), sa.column("cutoff_clock", sa.String),
    )
    updated = 0
    for r in bind.execute(sa.select(routes.c.id, routes.c.total_distance_km).where(routes.c.user_id == uid, sa.func.lower(routes.c.name).like(ROUTE_MATCH), routes.c.sport_type != "bike")):
        existing = [dict(row._mapping) for row in bind.execute(sa.select(cps.c.name, cps.c.distance_km).where(cps.c.route_id == r.id))]
        new = official_checkpoints(existing, float(r.total_distance_km or 0))
        bind.execute(cps.delete().where(cps.c.route_id == r.id))
        for cp in new:
            bind.execute(cps.insert().values(route_id=r.id, **cp))
        updated += 1
    return {"user": uid, "routes": updated}


def upgrade() -> None:
    apply(op.get_bind())


def downgrade() -> None:
    pass
