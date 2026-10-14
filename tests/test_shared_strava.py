"""Shared OAuth is explicit, app-bound and compatible with existing personal apps."""
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.config import settings
from app.crypto import encrypt_secret
from app.dependencies import get_current_user
from app.exceptions import StravaAPIError, StravaTokenError
from app.services.strava import StravaService, disconnect, strava_status
from tests.test_settings_links import _callback, _logged_in, no_commit  # noqa: F401


@pytest.fixture
def shared(monkeypatch):
    monkeypatch.setattr(settings, 'STRAVA_SHARED_APP_ENABLED', True)
    monkeypatch.setattr(settings, 'STRAVA_CLIENT_ID', '90001')
    monkeypatch.setattr(settings, 'STRAVA_CLIENT_SECRET', 'shared-test-secret')


async def test_shared_connect_and_sync_without_personal_keys(client, db_session, no_commit, shared, monkeypatch):
    exchange = AsyncMock(return_value={'access_token': 'at', 'refresh_token': 'rt', 'expires_at': 9999999999,
                                      'athlete': {'id': 90002}})
    monkeypatch.setattr(StravaService, 'exchange_token', exchange)
    subscribe = AsyncMock()
    monkeypatch.setattr(StravaService, 'create_webhook_subscription', subscribe)
    start = []
    monkeypatch.setattr('app.tasks.initial_sync.initial_sync.delay', lambda uid: start.append(uid))
    async with _logged_in(client, db_session) as (c, user):
        client._transport.app.dependency_overrides[get_current_user] = lambda: user
        page = (await c.get('/connections/strava')).text
        assert 'Continuer avec Strava' in page and 'Client Secret' not in page
        response = await c.get('/auth/strava?next=devices')
        assert parse_qs(urlsplit(response.headers['location']).query)['client_id'] == ['90001']
        assert 'shared-test-secret' not in str(c.cookies)
        response = await _callback(c, code='valid')
        assert response.headers['location'] == '/connections/strava'
        assert user.strava_client_id == '90001' and user.strava_client_secret_encrypted is None
        assert not user.has_own_strava_app and user.has_strava_app
        assert not strava_status(user)['needs_reauth'] and start == [user.id]
        subscribe.assert_not_awaited()  # the app-wide webhook must not be created per athlete
        recent = AsyncMock(return_value=[])
        monkeypatch.setattr(StravaService, 'get_recent_activities', recent)
        response = await c.post('/api/sync', headers={'accept': 'application/json', 'x-paceforge-refresh': '1'})
        assert response.json()['errors'] == [] and recent.await_count == 1
        # State has already been consumed: a reload cannot exchange the code again.
        await c.get('/auth/strava/callback?state=replayed&code=valid')
        assert exchange.await_count == 1


async def test_shared_attempt_rejects_changed_configuration(client, db_session, no_commit, shared, monkeypatch):
    exchange = AsyncMock()
    monkeypatch.setattr(StravaService, 'exchange_token', exchange)
    async with _logged_in(client, db_session) as (c, user):
        await c.get('/auth/strava?next=devices')
        monkeypatch.setattr(settings, 'STRAVA_CLIENT_ID', 'different-app')
        response = await _callback(c, code='valid')
        assert response.headers['location'] == '/connections/strava'
        exchange.assert_not_awaited()
        assert not user.strava_access_token


async def test_shared_failure_does_not_ask_for_athlete_keys(client, db_session, no_commit, shared, monkeypatch):
    monkeypatch.setattr(StravaService, 'exchange_token', AsyncMock(side_effect=StravaAPIError(status_code=401)))
    async with _logged_in(client, db_session) as (c, user):
        await c.get('/auth/strava?next=devices')
        response = await _callback(c, code='bad')
        assert response.headers['location'] == '/connections/strava'
        page = (await c.get('/connections/strava')).text
        assert 'temporairement indisponible' in page and 'Réparer la configuration' not in page


async def test_exact_app_binding_and_personal_app_priority(test_user, db_session, shared, monkeypatch):
    test_user.strava_client_id = None
    with pytest.raises(StravaTokenError):
        StravaService.for_user(db_session, test_user)
    test_user.strava_client_id = 'old-app'
    assert strava_status(test_user)['needs_reauth']
    with pytest.raises(StravaTokenError):
        StravaService.for_user(db_session, test_user)
    test_user.strava_client_id = '90001'
    assert StravaService.for_user(db_session, test_user).client_secret == 'shared-test-secret'
    revoke, unsubscribe = AsyncMock(), AsyncMock()
    monkeypatch.setattr(StravaService, 'deauthorize', revoke)
    monkeypatch.setattr(StravaService, 'delete_webhook_subscription', unsubscribe)
    await disconnect(db_session, test_user)
    revoke.assert_awaited_once()
    unsubscribe.assert_not_awaited()
    assert test_user.strava_access_token is None
    test_user.strava_client_id = 'personal'
    test_user.strava_client_secret_encrypted = encrypt_secret('own-test-secret')
    assert StravaService.for_user(db_session, test_user).client_secret == 'own-test-secret'
    # The owner's former personal setup may be the newly shared app itself.
    test_user.strava_client_id = '90001'
    test_user.strava_webhook_subscription_id = 55
    await disconnect(db_session, test_user)
    unsubscribe.assert_not_awaited()


