"""Refresh acknowledgements must distinguish queued, finished and failed work."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from app.models.user import User
from app.routers import dashboard
from app.services import coros
from tests.test_coros import _link
from tests.test_mobile import login

HEADERS = {'Accept': 'application/json', 'X-PaceForge-Refresh': '1'}


async def test_inline_session_is_authenticated_uncached_and_nonce_bound(client, test_user):
    url = '/mobile/session?nonce=' + 'a' * 64
    assert (await client.get(url, headers=HEADERS)).status_code != 200
    login(client, test_user)
    result = await client.get(url, headers=HEADERS)
    assert result.headers['cache-control'] == 'no-store'
    assert result.json() == {'type': 'session', 'nonce': 'a' * 64, 'user_id': test_user.id}
    assert (await client.get('/mobile/session?nonce=invalid', headers=HEADERS)).status_code == 400


async def test_watch_refresh_waits_for_completion_and_recovers_expired_claim(client, db_session, test_user, monkeypatch):
    login(client, test_user)
    conn = await _link(db_session, test_user)
    now = datetime.now(timezone.utc)
    conn.sync_claimed_at = now - timedelta(hours=1)
    conn.last_sync_at = now - timedelta(days=1)
    await db_session.flush()
    scheduled = []
    async def queue(db, link):
        scheduled.append(link.user_id)
        return True
    monkeypatch.setattr(coros, 'queue_sync', queue)
    assert (await client.post('/sante/sync', headers={'Accept': 'application/json'})).status_code == 403
    result = await client.post('/sante/sync', headers=HEADERS)
    assert result.headers['cache-control'] == 'no-store'
    assert result.json()['watches'] == ['COROS'] and scheduled == [test_user.id]
    params = {'watches': 'COROS', 'since': result.json()['since']}
    conn.sync_claimed_at = None
    await db_session.flush()
    assert (await client.get('/sante/sync-status', params=params, headers=HEADERS)).json()['pending'] == ['COROS']
    conn.sync_claimed_at = now
    await db_session.flush()
    assert (await client.get('/sante/sync-status', params=params, headers=HEADERS)).json()['pending'] == ['COROS']
    conn.sync_claimed_at = None
    conn.last_sync_at = datetime.now(timezone.utc).replace(tzinfo=None)  # SQLite-style naive UTC
    await db_session.flush()
    assert (await client.get('/sante/sync-status', params=params, headers=HEADERS)).json() == {'pending': [], 'errors': []}
    conn.last_error = 'upstream failed'
    await db_session.flush()
    status = (await client.get('/sante/sync-status', params=params, headers=HEADERS)).json()
    assert not status['pending'] and status['errors']


async def test_watch_refresh_does_not_include_other_users_or_reauthorize_silently(client, db_session, test_user, monkeypatch):
    login(client, test_user)
    other = User(firstname='Other'); db_session.add(other); await db_session.flush()
    await _link(db_session, other)
    scheduled = []
    monkeypatch.setattr(coros, 'queue_sync', AsyncMock(side_effect=lambda db, link: scheduled.append(link.user_id) or True))
    assert (await client.post('/sante/sync', headers=HEADERS)).json()['watches'] == []
    assert not scheduled
    own = await _link(db_session, test_user); own.needs_reauth = True; await db_session.flush()
    result = (await client.post('/sante/sync', headers=HEADERS)).json()
    assert result['errors'] == ['Reconnecte COROS dans Réglages.'] and not result['watches']
    assert not scheduled


async def test_strava_refresh_does_not_report_success_after_transport_failure(client, db_session, test_user, monkeypatch):
    login(client, test_user)
    test_user.strava_client_id = '123'  # a VARCHAR column: PostgreSQL refuses an int
    test_user.strava_client_secret_encrypted = b'test'
    test_user.strava_credentials_valid = True
    await db_session.flush()
    sync = AsyncMock(return_value=3)
    monkeypatch.setattr(dashboard, '_sync_recent_activities', sync)
    assert (await client.post('/api/sync', headers={'Accept': 'application/json'})).status_code == 403
    result = await client.post('/api/sync', headers=HEADERS)
    assert result.json() == {'received': 3, 'errors': []}
    assert sync.call_args.kwargs == {'strict': True}
    sync.side_effect = TimeoutError('upstream')
    result = await client.post('/api/sync', headers=HEADERS)
    assert result.json()['received'] == 0 and result.json()['errors']
    assert result.headers['cache-control'] == 'no-store'


async def test_retry_reserves_coros_before_polling_previous_error(client, db_session, test_user, monkeypatch):
    """An earlier error cannot finish the new attempt before its worker starts."""
    from app.config import settings
    monkeypatch.setattr(db_session, 'commit', db_session.flush)
    login(client, test_user)
    conn = await _link(db_session, test_user)
    conn.last_error = 'Previous network failure'
    await db_session.flush()
    monkeypatch.setattr(settings, 'COROS_SYNC', True)
    # The real reservation is committed; the worker has not started the queued job yet.
    from app.tasks.coros_sync import sync_coros_user
    jobs = []
    monkeypatch.setattr(sync_coros_user, 'apply_async', lambda *, args, retry: jobs.append(args))
    result = (await client.post('/sante/sync', headers=HEADERS)).json()
    assert result['watches'] == ['COROS'] and not result['errors'] and len(jobs) == 1
    assert conn.sync_claimed_at is not None
    result = (await client.get('/sante/sync-status', headers=HEADERS,
        params={'watches': 'COROS', 'since': result['since']})).json()
    assert result == {'pending': ['COROS'], 'errors': []}


async def test_reserved_coros_job_is_consumed_once(db_session, test_user, monkeypatch):
    monkeypatch.setattr(db_session, 'commit', db_session.flush)
    conn = await _link(db_session, test_user)
    assert await coros.claim(db_session, conn)
    stamp = conn.sync_claimed_at.isoformat()
    sync = AsyncMock(return_value={})
    monkeypatch.setattr(coros, 'sync_connection', sync)
    assert (await coros.run_sync(db_session, conn, claimed_at=stamp))['ok']
    assert await coros.run_sync(db_session, conn, claimed_at=stamp) is None
    assert sync.await_count == 1


async def test_simultaneous_refresh_joins_the_existing_reservation(client, db_session, test_user, monkeypatch):
    login(client, test_user)
    await _link(db_session, test_user)
    async def reserved_elsewhere(db, conn):
        conn.sync_claimed_at = datetime.now(timezone.utc)
        await db.flush()
        return False
    monkeypatch.setattr(coros, 'queue_sync', reserved_elsewhere)
    result = (await client.post('/sante/sync', headers=HEADERS)).json()
    assert result['watches'] == ['COROS'] and not result['errors']
