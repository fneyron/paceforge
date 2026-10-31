import test from 'node:test';
import assert from 'node:assert/strict';
import { api, ApiError, authorized, CONSENT_REQUIRED, consentRefusal, Credentials, rotateToken, rotationDue } from './api';

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

// What the server answers a phone's push without the consent to health data (app/services/health_consent.PHONE_REFUSED).
const refusal = { detail: 'Accepte le suivi de tes données de santé dans PaceForge pour envoyer tes mesures.', code: 'health_consent_required' };
async function answered(status: number, body: unknown) {
  const original = globalThis.fetch;
  globalThis.fetch = (async () => ({ ok: false, status, headers: { get: () => null },
    json: async () => { if (body instanceof Error) throw body; return body; } })) as unknown as typeof fetch;
  try { await api('/api/mobile/daily', 'POST', { days: [] }, 'token'); } catch (e) { return e; } finally { globalThis.fetch = original; }
  throw new Error('no refusal');
}

test('a push refused without the consent to health data says the server sentence, never temporary nor a reconnect', async () => {
  const e = await answered(403, refusal);
  assert.ok(e instanceof ApiError);
  assert.equal(e.status, 403); assert.equal(e.consentRequired, true);
  assert.equal(e.message, refusal.detail);
  assert.equal(e.temporary, false, 'not retried in a loop');
  // Without its sentence (or an unusable one), the same French sentence.
  for (const detail of [undefined, '', 42, 'x'.repeat(301)]) {
    const fallback = await answered(403, { code: 'health_consent_required', detail });
    assert.ok(fallback instanceof ApiError && fallback.consentRequired);
    assert.equal(fallback.message, CONSENT_REQUIRED);
  }
  // Any other 403 is unchanged.
  for (const body of [{ detail: 'Forbidden' }, { code: 'other', detail: 'Accepte…' }, null, new Error('not JSON')]) {
    const other = await answered(403, body);
    assert.ok(other instanceof ApiError);
    assert.equal(other.consentRequired, false);
    assert.equal(other.message, 'Envoi impossible (403). Réessaie dans quelques instants.');
  }
  assert.equal(consentRefusal({ code: 'health_consent_required', detail: ' Accepte. ' }), 'Accepte.');
  assert.equal(consentRefusal({ detail: refusal.detail }), null);
  assert.equal(new ApiError(503).consentRequired, false);
});

test('a consent refusal is not retried with the previous token and keeps the stored credentials', async () => {
  const used: string[] = [], kept: Credentials[] = [];
  const request = async (token: string) => { used.push(token); throw new ApiError(403, '', 0, refusal.detail); };
  await assert.rejects(authorized({ token: 'new', prev_token: 'old' }, request, async c => { kept.push(c); }),
    (e: unknown) => e instanceof ApiError && e.consentRequired);
  assert.deepEqual(used, ['new']);
  assert.deepEqual(kept, []);
});