async def test_shared_refresh_sends_the_bound_app_credentials(test_user, db_session, shared, monkeypatch):
    test_user.strava_client_id = '90001'
    test_user.strava_token_expires_at = 0
    await db_session.flush()
    response = httpx.Response(200, json={'access_token': 'fresh', 'refresh_token': 'rotated', 'expires_at': 9999999999})
    post = AsyncMock(return_value=response)
    monkeypatch.setattr(httpx.AsyncClient, 'post', post)
    await StravaService.for_user(db_session, test_user).refresh_token_if_needed(test_user)
    assert post.call_args.kwargs['data']['client_id'] == '90001'
    assert post.call_args.kwargs['data']['client_secret'] == 'shared-test-secret'
    assert test_user.strava_refresh_token == 'rotated'


def test_shared_connection_requires_explicit_activation(shared, monkeypatch):
    monkeypatch.setattr(settings, 'STRAVA_SHARED_APP_ENABLED', False)
    assert not settings.strava_shared_ready
    monkeypatch.setattr(settings, 'STRAVA_SHARED_APP_ENABLED', True)
    monkeypatch.setattr(settings, 'STRAVA_CLIENT_SECRET', '')
    assert not settings.strava_shared_ready


async def test_the_shared_secret_is_never_stored_in_a_link_attempt(client, db_session, no_commit, shared, monkeypatch):
    """A shared-app attempt keeps only {mode, client_id}; the secret is read from the configuration at callback."""
    import json

    from sqlalchemy import select

    from app.crypto import decrypt_secret
    from app.models.oauth_attempt import OAuthAttempt

    exchange = AsyncMock(return_value={'access_token': 'at', 'refresh_token': 'rt', 'expires_at': 9999999999,
                                      'athlete': {'id': 90003}})
    monkeypatch.setattr(StravaService, 'exchange_token', exchange)
    monkeypatch.setattr(StravaService, 'create_webhook_subscription', AsyncMock())
    monkeypatch.setattr('app.tasks.initial_sync.initial_sync.delay', lambda uid: None)
    seen = []
    real = StravaService.__init__

    def spy(self, db, client_id=None, client_secret=None):
        real(self, db, client_id=client_id, client_secret=client_secret)
        seen.append((self.client_id, self.client_secret))
    monkeypatch.setattr(StravaService, '__init__', spy)
    async with _logged_in(client, db_session) as (c, user):
        await c.get('/auth/strava')
        attempt = (await db_session.execute(select(OAuthAttempt))).scalar_one()
        assert json.loads(decrypt_secret(attempt.credentials)) == {'mode': 'shared', 'client_id': '90001'}
        seen.clear()
        response = await _callback(c, code='valid')
        assert response.status_code == 302 and user.strava_athlete_id == 90003
        assert seen[0] == ('90001', 'shared-test-secret')  # the exchange used the configured secret
        # the shared app's id changed meanwhile: the attempt is refused, nothing exchanged
        await c.get('/auth/strava')
        monkeypatch.setattr(settings, 'STRAVA_CLIENT_ID', '90009')
        exchange.reset_mock()
        response = await _callback(c, code='valid')
        assert exchange.await_count == 0


async def test_expired_link_attempts_are_purged_every_hour(db_session, test_user):
    from contextlib import asynccontextmanager
    from datetime import datetime, timedelta, timezone
    from unittest.mock import patch

    from sqlalchemy import select

    from app.celery_app import celery_app
    from app.models.oauth_attempt import OAuthAttempt
    from app.tasks import oauth_cleanup

    now = datetime.now(timezone.utc)
    db_session.add_all([
        OAuthAttempt(state_hash='a' * 64, user_id=test_user.id, credentials=encrypt_secret('{"client_secret": "k"}'),
                     expires_at=now - timedelta(minutes=1)),
        OAuthAttempt(state_hash='b' * 64, user_id=test_user.id, expires_at=now + timedelta(minutes=9)),
    ])
    await db_session.flush()

    @asynccontextmanager
    async def session():
        yield db_session
    with patch('app.database.get_task_session', session), patch.object(db_session, 'commit', db_session.flush):
        assert await oauth_cleanup._run() == 1
    assert [a.state_hash for a in (await db_session.execute(select(OAuthAttempt))).scalars()] == ['b' * 64]
    assert celery_app.conf.beat_schedule['purge-oauth-attempts']['task'] == 'paceforge.purge_oauth_attempts'
    assert 'app.tasks.oauth_cleanup' in celery_app.conf.include
