"""Synthetic Coaching preview (beta): an athlete mid-plan with done, missed and ressenti data, and her coach;
temporary database, localhost only. Coaching is left at its default: every account has it.

The preview's « today » is the latest Thursday (the athlete's day is patched to it: the same screens whatever the
day the suite runs). The athlete trains on Tuesday, Thursday, Friday and Sunday for a trail race in 54 days (an
objective set on its page: « Objectif 5h00 »; her long runs carry a climb target). She turned « Adapter mes séances
à ma forme » on, and this Thursday morning Santé's index reads low (the last 7 nights' HRV under its usual values):
today's quality session is a footing, the session itself moved to Friday — her coach reads « Séance adaptée ce
matin », never why. She gave her work calendar's address in « Mon agenda » (shown masked): meetings from 9 to 12 and
from 14 to 17:30 on working days, and next Tuesday full — every session of the next 21 days has a time, and next
Tuesday's moved to Friday, which her coach had freed (« Mardi est plein : séance déplacée à vendredi. »).
This Tuesday's run arrived and its ressenti is not said yet: the calendar asks about it first (« Ta sortie de mardi
est arrivée — c’était comment ? »). Yesterday she also ran with a friend, outside the plan: a grey block on Wednesday,
offered as the week's session it may have been.

uvicorn tests.browser.coaching_preview:app --host 127.0.0.1 --port 8961
    PF_COACHING_AS=athlete (default) | coach | new   who is signed in: the athlete (Stella), her coach (Florent),
                                                     or a new account: no plan, no activity, no race (the onboarding)

A fourth account has no plan either but a habit (Hugo: Monday, Wednesday and Saturday for eight weeks, the long run
on Saturday, a race in 40 days): what the onboarding proposes from a history. A fixture signs it in on request —
POST /__preview/coaching/as/habit (and back: …/as/new) — so that no fourth server is needed.
"""
import os
from contextlib import asynccontextmanager
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import delete, select

from app.config import settings
from app.models.activity import Activity
from app.models.coaching import CoachingBusy, CoachingCalendar, CoachingLink, CoachingSession
from app.models.health import HealthMetric
from app.models.route import Route
from app.models.user import User
from app.services import coaching as co
from app.services import coaching_plan as cp
from app.services import sante_training
from tests.browser import preview_app as preview

AS = os.environ.get("PF_COACHING_AS", "athlete")
if AS not in {"athlete", "coach", "new"}:
    raise ValueError("PF_COACHING_AS is athlete, coach or new")
EMAILS = {"athlete": "camille@example.org", "coach": "coach@example.org", "new": "nouvelle@example.org"}
if settings.COACHING_BETA_USERS != "*":
    raise ValueError("This preview shows Coaching as every account has it: leave COACHING_BETA_USERS unset")
settings.BASE_URL = "https://paceforge.example"


