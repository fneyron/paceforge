import base64
import importlib.util
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from itsdangerous import TimestampSigner
from sqlalchemy import create_engine, func, inspect, select, text

from app.config import settings
from app.models.health import HealthMetric
from app.models.mobile import MobileDaily, MobileDevice
from app.models.user import User
from app.routers.mobile import digest
from app.services.sante_daily import daily_card, daily_context


def login(client, user):
    # Production sessions are Secure; exercise the browser handshake over HTTPS.
    client.base_url = "https://test"
    value = base64.b64encode(json.dumps({"user_id": user.id}).encode())
    client.cookies.set("paceforge_session", TimestampSigner(settings.SECRET_KEY).sign(value).decode())


async def paired(client, user):
    login(client, user)
    verifier = "v" * 64
    challenge = digest(verifier)
    response = await client.get(f"/mobile/connect?platform=ios&challenge={challenge}")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', response.text)[1]
    response = await client.post('/mobile/connect', data={"challenge": challenge, "platform": "ios", "csrf_token": csrf})
    assert response.status_code == 200
    code = re.search(r'"code": "([^"]+)"', response.text)[1]
    bad = await client.post('/api/mobile/exchange', json={"code": code, "verifier": "wrong" * 12})
    assert bad.status_code == 401
    response = await client.post('/api/mobile/exchange', json={"code": code, "verifier": verifier})
    assert response.status_code == 200
    replay = await client.post('/api/mobile/exchange', json={"code": code, "verifier": verifier})
    assert replay.status_code == 401
    return response.json(), csrf


def day(**overrides):
    now = datetime.now(timezone.utc)
    return {"date": now.date().isoformat(), "metric": "weight", "value": 64.2, "unit": "kg",
            "sources": ["Senssun Health"], "measured_at": now.isoformat(), **overrides}


async def test_pair_ingest_idempotent_and_watch_is_preserved(client, db_session, test_user):
    account, _ = await paired(client, test_user)
    assert account['user_id'] == test_user.id
    phone = await db_session.scalar(select(MobileDevice).where(MobileDevice.user_id == test_user.id))
    assert phone.token_hash == digest(account['token']) and phone.code_hash is None
    assert phone.token_hash != account['token']
    db_session.add(HealthMetric(user_id=test_user.id, date=date.today(), metric='hr_day', value=66, source='Garmin'))
    await db_session.flush()
    headers = {"Authorization": 'Bearer ' + account['token']}
    payload = {"days": [day(), day(metric='hr_day', value=78, unit='bpm', measured_at=None)]}
    for _ in range(2):
        response = await client.post('/api/mobile/daily', json=payload, headers=headers)
        assert response.status_code == 200 and response.json()['received'] == 2
    rows = (await db_session.scalars(select(MobileDaily).where(MobileDaily.user_id == test_user.id))).all()
    assert len(rows) == 2
    payload['days'][0]['value'] = 63.9
    await client.post('/api/mobile/daily', json=payload, headers=headers)
    cards = {c['key']: c for c in await daily_context(db_session, test_user.id, date.today())}
    assert cards['hr_day']['source'] == 'Garmin' and cards['hr_day']['value'].startswith('66')
    assert 'Senssun Health' in cards['weight']['source'] and '63,9' in cards['weight']['value']
    assert test_user.weight_kg is None  # manual sports-profile weight isn't silently changed
    assert (await db_session.scalar(select(func.count(HealthMetric.id)).where(HealthMetric.user_id == test_user.id))) == 1
    assert (await client.delete('/api/mobile/device', headers=headers)).status_code == 200
    assert (await client.post('/api/mobile/daily', json=payload, headers=headers)).status_code == 401


async def test_pair_requires_login_csrf_and_valid_challenge(client, db_session, test_user):
    assert (await client.get('/mobile/connect?platform=ios&challenge=' + 'a'*64)).status_code == 303
    login(client, test_user)
    assert (await client.post('/mobile/connect', data={'platform': 'ios', 'challenge': 'a'*64, 'csrf_token': 'bad'})).status_code == 403
    assert (await client.get('/mobile/connect?platform=ios&challenge=oops')).status_code == 400
    assert (await client.post('/api/mobile/daily', json={'days': []})).status_code == 401
    # Browser login alone never authorizes a health import.
    assert (await db_session.scalar(select(func.count(MobileDevice.id)))) == 0


@pytest.mark.parametrize('change', [
    {'value': 0}, {'unit': 'lb'}, {'value': 500}, {'measured_at': None},
    {'measured_at': '2020-01-01T00:00:00Z'}, {'sources': []}, {'metric': 'hrv'},
    {'date': '2020-01-01'}, {'user_id': 999},
])
async def test_ingest_rejects_wrong_units_dates_metrics_and_ownership(client, test_user, change):
    account, _ = await paired(client, test_user)
    response = await client.post('/api/mobile/daily', json={'days': [day(**change)]}, headers={'Authorization': 'Bearer ' + account['token']})
    assert response.status_code == 422


async def test_revoke_and_delete_are_scoped_to_current_user(client, db_session, test_user):
    account, csrf = await paired(client, test_user)
    other = User(firstname='Other')
    db_session.add(other); await db_session.flush()
    other_phone = MobileDevice(user_id=other.id, platform='android', token_hash=digest('other'), expires_at=datetime.now(timezone.utc)+timedelta(days=1))
    db_session.add(other_phone); await db_session.flush()
    await client.post('/mobile/devices/revoke', data={'device_id': other_phone.id, 'csrf_token': csrf})
    assert await db_session.get(MobileDevice, other_phone.id) is not None
    await client.post('/api/mobile/daily', json={'days': [day()]}, headers={'Authorization': 'Bearer '+account['token']})
    await client.post('/mobile/data/delete', data={'csrf_token': csrf})
    assert await db_session.scalar(select(func.count(MobileDaily.id)).where(MobileDaily.user_id == test_user.id)) == 0
    assert await db_session.scalar(select(func.count(MobileDevice.id)).where(MobileDevice.user_id == other.id)) == 1


def test_old_steps_have_a_date_no_fake_current_chart_and_no_trend():
    today = date(2026, 10, 10)
    c = daily_card('steps', [HealthMetric(date=date(2026, 8, 17), metric='steps', value=4000, source='Garmin')], today)
    assert c['stale'] and c['latest'] == '17/08/2026' and c['chart'] is None and c['trend'] is None


def test_daily_gaps_and_no_trend_across_a_watch_change():
    today = date(2026, 10, 10)
    rows = [HealthMetric(date=today-timedelta(days=i), metric='hr_day', value=65, source='Garmin' if i<5 else 'COROS') for i in range(1, 15)]
    c = daily_card('hr_day', rows, today)
    assert c['trend'] is None and c['chart']['n'] == 30
    assert len(c['chart']['dots']) == 14
    assert 'jours' in c['chart']['title']


def test_mobile_migration_roundtrip():
    from alembic.config import Config
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic.script import ScriptDirectory
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('mobile_migration', root/'alembic/versions/d4a5b6c7d8e9_mobile_daily.py')
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    assert ScriptDirectory.from_config(Config(str(root/'alembic.ini'))).get_current_head() == mod.revision
    engine = create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE users (id INTEGER PRIMARY KEY)'))
        with patch.object(mod, 'op', Operations(MigrationContext.configure(conn))):
            mod.upgrade()
            assert {'mobile_daily', 'mobile_devices'} <= set(inspect(conn).get_table_names())
            mod.downgrade()
            assert inspect(conn).get_table_names() == ['users']
