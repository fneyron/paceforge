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
from app.routers import mobile
from app.routers.mobile import digest
from app.services.sante_daily import daily_card, daily_context


def login(client, user):
    # Production sessions are Secure; exercise the browser handshake over HTTPS.
    client.base_url = "https://test"
    value = base64.b64encode(json.dumps({"user_id": user.id}).encode())
    client.cookies.set("paceforge_session", TimestampSigner(settings.SECRET_KEY).sign(value).decode())


def bridge_payload(html):
    return json.loads(re.search(r'<script id="paceforge-bridge-message" type="application/json">(.*?)</script>', html, re.S)[1])


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
    code = bridge_payload(response.text)['code']
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
    response = await client.post('/api/mobile/daily', json=payload, headers=headers)
    assert response.status_code == 200 and response.json()['received'] == 2
    # An identical re-send (each background run) is accepted but changes nothing.
    response = await client.post('/api/mobile/daily', json=payload, headers=headers)
    assert response.status_code == 200 and (response.json()['received'], response.json()['skipped']) == (0, 2)
    rows = (await db_session.scalars(select(MobileDaily).where(MobileDaily.user_id == test_user.id))).all()
    assert len(rows) == 2 and {r.device_id for r in rows} == {phone.id}
    payload['days'][0]['value'] = 63.9
    await client.post('/api/mobile/daily', json=payload, headers=headers)
    older = {"days": [day(value=61, measured_at=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat())]}
    assert (await client.post('/api/mobile/daily', json=older, headers=headers)).status_code == 200
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


async def test_session_bridge_is_private_retryable_and_scoped_to_logged_in_account(client, test_user):
    url = '/mobile/session?nonce=' + 'b' * 64
    assert (await client.get(url)).status_code == 303
    login(client, test_user)
    assert (await client.get('/mobile/session?nonce=bad')).status_code == 400
    response = await client.get(url)
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['referrer-policy'] == 'no-referrer'
    assert bridge_payload(response.text) == {'type': 'session', 'nonce': 'b' * 64, 'user_id': test_user.id}
    assert '/static/js/mobile-bridge.js' in response.text
    assert 'paceforge://devices' in response.text
    assert 'Compte vérifié' not in response.text


@pytest.mark.parametrize('json_transport', [False, True])
async def test_connections_bridge_requires_session_and_nonce_and_never_returns_tokens(client, db_session, test_user, json_transport):
    if json_transport:
        client.headers['Accept'] = 'application/json'
    url = '/mobile/connections?nonce=' + 'a' * 64
    assert (await client.get(url)).status_code == 303
    login(client, test_user)
    assert (await client.get('/mobile/connections?nonce=bad')).status_code == 400
    response = await client.get(url)
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    assert response.headers['referrer-policy'] == 'no-referrer'
    assert response.headers['x-frame-options'] == 'DENY'
    payload = response.json() if json_transport else bridge_payload(response.text)
    assert payload['nonce'] == 'a' * 64 and payload['user_id'] == test_user.id
    assert payload['providers'] == {'coros': 'disconnected', 'garmin': 'disconnected', 'strava': 'reconnect'}
    assert set(payload) == {'type', 'nonce', 'user_id', 'name', 'providers'}
    assert 'access_token' not in response.text and 'refresh_token' not in response.text
    other = User(firstname='Other')
    db_session.add(other); await db_session.flush()
    client.cookies.clear(); login(client, other)
    response = await client.get(url)
    payload = response.json() if json_transport else bridge_payload(response.text)
    assert payload['user_id'] == other.id and payload['providers']['strava'] == 'disconnected'


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


def test_steps_axis_starts_at_zero_and_preserves_large_and_small_measurements():
    today = date(2026, 10, 10)
    for values in ([54, 121478, 2909], [0, 0, 0]):
        rows = [HealthMetric(date=today-timedelta(days=2-i), metric='steps', value=v, source='COROS')
                for i, v in enumerate(values)]
        chart = daily_card('steps', rows, today)['chart']
        assert 2 <= len(chart['ticks']) <= 4
        assert chart['ticks'][0] == {'y': chart['bottom'], 'label': '0'}
        assert len(chart['dots']) == len(values)
        assert all(chart['top'] <= p['y'] <= chart['bottom'] for p in chart['dots'])
        reads = json.loads(chart['data'])['r']
        assert [r[0] for r in reads[-3:]] == [str(v)+'\u00a0pas' for v in values]


