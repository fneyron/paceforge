"""The daily dashboard: a summary of existing readings, never a new health score."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.mobile import MobileDaily, MobileDevice
from app.models.user import User
from app.services import sante_training as st
from app.services import viz
from app.services.activity_dedupe import GROUP_LABELS, sport_group
from app.services.health import _ago
from app.services.strava import strava_status


def source_state(name: str, link: dict) -> dict:
    if link.get("needs_reauth"):
        state = "À reconnecter"
    elif link.get("syncing"):
        state = "Synchronisation en cours"
    elif link.get("last_error") or link.get("interrupted"):
        state = "Synchronisation à vérifier"
    else:
        state = "Connecté"
    return {"name": name, "state": state, "last": link.get("last_sync_ago") or "pas encore",
            "attention": bool(link.get("needs_reauth") or link.get("last_error") or link.get("interrupted"))}


async def overview(db: AsyncSession, user: User, page: dict | None, coros: dict, garmin: dict) -> dict:
    today = (page or {}).get("calendar_day") or await st.athlete_today(db, user.id)
    since = today - timedelta(days=6)
    # Reuse the same local dates, duplicate rules and moving time as Santé's activity link.
    sessions = sorted((s for s in await st.load_sessions(db, user.id, today) if s.end_day <= today),
                      key=lambda s: s.start, reverse=True)
    week = [s for s in sessions if s.end_day >= since]
    latest = sessions[:3]
    origins = (await db.execute(select(Activity.id, Activity.coros_activity_id, Activity.garmin_activity_id,
                                      Activity.strava_activity_id).where(
        Activity.user_id == user.id, Activity.id.in_([s.id for s in latest])))).all()
    sources = {r.id: " · ".join(name for name, key in (("COROS", r.coros_activity_id),
               ("Garmin", r.garmin_activity_id), ("Strava", r.strava_activity_id)) if key is not None)
               or "Import" for r in origins}
    activities = [{"id": s.id, "name": s.name, "sport": GROUP_LABELS[sport_group(s.sport)],
                   "date": st.local_start(s).strftime("%d/%m/%Y · %H:%M"), "source": sources[s.id],
                   "duration": viz.hm(s.minutes), "distance": viz.num(s.km, 1, "km")} for s in latest]
    cards = {c["key"]: c for c in (page or {}).get("daily", []) if not c.get("archived")}
    daily = [cards[k] for k in ("steps", "weight", "body_fat", "hr_day", "resp_night", "resp_day", "stress")
             if k in cards]
    links = [source_state(name, link) for name, link in (
        ("COROS", coros), ("Garmin", garmin), ("Strava", strava_status(user))) if link.get("connected")]
    now = datetime.now(timezone.utc)
    for platform, name in (("ios", "Apple Santé"), ("android", "Health Connect")):
        count, last = (await db.execute(select(func.count(MobileDevice.id), func.max(MobileDevice.last_sync_at)).where(
            MobileDevice.user_id == user.id, MobileDevice.platform == platform,
            MobileDevice.token_hash.is_not(None), MobileDevice.expires_at > now))).one()
        if count:
            links.append({"name": name, "state": "Téléphone associé", "last": _ago(last) or "pas encore",
                          "attention": False})
        elif await db.scalar(select(MobileDaily.id).where(
                MobileDaily.user_id == user.id, MobileDaily.platform == platform).limit(1)):
            links.append({"name": name, "state": "Historique conservé · téléphone à reconnecter",
                          "last": None, "attention": True})
    return {"date": today.strftime("%d/%m/%Y"), "since": since.isoformat(),
            "week": {"count": len(week), "duration": viz.hm(sum(s.minutes for s in week)),
                     "distance": viz.num(sum(s.km for s in week), 1, "km")},
            "activities": activities, "daily": daily, "sources": links,
            "attention": any(link["attention"] for link in links)}
