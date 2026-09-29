"""Transjeju 100M: road / track / trail share per km, from OpenStreetMap

The plan now reads the course surface (services/surface): fetched in the
background for any trace. For the Transjeju it is stored now from the
OpenStreetMap extract of 29 Sep 2026, so the plan has it before race day
whatever Overpass does. Only for routes carrying the official 2026 trace
(same length); the rest get it from the background fetch. Idempotent.

Revision ID: q1f2a3b4c5d6
Revises: p0e1f2a3b4c5
"""
import json
import pathlib
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = 'q1f2a3b4c5d6'
down_revision: Union[str, None] = 'p0e1f2a3b4c5'
branch_labels = None
depends_on = None

ROUTE_MATCH = "%transjeju%"
DATA = pathlib.Path(__file__).resolve().parents[1] / "data" / "transjeju_2026_surface_km.json"


def apply(bind) -> dict:
    surface = json.loads(DATA.read_text())
    routes = sa.table(
        "routes", sa.column("id", sa.Integer), sa.column("name", sa.String), sa.column("sport_type", sa.String),
        sa.column("course_json", sa.JSON().with_variant(JSONB, "postgresql")),
    )
    updated = 0
    for r in bind.execute(sa.select(routes.c.id, routes.c.course_json).where(sa.func.lower(routes.c.name).like(ROUTE_MATCH), routes.c.sport_type != "bike")):
        cj = r.course_json if isinstance(r.course_json, dict) else json.loads(r.course_json or "{}")
        coords = (cj or {}).get("route_coords") or []
        # the official 2026 file only: its km count must match the data
        if not coords or int(coords[-1][2]) + 1 != len(surface):
            continue
        cj = dict(cj)
        cj["surface_km"] = surface
        bind.execute(routes.update().where(routes.c.id == r.id).values(course_json=cj))
        updated += 1
    return {"routes": updated}


def upgrade() -> None:
    apply(op.get_bind())


def downgrade() -> None:
    pass
