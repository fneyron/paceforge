"""Transjeju 100M: the owner's race-day nutrition plan (data migration)

Adds the products he races with (if missing from his pantry) and ticks them
on his Transjeju plan with the rates the planner computes, caffeinated gels
on the caffeine plan. Idempotent; a no-op on any other database.

Revision ID: h2c3d4e5f6a7
Revises: g1b2c3d4e5f6
"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = 'h2c3d4e5f6a7'
down_revision: Union[str, None] = 'g1b2c3d4e5f6'
branch_labels = None
depends_on = None

OWNER_EMAIL = "florent.neyron@gmail.com"
ROUTE_MATCH = "%transjeju%"
JSONType = sa.JSON().with_variant(JSONB, "postgresql")

# name, kind, carbs_g, sodium_mg, kcal, caffeine_mg, volume_ml, rate per hour
# (the caffeinated gel's rate is a placeholder: the caffeine plan places it)
PRODUCTS = [
    ("Precision Fuel PF 90 Gel", "gel", 90, 0, 360, None, None, 0.5),
    ("Baouw Gel", "gel", 30, 0, 120, None, None, 0.5),
    ("Maurten Gel 160", "gel", 40, 30, 160, None, None, 0.5),
    ("Maurten Gel 100 CAF 100", "gel", 25, 20, 100, 100, None, 1.0),
    ("Precision Hydration PH 1500 (pastille, 500 ml)", "salt", 0, 750, None, None, None, 1.0),
]
CAFFEINE = {"enabled": True, "from_h": 3.0, "every_h": 2.5, "dose_mg": 100, "boost_dawn": True}
DEFAULT_TARGETS = {"carbs_g_per_h": 75, "fluid_ml_per_h": 650, "sodium_mg_per_h": 575}


def apply(bind) -> dict:
    users = sa.table("users", sa.column("id", sa.Integer), sa.column("email", sa.String))
    uid = bind.execute(sa.select(users.c.id).where(sa.func.lower(users.c.email) == OWNER_EMAIL)).scalar()
    if not uid:
        return {"user": None, "created": [], "routes": 0}
    products = sa.table(
        "nutrition_products", sa.column("id", sa.Integer), sa.column("user_id", sa.Integer), sa.column("name", sa.String),
        sa.column("kind", sa.String), sa.column("carbs_g", sa.Float), sa.column("sodium_mg", sa.Float), sa.column("kcal", sa.Float),
        sa.column("caffeine_mg", sa.Float), sa.column("volume_ml", sa.Float),
    )
    have = {(r.name or "").strip().lower(): r.id for r in bind.execute(sa.select(products.c.id, products.c.name).where(products.c.user_id == uid))}
    items, created = [], []
    for name, kind, carbs, sodium, kcal, caf, vol, rate in PRODUCTS:
        key = name.lower()
        pid = have.get(key)
        if pid is None and "baouw" in key:  # his own Baouw entry, whatever he called it
            pid = next((i for n, i in have.items() if "baouw" in n), None)
        if pid is None:
            bind.execute(products.insert().values(user_id=uid, name=name, kind=kind, carbs_g=carbs, sodium_mg=sodium, kcal=kcal, caffeine_mg=caf, volume_ml=vol))
            pid = bind.execute(sa.select(products.c.id).where(products.c.user_id == uid, products.c.name == name)).scalar()
            have[key] = pid
            created.append(name)
        items.append({"product_id": int(pid), "per_hour": rate})
    routes = sa.table("routes", sa.column("id", sa.Integer), sa.column("user_id", sa.Integer), sa.column("name", sa.String), sa.column("nutrition_json", JSONType))
    updated = 0
    for r in bind.execute(sa.select(routes.c.id, routes.c.nutrition_json).where(routes.c.user_id == uid, sa.func.lower(routes.c.name).like(ROUTE_MATCH))):
        prev = dict(r.nutrition_json or {})
        new = {
            **prev,
            "targets": prev.get("targets") or DEFAULT_TARGETS,
            "items": items,
            "flask_capacity_ml": prev.get("flask_capacity_ml") or 1000,
            "refills": prev.get("refills") or [],
            "caffeine": {**(prev.get("caffeine") or {}), **CAFFEINE},
        }
        bind.execute(routes.update().where(routes.c.id == r.id).values(nutrition_json=new))
        updated += 1
    return {"user": uid, "created": created, "routes": updated}


def upgrade() -> None:
    apply(op.get_bind())


def downgrade() -> None:
    pass  # data only; his plan stays as it is
