"""Shared identity of an imported activity and a race linked to a saved plan."""
from sqlalchemy import BigInteger, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.route import Route


def races_on_retained_copies(races: dict[int, str], duplicates: dict[int, int]) -> dict[int, str]:
    """An explicit result link follows its outing when the richer recording is selected for calculations."""
    result = dict(races)
    for linked, name in races.items():
        result.setdefault(duplicates.get(linked, linked), name)
    return result


def revision_sum(db: AsyncSession):
    """Persistent import revisions invalidate every worker, including equal-size JSON corrections."""
    value = Activity.raw_data["paceforge_revision"].as_string()
    if db.get_bind().dialect.name == "postgresql":
        # Old/malformed raw payloads must not turn a cache read into a cast error.
        value = case((value.op("~")(r"^[0-9]{1,12}$"), cast(value, BigInteger)), else_=0)
    else:
        value = cast(value, BigInteger)
    return func.coalesce(func.sum(value), 0)


async def confirmed_races(db: AsyncSession, user_id: int, *, since=None, activity_ids=None) -> dict[int, str]:
    """Only a real activity linked as a result, with both records owned by this athlete.

    No calendar date or planned duration can declare that the athlete ran a race.
    Keep the provider's tag untouched; unlinking the result removes this identity.
    """
    q = select(Activity.id, Route.name).join(Route, Route.result_activity_id == Activity.id).where(
        Activity.user_id == user_id, Route.user_id == user_id,
        Activity.sport_type.in_(("Run", "TrailRun", "VirtualRun")),
        Route.sport_type.in_(("trail", "run", "road")),
    ).order_by(Route.id)
    if since is not None:
        q = q.where(Activity.start_date >= since)
    if activity_ids is not None:
        if not activity_ids:
            return {}
        q = q.where(Activity.id.in_(activity_ids))
    return {aid: name for aid, name in (await db.execute(q)).all()}