def test_daily_gaps_and_no_trend_across_a_watch_change():
    today = date(2026, 10, 10)
    rows = [HealthMetric(date=today-timedelta(days=i), metric='hr_day', value=65, source='Garmin' if i<5 else 'COROS') for i in range(1, 15)]
    c = daily_card('hr_day', rows, today)
    assert '4 jours avec des relevés' in c['trend'] and c['chart']['n'] == 30
    assert 'par rapport' not in c['trend']
    assert len(c['chart']['dots']) == 14
    assert 'jours' in c['chart']['title']


def bearer(token):
    return {"Authorization": "Bearer " + token}


async def test_token_expiry_slides_with_use_and_last_seen_is_written_at_most_hourly(client, db_session, test_user):
    account, _ = await paired(client, test_user)
    phone = await db_session.scalar(select(MobileDevice).where(MobileDevice.user_id == test_user.id))
    now = datetime.now(timezone.utc)
    phone.expires_at, phone.last_seen_at = now + timedelta(days=170), now - timedelta(minutes=30)
    await db_session.flush()
    kept = (phone.expires_at, phone.last_seen_at)
    assert (await client.post('/api/mobile/daily', json={'days': []}, headers=bearer(account['token']))).status_code == 200
    assert (phone.expires_at, phone.last_seen_at) == kept  # no write on every background run
    phone.expires_at, phone.last_seen_at = now + timedelta(days=100), now - timedelta(hours=2)
    await db_session.flush()
    assert (await client.post('/api/mobile/daily', json={'days': []}, headers=bearer(account['token']))).status_code == 200
    assert phone.expires_at >= now + timedelta(days=179) and phone.last_seen_at >= now
    phone.expires_at = now - timedelta(minutes=1)
    await db_session.flush()
    assert (await client.post('/api/mobile/daily', json={'days': []}, headers=bearer(account['token']))).status_code == 401


async def test_rotation_keeps_the_previous_token_until_the_new_one_is_used(client, db_session, test_user):
    account, _ = await paired(client, test_user)
    first = account['token']
    rotated = await client.post('/api/mobile/token', headers=bearer(first))
    assert rotated.status_code == 200 and rotated.headers['cache-control'] == 'no-store'
    second = rotated.json()['token']
    assert second != first and rotated.json()['user_id'] == test_user.id
    phone = await db_session.scalar(select(MobileDevice).where(MobileDevice.user_id == test_user.id))
    assert (phone.token_hash, phone.prev_token_hash) == (digest(second), digest(first))
    # The response was lost: the phone keeps its token, and rotates again with it.
    assert (await client.post('/api/mobile/daily', json={'days': []}, headers=bearer(first))).status_code == 200
    third = (await client.post('/api/mobile/token', headers=bearer(first))).json()['token']
    assert (await client.post('/api/mobile/daily', json={'days': []}, headers=bearer(second))).status_code == 401
    # The first use of the new token retires the previous one.
    assert (await client.post('/api/mobile/daily', json={'days': []}, headers=bearer(third))).status_code == 200
    assert phone.prev_token_hash is None and phone.prev_expires_at is None
    assert (await client.post('/api/mobile/daily', json={'days': []}, headers=bearer(first))).status_code == 401
    # The grace period is bounded, and rotating needs a valid token.
    fourth = (await client.post('/api/mobile/token', headers=bearer(third))).json()['token']
    phone.prev_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await db_session.flush()
    assert (await client.post('/api/mobile/daily', json={'days': []}, headers=bearer(third))).status_code == 401
    assert (await client.post('/api/mobile/daily', json={'days': []}, headers=bearer(fourth))).status_code == 200
    assert (await client.post('/api/mobile/token')).status_code == 401
    assert (await client.post('/api/mobile/token', headers=bearer('unknown'))).status_code == 401


