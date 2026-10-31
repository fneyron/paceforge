from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.models.health import HealthMetric
from app.models.user import User
from app.services import apple_nights as apple, health_consent, nights
from app.services.health import HRV_METHOD
from app.services.sante import health_page
from tests.test_mobile import paired


def packet():
    end = datetime.now(timezone.utc).replace(hour=7, minute=0, second=0, microsecond=0)-timedelta(days=1)
    start = end-timedelta(hours=8)
    return dict(date=end.date().isoformat(), source_id='a'*64, source_name='Apple Watch', timezone='UTC',
                sleep=[dict(start=start.isoformat(),end=end.isoformat(),stage='core')], naps=[],
                hr=[dict(at=(start+timedelta(minutes=20*i)).isoformat(),value=50) for i in range(24)],
                hrv_sdnn=[dict(at=(start+timedelta(hours=i)).isoformat(),value=40) for i in range(8)],
                resp=[dict(at=(start+timedelta(hours=i)).isoformat(),value=14) for i in range(8)])


def test_awake_unknown_duplicate_and_sparse_samples():
    p = packet(); start=datetime.fromisoformat(p['sleep'][0]['start'])
    # Generic sleep cannot override explicit wake or double-count staged sleep.
    p['sleep'] += [dict(p['sleep'][0],stage='asleep'),dict(start=(start+timedelta(hours=2)).isoformat(),
                    end=(start+timedelta(hours=3)).isoformat(),stage='awake')]
    p['hr'] += p['hr'][:]
    p['hr'] += [dict(at=(start-timedelta(hours=1)).isoformat(),value=220)]
    p['hr'][6]['value'] = 200  # asleep? no: explicit wake, must not contribute
    result = {r.metric:r for r in apple.reduce_night(apple.AppleNight(**p))}
    assert result['sleep'].value == 420
    assert result['hr_night'].value == 50
    assert result['hr_night'].details['n'] == 21
    assert result['hrv'].details['method'] == apple.SDNN_METHOD
    assert result['hrv'].value == pytest.approx(40)
    p['hrv_sdnn'] = p['hrv_sdnn'][:2]
    assert 'hrv' not in {r.metric for r in apple.reduce_night(apple.AppleNight(**p))}
    p['hr'] = [dict(at=(start+timedelta(seconds=i)).isoformat(),value=50) for i in range(20)]
    assert 'hr_night' not in {r.metric for r in apple.reduce_night(apple.AppleNight(**p))}


def test_dst_uses_elapsed_time_and_timezone_wake_day():
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):
            return datetime(2026,11,2,12,tzinfo=timezone.utc)
    # Europe/Paris fall back: local 23:00 -> 07:00, nine real hours.
    p=packet(); p.update(date='2026-10-25',timezone='Europe/Paris',sleep=[dict(
        start='2026-10-24T21:00:00Z',end='2026-10-25T06:00:00Z',stage='asleep')],hr=[],hrv_sdnn=[],resp=[])
    with patch.object(apple,'datetime',Clock):
        result=apple.reduce_night(apple.AppleNight(**p))
    assert result[0].value == 540
    assert result[0].details['main_start'] == '2026-10-24T23:00:00'
    assert result[0].details['main_end'] == '2026-10-25T07:00:00'
    # Local next day in Tokyo despite yesterday in UTC.
    p=packet(); end=datetime.fromisoformat(p['sleep'][0]['end']).replace(hour=22)
    p.update(date=(end+timedelta(hours=9)).date().isoformat(),timezone='Asia/Tokyo',
        sleep=[dict(start=(end-timedelta(hours=8)).isoformat(),end=end.isoformat(),stage='asleep')],hr=[],hrv_sdnn=[],resp=[])
    assert apple.AppleNight(**p).date == (end+timedelta(hours=9)).date()


@pytest.mark.parametrize('change',[
    {'source_id':'not-a-hash'},{'timezone':'somewhere'},{'user_id':1},{'hr':[{'at':'2026-01-01','value':55}]},
    {'date':'2020-01-01'}, {'hrv_sdnn':[{'at':'2026-10-01T00:00:00Z','value':float('inf')}]},
    {'sleep':[],'naps':[]},
])
def test_reject_bad_packets(change):
    with pytest.raises(ValidationError):
        apple.AppleNight(**(packet()|change))


