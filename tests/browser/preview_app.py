"""Synthetic dashboard for browser regressions; bind to localhost only."""
import os
from tempfile import TemporaryDirectory
os.environ.update(DEBUG='false',COROS_SYNC='false',GARMIN_SYNC='false',ACTIVITY_ENV_FETCH='false')
from contextlib import asynccontextmanager
from datetime import datetime,timezone,timedelta
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from app.main import create_app
from app.database import Base
from app.dependencies import get_db,get_current_user
from app.models.user import User
from app.models.activity import Activity
from app.models.health import HealthMetric
from app.models.route import Route, RouteCheckpoint
from app.services.health import HRV_METHOD
from tests.test_race_plan_services import CPS, _course

# Independent connections prevent overlapping requests from releasing each other's savepoints.
preview_db = TemporaryDirectory(prefix='paceforge-preview-')
engine=create_async_engine(f'sqlite+aiosqlite:///{preview_db.name}/preview.sqlite')
factory=async_sessionmaker(engine,expire_on_commit=False)
@asynccontextmanager
async def lifespan(app):
    async with engine.begin() as c: await c.run_sync(Base.metadata.create_all)
    async with factory() as db:
        user=User(firstname='Camille');db.add(user);await db.flush();app.state.user_id=user.id
        now=datetime.now(timezone.utc)
        course = _course()
        route = Route(user_id=user.id, name='Course test UX', course_json=course.model_dump(),
                      total_distance_km=course.total_distance_km,
                      total_elevation_gain=course.total_elevation_gain,
                      total_elevation_loss=course.total_elevation_loss,
                      target_time_s=18000, start_hour=6, start_minute=0,
                      race_date=(now.date()+timedelta(days=30)).isoformat(), sport_type='trail')
        db.add(route)
        await db.flush()
        for cp in CPS:
            db.add(RouteCheckpoint(route_id=route.id, **cp))
        for i in range(150):
            day=now.date()-timedelta(days=i)
            for metric,value,det in [('sleep',460,dict(main_start=str(day-timedelta(days=1))+'T23:00',main_end=str(day)+'T07:00',
                timeline=False,stages=dict(awake=20,light=280,deep=90,rem=90))),
                ('hr_night',50+i%5,{}),('hrv',62+i%7,dict(method=HRV_METHOD,n=20))]:
                db.add(HealthMetric(user_id=user.id,date=day,metric=metric,value=value,source='COROS',details=det))
            if i < 30:
                for metric,value in [('weight',64.2+(i%7)/10),('body_fat',17.4+(i%4)/10),('steps',8240+i%8*350)]:
                    db.add(HealthMetric(user_id=user.id,date=day,metric=metric,value=value,source='COROS',details={}))
            if i%3==1:
                minutes=50+i%4*4
                db.add(Activity(user_id=user.id,strava_activity_id=i+1,name='Course facile',sport_type='Run',start_date=now-timedelta(days=i),distance=10000,moving_time=minutes*60,elapsed_time=minutes*60,average_heartrate=130+i%4,average_speed=10000/(minutes*60),max_heartrate=180,total_elevation_gain=20,raw_data={'utc_offset':0}))
        await db.commit()
    yield
    await engine.dispose()
    preview_db.cleanup()
app=create_app();app.router.lifespan_context=lifespan
async def session():
    async with factory() as db:
        yield db
        await db.commit()
async def user():
    async with factory() as db:return await db.get(User,app.state.user_id)
app.dependency_overrides[get_db]=session;app.dependency_overrides[get_current_user]=user