async def test_identical_resends_change_nothing_and_latest_is_one_row_per_metric(client, db_session, test_user):
    from app.routers.sante import data_version
    account, _ = await paired(client, test_user)
    now = datetime.now(timezone.utc)
    days = [day(date=(now - timedelta(days=i)).date().isoformat(), value=70 + i,
                measured_at=(now - timedelta(days=i)).isoformat()) for i in range(3)]
    days += [day(date=(now - timedelta(days=i)).date().isoformat(), metric='steps', value=1000 * i, unit='count',
                 measured_at=None, sources=['iPhone']) for i in range(3)]
    first = await client.post('/api/mobile/daily', json={'days': days}, headers=bearer(account['token']))
    assert first.json()['received'] == 6
    assert set(first.json()['latest']) == {'weight', 'steps'}
    assert (first.json()['latest']['weight']['value'], first.json()['latest']['steps']['value']) == (70, 0)
    version = await data_version(db_session, test_user.id)
    stamps = {r.id: r.updated_at for r in await db_session.scalars(select(MobileDaily))}
    again = await client.post('/api/mobile/daily', json={'days': days}, headers=bearer(account['token']))
    assert again.json()['received'] == 0 and again.json()['latest'] == first.json()['latest']
    assert await data_version(db_session, test_user.id) == version
    assert {r.id: r.updated_at for r in await db_session.scalars(select(MobileDaily))} == stamps
    days[3]['value'] = 1234
    changed = await client.post('/api/mobile/daily', json={'days': days}, headers=bearer(account['token']))
    assert changed.json()['received'] == 1 and changed.json()['latest']['steps']['value'] == 1234
    assert await data_version(db_session, test_user.id) != version


