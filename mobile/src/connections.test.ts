import assert from 'node:assert/strict';
import test from 'node:test';
import { ORIGIN } from './api';
import { connectionLabel, readConnections } from './connections';

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
