import assert from 'node:assert/strict';
import test from 'node:test';
import { ORIGIN } from './api';
import { connectionLabel, readActionReply, readConnections, signInResult } from './connections';

test('connection status cannot come from a different origin, route, request or malformed payload', () => {
  const url = ORIGIN + '/mobile/connections';
  const payload = { type: 'connections', nonce: 'a'.repeat(64), user_id: 2, name: 'Florent',
    providers: { coros: 'connected', garmin: 'disconnected', strava: 'reconnect' } };
  assert.equal(readConnections(payload, url, payload.nonce)?.providers.coros, 'connected');
  for (const wrong of ['https://example.org/mobile/connections', ORIGIN + '/mobile/connections/extra', ORIGIN + '/settings']) {
    assert.equal(readConnections(payload, wrong, payload.nonce), null);
  }
  assert.equal(readConnections(payload, url, 'b'.repeat(64)), null);
  for (const data of [null, {}, { ...payload, type: 'session' }, { ...payload, user_id: '2' },
    { ...payload, providers: { coros: 'connected' } }, { ...payload, providers: { ...payload.providers, strava: 'yes' } }]) {
    assert.equal(readConnections(data, url, payload.nonce), null);
  }
  assert.equal(connectionLabel(), 'État à vérifier');
  assert.equal(connectionLabel('reconnect'), 'À reconnecter');
});

test('details, action answers and the sign-in outcome are read strictly', () => {
  const url = ORIGIN + '/mobile/connections', nonce = 'a'.repeat(64);
  const payload = { type: 'connections', nonce, user_id: 2, name: 'Florent',
    providers: { coros: 'connected', garmin: 'disconnected', strava: 'attention' },
    details: { coros: { last_sync: '2026-10-17T05:58:43+00:00', notice: null }, strava: { last_sync: 'hier', notice: 'Import incomplet' } } };
  const read = readConnections(payload, url, nonce)!;
  assert.deepEqual(read.details.coros, { lastSync: '2026-10-17T05:58:43+00:00', notice: null });
  assert.deepEqual(read.details.strava, { lastSync: null, notice: 'Import incomplet' });  // a date only
  assert.deepEqual(read.details.garmin, { lastSync: null, notice: null });  // an older server sends none
  const link = ORIGIN + '/mobile/oauth/coros#ticket';
  assert.deepEqual(readActionReply({ result: 'Synchronisation lancée.', link }, { provider: 'coros', kind: 'link' }),
    { result: 'Synchronisation lancée.', link });
  for (const wrong of ['https://example.org/mobile/oauth/coros#t', ORIGIN + '/mobile/oauth/strava#t', ORIGIN + '/settings']) {
    assert.equal(readActionReply({ link: wrong }, { provider: 'coros', kind: 'link' }).link, null);
  }
  assert.equal(readActionReply({ link }, { provider: 'coros', kind: 'sync' }).link, null);
  assert.equal(signInResult('paceforge://devices?connection=strava&result=ok', 'strava'), 'Strava connecté. Tes données arrivent.');
  assert.equal(signInResult('paceforge://devices?connection=coros&result=cancelled', 'coros'), 'Connexion annulée.');
  assert.equal(signInResult('paceforge://devices?connection=coros&result=<b>', 'coros'), '');
  assert.equal(signInResult(null, 'coros'), '');
});