async def test_a_phone_corrects_its_own_day_but_an_older_snapshot_never_wins(client, db_session, test_user):
    account, _ = await paired(client, test_user)
    other, _ = await paired(client, test_user)
    at = datetime.now(timezone.utc) - timedelta(hours=3)
    later, earlier = day(measured_at=at.isoformat(), value=71), day(measured_at=(at - timedelta(hours=1)).isoformat(), value=70)
    assert (await client.post('/api/mobile/daily', json={'days': [later]}, headers=bearer(account['token']))).json()['received'] == 1
    # Without a read time (older apps), or read before that row was stored (a
    # delayed request), the earlier weigh-in never replaces the later one.
    stale = {**bearer(account['token']), 'X-PaceForge-Read-At': (at - timedelta(minutes=5)).isoformat()}
    for headers in (bearer(account['token']), stale):
        response = await client.post('/api/mobile/daily', json={'days': [earlier]}, headers=headers)
        assert response.json()['received'] == 0 and response.json()['latest']['weight']['value'] == 71
    # Another phone, even when it read Santé just now, cannot replace it either.
    now_read = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    response = await client.post('/api/mobile/daily', json={'days': [earlier]},
                                 headers={**bearer(other['token']), 'X-PaceForge-Read-At': now_read})
    assert response.json()['received'] == 0
    # The same phone read Santé after the later weigh-in was deleted there.
    response = await client.post('/api/mobile/daily', json={'days': [earlier]},
                                 headers={**bearer(account['token']), 'X-PaceForge-Read-At': now_read})
    assert response.json()['received'] == 1 and response.json()['latest']['weight']['value'] == 70
    for implausible in ('2020-01-01T00:00:00Z', 'yesterday', (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()):
        assert mobile.read_at(implausible, datetime.now(timezone.utc)) is None


async def test_each_phone_has_a_request_budget(client, test_user, monkeypatch):
    budget = mobile.PhoneLimiter(limit=2, window=600)
    budget.redis_down_until = float('inf')  # this worker's count
    monkeypatch.setattr(mobile, 'limiter', budget)
    account, _ = await paired(client, test_user)
    other, _ = await paired(client, test_user)
    for _ in range(2):
        assert (await client.post('/api/mobile/daily', json={'days': []}, headers=bearer(account['token']))).status_code == 200
    refused = await client.post('/api/mobile/daily', json={'days': []}, headers=bearer(account['token']))
    assert refused.status_code == 429 and 0 < int(refused.headers['retry-after']) <= 600
    assert (await client.post('/api/mobile/nights', json={}, headers=bearer(account['token']))).status_code == 429
    assert (await client.post('/api/mobile/token', headers=bearer(account['token']))).status_code == 429
    assert (await client.post('/api/mobile/daily', json={'days': []}, headers=bearer(other['token']))).status_code == 200
    assert (await client.delete('/api/mobile/device', headers=bearer(account['token']))).status_code == 200


async def test_the_budget_is_shared_through_redis_and_survives_its_absence(monkeypatch):
    class Pipeline:
        def __init__(self, counts):
            self.counts, self.keys = counts, []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def incr(self, key):
            self.keys.append(key)
            return self

        def expire(self, key, seconds):
            return self

        async def execute(self):
            key = self.keys[0]
            self.counts[key] = self.counts.get(key, 0) + 1
            return [self.counts[key], True]

    class Redis:
        counts: dict = {}

        def pipeline(self, transaction=True):
            return Pipeline(self.counts)

    budget = mobile.PhoneLimiter(limit=1, window=600)
    budget.redis = Redis()
    await budget.check(7)
    with pytest.raises(mobile.HTTPException) as refused:
        await budget.check(7)
    assert refused.value.status_code == 429 and list(Redis.counts.values()) == [2]
    budget.redis = object()  # a broken connection: count in this worker, without failing the request
    await budget.check(8)
    assert budget.redis is None and budget.local[8][1] == 1


async def test_bodies_are_capped_before_parsing_and_errors_keep_their_shape(client, test_user):
    account, _ = await paired(client, test_user)
    headers = {**bearer(account['token']), 'Content-Type': 'application/json'}
    big = b'{"days": [' + b' ' * mobile.MAX_DAILY_BYTES + b']}'
    assert (await client.post('/api/mobile/daily', content=big, headers=headers)).status_code == 413

    async def streamed():
        yield b'{"date": "'
        for _ in range(33):
            yield b'x' * 65536

    assert (await client.post('/api/mobile/nights', content=streamed(), headers=headers)).status_code == 413
    response = await client.post('/api/mobile/daily', json={'days': [day(value=0)]}, headers=bearer(account['token']))
    assert response.status_code == 422 and response.json()['detail'][0]['loc'][:3] == ['body', 'days', 0]
    assert (await client.post('/api/mobile/daily', content=b'{', headers=headers)).status_code == 422


async def test_a_night_sets_the_athlete_zone_once_and_never_from_a_fixed_offset(client, test_user):
    from tests.test_apple_nights import packet
    account, _ = await paired(client, test_user)
    for zone in ('UTC', 'Etc/GMT-2'):
        assert (await client.post('/api/mobile/nights', json=packet() | {'timezone': zone},
                                  headers=bearer(account['token']))).status_code == 200
        assert test_user.timezone is None
    assert (await client.post('/api/mobile/nights', json=packet() | {'timezone': 'Europe/Paris'},
                              headers=bearer(account['token']))).status_code == 200
    assert test_user.timezone == 'Europe/Paris'
    assert (await client.post('/api/mobile/nights', json=packet() | {'timezone': 'America/New_York'},
                              headers=bearer(account['token']))).status_code == 200
    assert test_user.timezone == 'Europe/Paris'
    # Validation and reduction run off the event loop; their refusals stay 422.
    p = packet()
    p['naps'] = [p['sleep']]
    assert (await client.post('/api/mobile/nights', json=p, headers=bearer(account['token']))).status_code == 422
    assert (await client.post('/api/mobile/nights', json=packet() | {'timezone': 'Nowhere/City'},
                              headers=bearer(account['token']))).status_code == 422


def test_mobile_migration_roundtrip():
    from alembic.config import Config
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic.script import ScriptDirectory
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('mobile_migration', root/'alembic/versions/d4a5b6c7d8e9_mobile_daily.py')
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    assert mod.revision in {r.revision for r in ScriptDirectory.from_config(Config(str(root/'alembic.ini'))).walk_revisions()}
    engine = create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE users (id INTEGER PRIMARY KEY)'))
        with patch.object(mod, 'op', Operations(MigrationContext.configure(conn))):
            mod.upgrade()
            assert {'mobile_daily', 'mobile_devices'} <= set(inspect(conn).get_table_names())
            mod.downgrade()
            assert inspect(conn).get_table_names() == ['users']
