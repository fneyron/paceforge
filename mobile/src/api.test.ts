import test from 'node:test';
import assert from 'node:assert/strict';
import { ApiError, authorized, Credentials, rotateToken, rotationDue } from './api';

test('too many requests and server failures are temporary; the delay asked is kept', () => {
  assert.equal(new ApiError(429, '', 120).temporary, true);
  assert.equal(new ApiError(429, '', 120).retryAfter, 120);
  assert.equal(new ApiError(503).temporary, true);
  assert.equal(new ApiError(401).temporary, false);
  assert.equal(new ApiError(422).temporary, false);
});

test('after a rotation, a 401 is retried once with the previous token, which is then kept', async () => {
  const kept: Credentials[] = [], used: string[] = [];
  const request = async (token: string) => { used.push(token); if (token !== 'old') throw new ApiError(401); return 'ok'; };
  assert.equal(await authorized({ token: 'new', prev_token: 'old' }, request, async c => { kept.push(c); }), 'ok');
  assert.deepEqual(used, ['new', 'old']);
  assert.deepEqual(kept, [{ token: 'old' }]);
  // The new token works: the previous one is forgotten (the server retired it).
  kept.length = 0;
  assert.equal(await authorized({ token: 'old', prev_token: 'older' }, request, async c => { kept.push(c); }), 'ok');
  assert.deepEqual(kept, [{ token: 'old' }]);
  // Without a previous token, or for another error, nothing is retried.
  used.length = 0;
  await assert.rejects(authorized({ token: 'new' }, request, async () => {}), (e: unknown) => e instanceof ApiError && e.status === 401);
  await assert.rejects(authorized({ token: 'new', prev_token: 'old' }, async () => { throw new ApiError(503); }, async () => {}));
  assert.deepEqual(used, ['new']);
});

test('rotation stores the new token with the previous one, and refuses a malformed answer', async () => {
  const pair = { token: 'a'.repeat(64), user_id: 1, name: 'F' };
  const stored: unknown[] = [];
  const next = await rotateToken(pair, async p => { stored.push(p); },
    (async () => ({ token: 'b'.repeat(64), user_id: 1 })) as never, () => '2026-10-14T08:00:00Z');
  assert.deepEqual(next, { ...pair, token: 'b'.repeat(64), prev_token: 'a'.repeat(64), rotated_at: '2026-10-14T08:00:00Z' });
  assert.deepEqual(stored, [next]);
  for (const answer of [{ token: 'short', user_id: 1 }, { token: 'b'.repeat(64), user_id: 2 }, null]) {
    await assert.rejects(rotateToken(pair, async () => { throw new Error('must not store'); }, (async () => answer) as never));
  }
  const now = Date.parse('2026-10-14T08:00:00Z');
  assert.equal(rotationDue(undefined, now), true);
  assert.equal(rotationDue('2026-10-01T08:00:00Z', now), false);
  assert.equal(rotationDue('2026-09-01T08:00:00Z', now), true);
});