async def test_ingest_is_scoped_idempotent_and_separated_from_rmssd(client,db_session,test_user):
    account,csrf = await paired(client,test_user)
    headers={'Authorization':'Bearer '+account['token']}; p=packet(); d=apple.AppleNight(**p).date
    assert (await client.post('/api/mobile/nights',json=p)).status_code==401
    for _ in range(2):
        r=await client.post('/api/mobile/nights',json=p,headers=headers)
        assert r.status_code==200 and r.json()['received']==4
    assert await db_session.scalar(select(func.count(HealthMetric.id)))==4
    row=await db_session.scalar(select(HealthMetric).where(HealthMetric.metric=='sleep'))
    db_session.add(HealthMetric(user_id=test_user.id,date=d,metric='sleep',value=430,source='COROS',details=row.details))
    db_session.add(HealthMetric(user_id=test_user.id,date=d,metric='hrv',value=100,source='COROS',details={'method':HRV_METHOD}))
    other=User(firstname='Other'); health_consent.record(other); db_session.add(other); await db_session.flush()
    await apple.ingest(db_session,other.id,apple.AppleNight(**p))
    rows=await nights.read_rows(db_session,test_user.id,d,d)
    assert rows['sleep'][d][2]=='COROS' and rows['hrv'][d][0]==100
    test_user.health_source='Apple Santé'; await db_session.flush()
    rows=await nights.read_rows(db_session,test_user.id,d,d)
    n=nights.build_nights(rows,d)[d]
    assert n.hrv == pytest.approx(40) and n.hrv_source.endswith('(SDNN sommeil)')
    assert n.hr==50 and n.asleep==480
    page=await health_page(db_session,test_user.id,today=d)
    assert page['has_data'] and page['sleep']['hero']
    assert 'a'*64 not in page['vfc']['data']
    # Correcting sleep must invalidate the old, differently filtered heart summaries.
    p['sleep'][0]['start']=(datetime.fromisoformat(p['sleep'][0]['start'])+timedelta(minutes=30)).isoformat()
    p.update(hr=[],hrv_sdnn=[],resp=[])
    assert (await client.post('/api/mobile/nights',json=p,headers=headers)).status_code==200
    db_session.expire_all()
    rows=await nights.read_rows(db_session,account['user_id'],d,d)
    assert rows['hrv'][d][2]=='COROS'  # valid fallback, never stale Apple SDNN
    # Removing mobile imports doesn't remove direct watch data or another athlete.
    r=await client.post('/mobile/data/delete',data={'csrf_token':csrf})
    assert r.status_code==303
    assert await db_session.scalar(select(func.count(HealthMetric.id)).where(HealthMetric.source.startswith(apple.PREFIX)))==4
    assert await db_session.scalar(select(func.count(HealthMetric.id)).where(HealthMetric.source=='COROS'))==2


async def test_apple_sleep_only_never_fabricates_heart_values(client,test_user,db_session):
    account,_=await paired(client,test_user); p=packet(); p.update(hr=[],hrv_sdnn=[],resp=[])
    r=await client.post('/api/mobile/nights',json=p,headers={'Authorization':'Bearer '+account['token']})
    assert r.status_code==200 and r.json()['received']==1
    d=apple.AppleNight(**p).date
    rows=await nights.read_rows(db_session,test_user.id,d,d)
    n=nights.build_nights(rows,d)[d]
    assert n.hrv is None and n.hr is None


def test_sdnn_reference_uses_own_variance_and_never_rmssd_score():
    import math
    import statistics
    from datetime import date
    from app.services import sante
    d=date(2026,10,12)
    source=apple.PREFIX+'a'*64+' (SDNN sommeil)'
    ns={}
    for i in range(30):
        day=d-timedelta(days=29-i)
        ns[day]=nights.Night(day,hrv=40+i%5,hrv_source=source)
    band=nights.normal(ns,'hrv',d)
    # owner, 2026-10-14: scientific basis — the baseline is days -67 to -8 (it held the week its trend judges)
    assert band['sd']==pytest.approx(statistics.stdev(math.log(n.hrv) for x,n in ns.items() if x<=d-timedelta(days=8)))
    assert nights.normal(dict(list(ns.items())[-13:]),'hrv',d) is None
    score=sante._assess(ns,[],[],d)['score']
    assert score['value'] is None and 'hrv' not in [p['key'] for p in score['parts']]
    # An identical numeric RMSSD series still follows its existing independent method.
    for n in ns.values(): n.hrv_source='COROS'
    assert sante._assess(ns,[],[],d)['score']['value'] is not None


def test_naps_preserve_each_episodes_measured_duration_and_reject_overlap():
    p=packet(); end=datetime.fromisoformat(p['sleep'][0]['end'])
    a,b=end+timedelta(hours=6),end+timedelta(hours=9)
    p['naps']=[[dict(start=a.isoformat(),end=(a+timedelta(hours=1)).isoformat(),stage='asleep'),
                 dict(start=a.isoformat(),end=(a+timedelta(minutes=30)).isoformat(),stage='awake')],
                [dict(start=b.isoformat(),end=(b+timedelta(hours=1)).isoformat(),stage='asleep')]]
    row=next(r for r in apple.reduce_night(apple.AppleNight(**p)) if r.metric=='nap')
    parts=nights._nap_parts(row.day,row.value,row.details)
    assert [part[2] for part in parts]==[30,60] and row.value==90
    p['naps']=[p['sleep']]
    with pytest.raises(ValueError): apple.reduce_night(apple.AppleNight(**p))


async def test_apple_night_timezone_can_establish_local_day_without_activities(db_session,test_user):
    from app.services import sante_training
    p=packet(); p.update(timezone='Asia/Tokyo')
    end=datetime.fromisoformat(p['sleep'][0]['end']).replace(hour=22)
    p.update(date=(end+timedelta(hours=9)).date().isoformat(),sleep=[dict(start=(end-timedelta(hours=8)).isoformat(),
        end=end.isoformat(),stage='asleep')],hr=[],hrv_sdnn=[],resp=[])
    await apple.ingest(db_session,test_user.id,apple.AppleNight(**p))
    assert await sante_training.utc_offset(db_session,test_user.id)==9*3600
    clock=end.replace(hour=23)
    assert await sante_training.athlete_today(db_session,test_user.id,clock)==(clock+timedelta(hours=9)).date()
