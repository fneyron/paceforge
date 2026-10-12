import { test } from 'node:test';
import assert from 'node:assert/strict';
import { runInNewContext } from 'node:vm';
import { clockLabel, offlineAccountScript, OfflinePlan, offlineReply, readOfflinePlan, withPlan } from './offline';

const plan: OfflinePlan = { version: 1, user_id: 1, route_id: 2, name: 'Course', saved_at: '2026-10-12T20:00:00Z', race_date: null,
  start_s: 75600, distance_km: 30, elevation_m: 1100, planned: true, points: [{ name: 'Col', km: 10, clock_s: 86460, kind: 'full', crew: false, drop_bag: true, cutoff_clock: '02:00', cutoff_margin_s: 7140, pinned: false, passed: false }] };

test('only validated plan fields are persisted; missing clocks stay absent', () => {
  assert.deepEqual(readOfflinePlan({ ...plan, token: 'never-save', html: '<script>' }), plan);
  assert.equal(readOfflinePlan({ ...plan, points: [null] }), null);
  assert.equal(readOfflinePlan({ ...plan, points: [{ ...plan.points[0], clock_s: NaN }] }), null);
  assert.equal(readOfflinePlan({ ...plan, version: 2 }), null);
  assert.equal(readOfflinePlan({ ...plan, user_id: '1' }), null);
  assert.equal(clockLabel(null), '—');
  assert.equal(clockLabel(86460), '00:01 · J+1');
});

test('copy replacement is scoped to its account and library capacity is explicit', () => {
  const library = withPlan({ userId: null, plans: [] }, plan);
  assert.equal(withPlan(library, { ...plan, name: 'Updated' }).plans.length, 1);
  const changed = withPlan(library, { ...plan, user_id: 3, route_id: 4 });
  assert.deepEqual(changed.plans.map(p => p.user_id), [3]);
  assert.throws(() => withPlan({ userId: 1, plans: Array.from({ length: 20 }, (_, i) => ({ ...plan, route_id: i + 10 })) }, plan));
});

test('a bridge reply needs the current nonce, document, origin and message type', () => {
  const task = { nonce: 'abc', url: 'https://paceforge.fr/simulator/routes/2', routeId: 2 };
  const raw = JSON.stringify({ type: 'offline-plan', nonce: 'abc', plan });
  assert.ok(offlineReply(task, raw, task.url));
  assert.equal(offlineReply(task, raw, 'https://other.test/simulator/routes/2'), null);
  assert.equal(offlineReply(task, raw, 'https://paceforge.fr/simulator/routes/3'), null);
  assert.equal(offlineReply({ ...task, nonce: 'new' }, raw, task.url), null);
  assert.equal(offlineReply(null, raw, task.url), null);
  assert.equal(offlineReply(task, ' '.repeat(256001), task.url), null);
});

test('opening public privacy/help does not erase saved plans as a false logout', () => {
  const messages: string[] = [];
  const context = (pathname: string, account: string | null) => ({
    location: { origin: 'https://paceforge.fr', pathname },
    document: { documentElement: { classList: { add() {} } }, querySelector: () => account ? { dataset: { account } } : null },
    window: { ReactNativeWebView: { postMessage: (data: string) => messages.push(data) } },
  });
  runInNewContext(offlineAccountScript('nonce'), context('/mobile/privacy', null));
  assert.equal(messages.length, 0);
  runInNewContext(offlineAccountScript('nonce'), context('/sante', '2'));
  assert.equal(JSON.parse(messages.pop()!).user_id, 2);
  runInNewContext(offlineAccountScript('nonce'), context('/auth/login', null));
  assert.equal(JSON.parse(messages.pop()!).user_id, null);
});
