import test from 'node:test';
import assert from 'node:assert/strict';
import { ApiError } from './api';
import { createEngine, emptyState, EngineDeps, readState, RUN_MS } from './background-engine';
import { isLockedError, KeychainLocked } from './secure-core';
import type { SyncReport } from './sync';
import type { Daily, Pair } from './types';
import type { AppleNight } from './night-samples';

const start = Date.parse('2026-10-14T08:00:00Z');
const weighIn: Daily = { date: '2026-10-14', metric: 'weight', value: 70.2, unit: 'kg', measured_at: '2026-10-14T06:58:00Z', sources: ['Senssun Health'] };
const steps: Daily = { date: '2026-10-14', metric: 'steps', value: 1200, unit: 'count', sources: ['iPhone'] };
const night = { date: '2026-10-14', source_id: 'a'.repeat(64), source_name: 'Apple Watch', timezone: 'Europe/Paris',
  sleep: [{ start: '2026-10-13T21:00:00Z', end: '2026-10-14T05:00:00Z', stage: 'core' }], naps: [], hr: [], hrv_sdnn: [], resp: [] } as AppleNight;

function fixture(overrides: Partial<EngineDeps> = {}) {
  // waitTick: fake time spent by each failed lock attempt (a foreground sync holding it).
  let clock = start, held = false, active = 0, peak = 0, waitTick = 0;
  const calls: string[] = [];
  const sent: { path: string; body: unknown; token: string; timeout: number; readAt: string }[] = [];
  const stored = { pair: { token: 'token-2', user_id: 1, name: 'Florent' } as Pair, automatic: true,
    receipt: null as SyncReport | null, state: { ...emptyState(1), enabled: true } };
  const data = { days: [weighIn, steps], nights: [night] };
  const deps: EngineDeps = {
    id: 'test', now: () => clock, debounceMs: 15, retryMs: 2,
    load: async () => ({ pair: stored.pair, automatic: stored.automatic, receipt: stored.receipt, state: stored.state }),
    saveState: async state => { stored.state = state; },
    saveReceipt: async report => { stored.receipt = report; },
    savePair: async pair => { stored.pair = pair; },
    lock: { acquire: async () => { if (held) { clock += waitTick; return false; } held = true; return true; }, release: async () => { held = false; } },
    protectedData: async () => true,
    changes: async tokens => ({ daysBack: 3, tokens }),
    readDaily: async (daysBack, budget) => {
      active++; peak = Math.max(peak, active); calls.push(`daily ${daysBack} ${budget.total}/${budget.perRead}`);
      await new Promise(resolve => setTimeout(resolve, 5)); active--;
      return { days: data.days, notices: [], complete: true };
    },
    readNights: async (daysBack, budget) => { calls.push(`nights ${daysBack} ${budget.total}/${budget.perRead}`); return { nights: data.nights, notices: [], complete: true }; },
    send: async (path, body, token, timeout, readAt) => {
      sent.push({ path, body, token, timeout, readAt });
      return { received: 1, checked_at: new Date(clock).toISOString(), latest: { weight: { value: 70.2, measured_at: weighIn.measured_at } } };
    },
    digest: async value => `#${value}`,
    disable: async () => { calls.push('disable'); },
    notify: () => {},
    locked: isLockedError,
    ...overrides,
  };
  return { deps, stored, data, calls, sent, peak: () => peak, advance: (ms: number) => { clock += ms; },
    hold: (tick = 1000) => { held = true; waitTick = tick; }, free: () => { held = false; } };
}

test('changes reported together (weight and body fat, per type) make one bounded background run', async () => {
  const f = fixture(), engine = createEngine(f.deps);
  const outcomes = await Promise.all([engine.request('healthkit'), engine.request('healthkit'), engine.request('healthkit')]);
  assert.deepEqual(outcomes, ['sent', 'sent', 'sent']);
  // Three days and three nights, within the native window, short reads, never the history.
  assert.deepEqual(f.calls, ['daily 3 8000/3000', 'nights 3 10000/3000']);
  assert.deepEqual(f.sent.map(s => s.path), ['/api/mobile/daily', '/api/mobile/nights']);
  assert.ok(f.sent.every(s => s.timeout <= 8000 && s.token === 'token-2' && s.readAt === new Date(start).toISOString()));
  assert.equal(f.stored.receipt?.trigger, 'background');
  assert.equal(f.stored.receipt?.fullAt, '');
  assert.equal(f.stored.receipt?.pending, false);
  assert.equal(f.stored.receipt?.lastReceivedAt, new Date(start).toISOString());
});