@asynccontextmanager
async def lifespan(app):
    async with preview.lifespan(app):
        real = datetime.now(timezone.utc).date()
        today = real - timedelta(days=(real.weekday() - 3) % 7)  # the latest Thursday
        monday = cp.monday_of(today)

        async def athlete_today(*args, **kwargs):
            return today
        sante_training.athlete_today = athlete_today  # every page of this preview reads that Thursday
        noon = datetime.combine(today, time(8, 30), timezone.utc)
        async with preview.factory() as db:
            athlete = await db.get(User, app.state.user_id)
            athlete.firstname, athlete.max_heart_rate, athlete.timezone = "Stella", 186, "UTC"
            coach = User(firstname="Florent", email=EMAILS["coach"], email_verified=True, timezone="UTC")
            new = User(firstname="Nina", email=EMAILS["new"], email_verified=True, timezone="UTC")
            habit = User(firstname="Hugo", email="hugo@example.org", email_verified=True, timezone="UTC")
            db.add_all([coach, new, habit])
            await db.flush()
            # Hugo: eight weeks on Monday, Wednesday and Saturday (the long run), and a race to come
            for week in range(1, 9):
                for wd, name, minutes, km in ((0, "Footing", 40, 6.4), (2, "Fractionné", 50, 8.6),
                                              (5, "Sortie longue", 85 + 5 * (week % 2), 13.8)):
                    day = monday - timedelta(weeks=week) + timedelta(days=wd)
                    db.add(Activity(user_id=habit.id, strava_activity_id=800000 + week * 10 + wd, name=name,
                                    sport_type="Run", start_date=datetime.combine(day, time(7), timezone.utc),
                                    distance=km * 1000, moving_time=minutes * 60, elapsed_time=minutes * 60 + 60,
                                    total_elevation_gain=60, average_speed=km * 1000 / (minutes * 60),
                                    raw_data={"utc_offset": 0}))
            db.add(Route(user_id=habit.id, name="Semi des Vignes", total_distance_km=21.1, total_elevation_gain=180,
                         sport_type="road", race_date=(today + timedelta(days=40)).isoformat()))
            # the athlete's runs: the last 5 weeks on Tuesday, Thursday, Friday and Sunday, this week before today
            await db.execute(delete(Activity).where(Activity.user_id == athlete.id))
            n = 0
            for week in range(5, -1, -1):
                for wd, name, minutes, km, up in ((1, "Footing du mardi", 42, 6.9, 40), (3, "Côtes au parc", 47, 7.2, 160),
                                                  (4, "Footing du vendredi", 38, 6.2, 30),
                                                  (6, "Sortie longue en forêt", 78 - 2 * week, 12.4, 320)):
                    day = monday - timedelta(weeks=week) + timedelta(days=wd)
                    if day >= today or (week == 1 and wd == 3):
                        continue  # last Thursday's session was skipped: « pas faite »
                    n += 1
                    db.add(Activity(user_id=athlete.id, strava_activity_id=700000 + n, name=name, sport_type="TrailRun"
                                    if wd in (3, 6) else "Run", start_date=datetime.combine(day, time(7), timezone.utc),
                                    distance=km * 1000, moving_time=minutes * 60, elapsed_time=minutes * 60 + 90,
                                    total_elevation_gain=up, average_heartrate=141, max_heartrate=168,
                                    average_speed=km * 1000 / (minutes * 60), raw_data={"utc_offset": 0}))
            # yesterday, a run outside the plan (Wednesday is not one of her days)
            db.add(Activity(user_id=athlete.id, strava_activity_id=700900, name="Footing avec Léa", sport_type="Run",
                            start_date=datetime.combine(monday + timedelta(days=2), time(18), timezone.utc),
                            distance=5600, moving_time=35 * 60, elapsed_time=36 * 60, total_elevation_gain=25,
                            average_heartrate=134, max_heartrate=151, average_speed=5600 / (35 * 60),
                            raw_data={"utc_offset": 0}))
            route = (await db.execute(select(Route).where(Route.user_id == athlete.id))).scalars().first()
            route.name, route.race_date = "Trail des Crêtes", (today + timedelta(days=54)).isoformat()
            # an objective the athlete set on the race page (today's page marks its saves): « Objectif 5h00 »
            route.target_time_s, route.params_json = 5 * 3600, {"target_v": 2}
            await db.flush()
            # the plan was made 3 weeks ago: its past weeks are generated as they were then, then read against the runs
            start = today - timedelta(weeks=3)
            plan = await co.save_plan(db, athlete, co.Clock(datetime.now(timezone.utc), timezone.utc, start),
                                      route_id=route.id, days=[1, 3, 4, 6], long_day=6, base=200)
            goal = co.goal_of(await co.plan_route(db, plan), route.target_time_s)
            for week in cp.build(co.inputs(plan, goal), start):
                for s in week.sessions:
                    if start <= s.day < today:
                        db.add(CoachingSession(plan_id=plan.id, user_id=athlete.id, day=s.day, auto_day=s.day,
                                               kind=s.kind, minutes=s.minutes, structure=s.structure))
            await db.flush()
            state = await co.sync(db, plan, athlete)
            feel = {0: 3, 1: 2, 2: 3, 3: 4, 4: 3, 5: 2, 6: 3, 7: 4, 8: 3, 9: 3}
            done = [r for r in state.rows if r.status == co.DONE]
            for i, row in enumerate(done):
                # this week's run (Tuesday) waits for its debrief; the others have their ressenti
                row.ressenti = None if row.day >= monday else feel.get(i, 3)
            before = [r for r in done if r.day < monday]
            if before:  # last Sunday's long run: her note, her coach's comment
                before[-1].note = "Jambes lourdes au début, mieux après 20 minutes."
                before[-1].coach_comment = "Bien géré. Garde cette allure sur les côtes de jeudi."
            db.add(CoachingLink(athlete_id=athlete.id, coach_id=coach.id))
            await co.sync(db, plan, athlete)
            # « Adapter mes séances à ma forme », turned on a week ago; this morning Santé's index reads low (the
            # last 7 nights' HRV far under its usual values): today's quality session becomes a footing and moves
            for row in (await db.execute(select(HealthMetric).where(
                    HealthMetric.user_id == athlete.id, HealthMetric.metric == "hrv",
                    HealthMetric.date > today - timedelta(days=7), HealthMetric.date <= today))).scalars():
                row.value = 40
            plan.form_on_at = noon - timedelta(days=7)
            await db.flush()
            state = await co.sync(db, plan, athlete, noon)
            # her coach removed next Friday's session: an available day without a session
            friday = monday + timedelta(days=11)
            await co.remove(db, next(r for r in state.rows if r.day == friday), today, noon)
            # « Mon agenda »: her work calendar, read this morning (the preview has no worker: the busy time a
            # reading would keep is written here) — meetings 9–12 and 14–17:30 on working days, next Tuesday full
            calendar = CoachingCalendar(
                user_id=athlete.id, status="ok", fetched_at=noon, checked_at=noon,
                url="https://calendar.google.com/calendar/ical/stella%40example.org/private-preview/basic.ics")
            db.add(calendar)
            await db.flush()
            for k in range(21):
                day = today + timedelta(days=k)
                if day.weekday() >= 5:
                    continue
                spans = [(6, 0, 21, 30)] if day == monday + timedelta(days=8) else [(9, 0, 12, 0), (14, 0, 17, 30)]
                for h1, m1, h2, m2 in spans:
                    db.add(CoachingBusy(user_id=athlete.id, calendar_id=calendar.id,
                                        start_at=datetime.combine(day, time(h1, m1), timezone.utc),
                                        end_at=datetime.combine(day, time(h2, m2), timezone.utc)))
            await db.flush()
            await co.sync(db, plan, athlete, noon)
            await db.commit()
            ids = {"athlete": athlete.id, "coach": coach.id, "new": new.id, "habit": habit.id}
            app.state.coaching_ids = ids
            app.state.user_id = ids[AS]
            app.state.coaching_feed = plan.feed_token
        app.state.coaching_day = today.isoformat()
        yield


app = preview.app
app.router.lifespan_context = lifespan


@app.get("/__preview/coaching")
async def fixture():
    return {"synthetic": True, "as": AS, "day": app.state.coaching_day, "ids": app.state.coaching_ids,
            "feed": app.state.coaching_feed}


@app.post("/__preview/coaching/as/{who}")
async def sign_in_as(who: str):
    """This preview's signed-in account, changed on request (synthetic accounts of this temporary database only)."""
    app.state.user_id = app.state.coaching_ids[who]
    return {"synthetic": True, "as": who}
