"""store the effort D+ on the courses saved before it existed

The race calibration reads the course's D+ with the algorithm used on the
athlete's races (gpx.profile_elevation_gain, ``dplus_effort`` in course_json).
Courses saved before carry none, so every prediction on them recomputed it
from the full trace (~20 ms on a 22 000-point course, twice per page). Computed
once here; a course whose trace has no elevation keeps none (the displayed
total applies). Idempotent.

Revision ID: v6e7f8a9b0c1
Revises: u5d6e7f8a9b0
Create Date: 2026-10-05 01:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = 'v6e7f8a9b0c1'
down_revision: Union[str, None] = 'u5d6e7f8a9b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def apply(bind) -> int:
    from app.services.race_calibration import dplus_from_route_coords

    routes = sa.table(
        "routes", sa.column("id", sa.Integer), sa.column("course_json", sa.JSON().with_variant(JSONB, "postgresql")),
    )
    ids = [r.id for r in bind.execute(sa.select(routes.c.id).where(routes.c.course_json.is_not(None)).order_by(routes.c.id))]
    done = 0
    for rid in ids:  # one course at a time: a trace weighs a few MB
        cj = bind.execute(sa.select(routes.c.course_json).where(routes.c.id == rid)).scalar()
        if not isinstance(cj, dict) or cj.get("dplus_effort") is not None:
            continue
        gain = dplus_from_route_coords(cj.get("route_coords"), cj.get("total_elevation_gain"))
        if gain is None:
            continue
        bind.execute(routes.update().where(routes.c.id == rid).values(course_json={**cj, "dplus_effort": gain}))
        done += 1
    return done


def upgrade() -> None:
    apply(op.get_bind())


def downgrade() -> None:
    pass  # an extra key the previous code ignores