test('identical data is not sent again; a changed weigh-in sends only its own row', async () => {
  const f = fixture(), engine = createEngine(f.deps);
  await engine.request('task');
  assert.equal(f.sent.length, 2);
  assert.equal(await engine.request('task'), 'unchanged');
  assert.equal(f.sent.length, 2);
  f.data.days = [{ ...weighIn, value: 69.8, measured_at: '2026-10-14T07:30:00Z' }, steps];
  assert.equal(await engine.request('task'), 'sent');
  assert.deepEqual(f.sent.slice(2).map(s => s.body), [{ days: [f.data.days[0]] }]);
  // The shortcut is forgotten after a day, so a loss on the server heals by itself.
  f.advance(25 * 3600000);
  await engine.request('task');
  assert.equal(f.sent.length, 5);
});

test('one run at a time: a change during a run is read again right after it', async () => {
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const f = fixture();
  const base = f.deps.readDaily;
  let reads = 0;
  f.deps.readDaily = async (...args) => { if (reads++ === 0) await gate; return base(...args); };
  const engine = createEngine(f.deps);
  const first = engine.request('healthkit');
  await new Promise(resolve => setTimeout(resolve, 40));
  const second = engine.request('healthkit');
  await new Promise(resolve => setTimeout(resolve, 40));
  assert.equal(reads, 1, 'no second run while the first is reading');
  release();
  assert.equal(await first, 'sent');
  assert.equal(await second, 'unchanged');
  assert.equal(reads, 2); assert.equal(f.peak(), 1);
});

test('a wake while the foreground sync holds the lock waits for it, or runs once that sync is over', async () => {
  // Released within the wake's time: the same wake reads after it.
  const quick = fixture(), engine = createEngine(quick.deps);
  quick.hold(0);
  const waiting = engine.request('healthkit');
  await new Promise(resolve => setTimeout(resolve, 60));
  assert.deepEqual(quick.calls, [], 'nothing read while the lock is held');
  quick.free();
  assert.equal(await waiting, 'sent');
  // Held past the wake's window (a long manual sync): kept, pending, then resumed.
  const slow = fixture(), later = createEngine(slow.deps);
  slow.hold(1000);
  assert.equal(await later.request('healthkit'), 'busy');
  assert.equal(slow.calls.length, 0);
  assert.ok(later.pending());
  slow.free();
  later.resumeMissed(); // syncExclusively's finally, after releasing the lock
  await new Promise(resolve => setTimeout(resolve, 60));
  assert.deepEqual(slow.calls.slice(0, 1), ['daily 3 8000/3000'], 'the kept wake gets a full run');
  assert.equal(slow.sent.length, 2);
  later.resumeMissed(); // nothing kept any more
  await new Promise(resolve => setTimeout(resolve, 30));
  assert.equal(slow.calls.filter(c => c.startsWith('daily')).length, 1);
});

test('the deadline stops before sending: nothing half-sent, the next opening syncs at once', async () => {
  const f = fixture();
  const base = f.deps.readDaily;
  f.deps.readDaily = async (...args) => { const r = await base(...args); f.advance(RUN_MS); return r; };
  const engine = createEngine(f.deps);
  assert.equal(await engine.request('task'), 'partial');
  assert.equal(f.sent.length, 0);
  assert.equal(f.stored.receipt?.pending, true);
  assert.equal(f.stored.receipt?.complete, false);
  assert.ok(engine.pending());
});

test('a HealthKit run ends within 14 s of its JavaScript callback (boot allowance); a late one is short or full', async () => {
  const f = fixture(), engine = createEngine(f.deps);
  const nights = () => f.calls.filter(c => c.startsWith('nights')).at(-1);
  await engine.request('healthkit');
  assert.equal(nights(), 'nights 3 10000/3000');
  f.data.days = [{ ...weighIn, value: 70.4 }];
  const wake = engine.request('healthkit');
  f.advance(9000); // the debounce fired late: 5 s of the window left
  await wake;
  assert.deepEqual(f.calls.slice(-2), ['daily 3 5000/3000', 'nights 3 5000/3000']);
  f.data.days = [{ ...weighIn, value: 70.6 }];
  const end = engine.request('healthkit');
  f.advance(13000); // at the very end of the window: a short attempt
  await end;
  assert.deepEqual(f.calls.slice(-2), ['daily 3 3000/3000', 'nights 3 3000/3000']);
  f.data.days = [{ ...weighIn, value: 70.8 }];
  const resumed = engine.request('healthkit');
  f.advance(60000); // the app was suspended, then woken again or opened
  await resumed;
  assert.equal(nights(), 'nights 3 10000/3000');
});

test('a refused token stops background sync and asks to reconnect, without retrying', async () => {
  const f = fixture({ send: async () => { throw new ApiError(401); } }), engine = createEngine(f.deps);
  assert.equal(await engine.request('task'), 'reconnect');
  assert.ok(f.calls.includes('disable'));
  assert.equal(f.stored.state.enabled, false);
  assert.equal(f.stored.receipt?.reconnect, true);
  assert.equal(await engine.request('task'), 'off');
  assert.equal(f.calls.filter(c => c.startsWith('daily')).length, 1);
});

test('after a rotation the server never stored, the previous token is retried once and kept', async () => {
  const tokens: string[] = [];
  const f = fixture();
  f.stored.pair = { ...f.stored.pair, prev_token: 'token-1' };
  const base = f.deps.send;
  f.deps.send = async (path, body, token, timeout, readAt) => {
    tokens.push(token);
    if (token === 'token-2') throw new ApiError(401);
    return base(path, body, token, timeout, readAt);
  };
  const engine = createEngine(f.deps);
  assert.equal(await engine.request('task'), 'sent');
  assert.deepEqual(tokens, ['token-2', 'token-1', 'token-1']);
  assert.equal(f.stored.pair.token, 'token-1');
  assert.equal(f.stored.pair.prev_token, undefined);
});

test('a locked phone reads nothing: the keychain is « locked », never « not paired », and the run stays pending', async () => {
  const locked = fixture({ load: async () => { throw new KeychainLocked(); } });
  assert.equal(await createEngine(locked.deps).request('healthkit'), 'locked');
  assert.deepEqual(locked.calls, []);
  const encrypted = fixture({ protectedData: async () => false });
  encrypted.stored.receipt = { userId: 1, checkedAt: '', lastReceivedAt: '', fullAt: '', complete: true, reconnect: false,
    retryFull: false, received: 0, notices: [], weightRead: null, weightReceived: null };
  const engine = createEngine(encrypted.deps);
  assert.equal(await engine.request('healthkit'), 'locked');
  assert.deepEqual(encrypted.calls, []);
  assert.equal(encrypted.stored.receipt?.pending, true);
  assert.ok(engine.pending());
});

test('too many requests waits for the server; off, blocked and unpaired phones never read', async () => {
  const f = fixture({ send: async () => { throw new ApiError(429, '', 120); } }), engine = createEngine(f.deps);
  assert.equal(await engine.request('task'), 'partial');
  assert.equal(f.stored.state.retryAt, start + 120000);
  assert.equal(f.stored.state.enabled, true);
  assert.equal(await engine.request('task'), 'deferred');
  for (const change of [{ automatic: false }, { state: { ...emptyState(1), blocked: 'logout' as const } }, { pair: null }]) {
    const off = fixture();
    Object.assign(off.stored, change);
    assert.equal(await createEngine(off.deps).request('task'), 'off');
    assert.deepEqual(off.calls, []);
  }
});

test('Health Connect change tokens: no change reads nothing, a quota refusal stops, tokens move once sent', async () => {
  const quiet = fixture({ changes: async () => ({ daysBack: 0, tokens: { Weight: 'w2' } }) });
  assert.equal(await createEngine(quiet.deps).request('task'), 'unchanged');
  assert.deepEqual(quiet.calls, []);
  assert.deepEqual(quiet.stored.state.tokens, { Weight: 'w2' });
  const refused = fixture({ changes: async tokens => ({ daysBack: 0, tokens, quota: true }) });
  assert.equal(await createEngine(refused.deps).request('task'), 'deferred');
  assert.equal(refused.stored.state.retryAt, start + 3600000);
  const failing = fixture({ changes: async () => ({ daysBack: 7, metrics: ['weight'], tokens: { Weight: 'w3' } }),
    send: async () => { throw new Error('offline'); } });
  failing.stored.state = { ...failing.stored.state, tokens: { Weight: 'w1' } };
  assert.equal(await createEngine(failing.deps).request('task'), 'partial');
  assert.equal(failing.calls[0], 'daily 7 8000/3000');
  assert.deepEqual(failing.stored.state.tokens, { Weight: 'w1' }, 'unsent changes are announced again');
  const quota = fixture({ readDaily: async () => ({ days: [], notices: [], complete: false, quota: true }) });
  await createEngine(quota.deps).request('task');
  assert.equal(quota.stored.state.retryAt, start + 3600000);
});

test('stored background state is read defensively', () => {
  assert.deepEqual(readState(null), emptyState());
  assert.deepEqual(readState('{oops'), emptyState());
  assert.deepEqual(readState(JSON.stringify({ userId: 3, enabled: true, blocked: 'other', hashes: { a: 'b', c: 1 }, tokens: [], retryAt: 'x' })),
    { ...emptyState(3), enabled: true, hashes: { a: 'b' } });
});
