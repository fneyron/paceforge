import test from 'node:test';
import assert from 'node:assert/strict';
import { ApiError } from './api';
import { BackgroundProgress, BackgroundState, createEngine, emptyState, EngineDeps, fileLock, foregroundSkip, LOCK_MS, LockFile, readState, RUN_MS } from './background-engine';
import { isLockedError, KeychainLocked } from './secure-core';
import { fullDue, syncHealth, SyncReport } from './sync';
import type { Daily, HealthResult, Metric, Pair } from './types';
import type { AppleNight } from './night-samples';

const start = Date.parse('2026-10-14T08:00:00Z');
const weighIn: Daily = { date: '2026-10-14', metric: 'weight', value: 70.2, unit: 'kg', measured_at: '2026-10-14T06:58:00Z', sources: ['Senssun Health'] };
const steps: Daily = { date: '2026-10-14', metric: 'steps', value: 1200, unit: 'count', sources: ['iPhone'] };
const night = { date: '2026-10-14', source_id: 'a'.repeat(64), source_name: 'Apple Watch', timezone: 'Europe/Paris',
  sleep: [{ start: '2026-10-13T21:00:00Z', end: '2026-10-14T05:00:00Z', stage: 'core' }], naps: [], hr: [], hrv_sdnn: [], resp: [] } as AppleNight;

function fixture(overrides: Partial<EngineDeps> = {}) {
  // waitTick: fake time spent by each failed lock attempt (a foreground sync holding it
  // until freeAt).
  let clock = start, held = false, active = 0, peak = 0, waitTick = 0, freeAt = Infinity;
  const calls: string[] = [];
  const sent: { path: string; body: unknown; token: string; timeout: number; readAt: string }[] = [];
  const stored = { pair: { token: 'token-2', user_id: 1, name: 'Florent' } as Pair, automatic: true,
    receipt: null as SyncReport | null, state: { ...emptyState(1), enabled: true } };
  const data = { days: [weighIn, steps], nights: [night] };
  const deps: EngineDeps = {
    id: 'test', now: () => clock, debounceMs: 15, retryMs: 2,
    load: async () => ({ pair: stored.pair, automatic: stored.automatic, receipt: stored.receipt, state: stored.state }),
    saveProgress: async progress => { stored.state = { ...stored.state, ...progress }; },
    saveReceipt: async report => { stored.receipt = report; },
    savePair: async pair => { stored.pair = pair; },
    lock: { acquire: async () => { if (held && clock < freeAt) { clock += waitTick; return false; } held = true; return true; },
      release: async () => { held = false; } },
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
    hold: (tick = 1000, until = Infinity) => { held = true; waitTick = tick; freeAt = until; }, free: () => { held = false; } };
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

test('logout, automatic-off, a new account or a new pairing during a read stop before any upload', async () => {
  for (const change of ['logout', 'off', 'account', 'pairing'] as const) {
    const f = fixture(), read = f.deps.readDaily;
    let progressWrites = 0;
    f.deps.saveProgress = async () => { progressWrites++; };
    f.deps.readDaily = async (...args) => {
      if (change === 'logout') f.stored.state = { ...f.stored.state, blocked: 'logout', enabled: false };
      if (change === 'off') f.stored.automatic = false;
      if (change === 'account') {
        f.stored.pair = { token: 'other-account', user_id: 2, name: 'Other' };
        f.stored.state = { ...emptyState(2), enabled: true };
      }
      if (change === 'pairing') f.stored.state = { ...emptyState(1), enabled: true, generation: 'new-pairing' };
      f.stored.receipt = null; // the new account/pairing must keep its own receipt
      return read(...args);
    };
    assert.equal(await createEngine(f.deps).request('task'), 'off', change);
    assert.equal(f.sent.length, 0, change);
    assert.equal(progressWrites, 0, change);
    assert.equal(f.stored.receipt, null, change);
    if (change === 'logout') assert.equal(f.stored.state.blocked, 'logout');
  }
});

test('logout during an in-flight upload preserves the block and prevents subsequent nights or credentials writes', async () => {
  const f = fixture(), send = f.deps.send;
  f.stored.pair.prev_token = 'previous';
  let pairingWrites = 0, progressWrites = 0;
  f.deps.savePair = async () => { pairingWrites++; };
  f.deps.saveProgress = async () => { progressWrites++; };
  f.deps.send = async (...args) => {
    f.stored.state = { ...f.stored.state, blocked: 'logout', enabled: false };
    return send(...args);
  };
  assert.equal(await createEngine(f.deps).request('task'), 'off');
  assert.deepEqual(f.sent.map(s => s.path), ['/api/mobile/daily']);
  assert.equal(f.stored.state.blocked, 'logout');
  assert.equal(f.stored.state.enabled, false);
  assert.equal(pairingWrites, 0); assert.equal(progressWrites, 0);
});

test('local cancellation is immediate, before the control state finishes persisting', async () => {
  const f = fixture(), engine = createEngine(f.deps), read = f.deps.readDaily;
  f.deps.readDaily = async (...args) => { engine.cancel(); return read(...args); };
  assert.equal(await engine.request('task'), 'off');
  assert.equal(f.sent.length, 0);
  const checking = fixture();
  let changeReads = 0;
  checking.deps.protectedData = async () => { checking.stored.state.blocked = 'logout'; return true; };
  checking.deps.changes = async tokens => { changeReads++; return { daysBack: 3, tokens }; };
  assert.equal(await createEngine(checking.deps).request('task'), 'off');
  assert.equal(changeReads, 0, 'a logout during the availability check must stop before reading changes');
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

test('a wake whose lock is freed too late to send is resumed at once in full, not run short', async () => {
  const f = fixture(), engine = createEngine(f.deps);
  f.hold(1000, start + 12000); // the foreground sync ends 12 s after the wake: 2 s of it left
  assert.equal(await engine.request('healthkit'), 'busy');
  await new Promise(resolve => setTimeout(resolve, 60));
  assert.deepEqual(f.calls, ['daily 3 8000/3000', 'nights 3 10000/3000'], 'one run, with its full read times');
  assert.equal(f.sent.length, 2);
  assert.equal(engine.pending(), false);
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

test('a token rotated by the app while a run was suspended is picked up: no reconnect', async () => {
  const tokens: string[] = [];
  const f = fixture();
  f.stored.pair = { token: 'old', user_id: 1, name: 'Florent', rotated_at: '2026-08-01T00:00:00Z' };
  const base = f.deps.send, load = f.deps.load;
  let loads = 0;
  f.deps.load = async () => {
    // The run starts with the old token; the app stores the rotated one meanwhile.
    if (loads++ === 1) f.stored.pair = { token: 'new', user_id: 1, name: 'Flo', prev_token: 'old', rotated_at: '2026-10-14T07:59:00Z' };
    return load();
  };
  f.deps.send = async (path, body, token, timeout, readAt) => {
    tokens.push(token);
    if (token !== 'new') throw new ApiError(401);
    return base(path, body, token, timeout, readAt);
  };
  const engine = createEngine(f.deps);
  assert.equal(await engine.request('task'), 'sent');
  assert.deepEqual(tokens.slice(0, 2), ['old', 'new']);
  // Kept from the pairing read again: its rotation date (no early rotation) and name.
  assert.deepEqual(f.stored.pair, { token: 'new', user_id: 1, name: 'Flo', prev_token: undefined, rotated_at: '2026-10-14T07:59:00Z' });
  assert.equal(f.stored.receipt?.reconnect, false);
  assert.ok(!f.calls.includes('disable'));
  // Without a newer stored token, a refused token still asks to reconnect.
  const stale = fixture({ send: async () => { throw new ApiError(401); } });
  stale.stored.pair = { token: 'old', user_id: 1, name: 'Florent' };
  assert.equal(await createEngine(stale.deps).request('task'), 'reconnect');
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
  const quiet = fixture({ changes: async () => ({ daysBack: 0, tokens: { weight: 'w2' } }) });
  assert.equal(await createEngine(quiet.deps).request('task'), 'unchanged');
  assert.deepEqual(quiet.calls, []);
  assert.deepEqual(quiet.stored.state.tokens, { weight: 'w2' });
  const refused = fixture({ changes: async tokens => ({ daysBack: 0, tokens, quota: true }) });
  assert.equal(await createEngine(refused.deps).request('task'), 'deferred');
  assert.equal(refused.stored.state.retryAt, start + 3600000);
  const failing = fixture({ changes: async () => ({ daysBack: 7, metrics: ['weight'], tokens: { weight: 'w3' } }),
    send: async () => { throw new Error('offline'); } });
  failing.stored.state = { ...failing.stored.state, tokens: { weight: 'w1' } };
  assert.equal(await createEngine(failing.deps).request('task'), 'partial');
  assert.equal(failing.calls[0], 'daily 7 8000/3000');
  assert.deepEqual(failing.stored.state.tokens, { weight: 'w1' }, 'unsent changes are announced again');
  const quota = fixture({ readDaily: async () => ({ days: [], notices: [], complete: false, quota: true }) });
  await createEngine(quota.deps).request('task');
  assert.equal(quota.stored.state.retryAt, start + 3600000);
});

test('Health Connect tokens move on only for categories read in full', async () => {
  const changed = { daysBack: 3, metrics: ['weight', 'steps'] as Metric[], tokens: { weight: 'w2', steps: 's2', hr_day: 'h2' } };
  const run = async (daily: Partial<HealthResult>) => {
    const f = fixture({ changes: async () => changed,
      readDaily: async () => ({ days: [steps], notices: [], complete: true, ...daily }) });
    f.stored.state = { ...f.stored.state, tokens: { weight: 'w1', steps: 's1', hr_day: 'h1' } };
    await createEngine(f.deps).request('task');
    return f.stored.state.tokens;
  };
  // A quota refusal cut the read: every change is announced again.
  assert.deepEqual(await run({ complete: false, quota: true, days: [] }), { weight: 'w1', steps: 's1', hr_day: 'h1' });
  // The weigh-ins timed out while steps were read: only weight keeps its old token.
  assert.deepEqual(await run({ complete: false, unread: ['weight'] }), { weight: 'w1', steps: 's2', hr_day: 'h2' });
  // An incomplete read that does not say which categories: nothing moves.
  assert.deepEqual(await run({ complete: false }), { weight: 'w1', steps: 's1', hr_day: 'h1' });
  // Read in full and sent: every token moves.
  assert.deepEqual(await run({}), { weight: 'w2', steps: 's2', hr_day: 'h2' });
});

test('stored background state is read defensively', () => {
  assert.deepEqual(readState(null), emptyState());
  assert.deepEqual(readState('{oops'), emptyState());
  assert.deepEqual(readState(JSON.stringify({ userId: 3, enabled: true, blocked: 'other', hashes: { a: 'b', c: 1 }, tokens: [], retryAt: 'x' })),
    { ...emptyState(3), enabled: true, hashes: { a: 'b' } });
});

test('the lock file: held until it expires, released by its owner, written over when unreadable', async () => {
  let text: string | null = null, clock = start;
  const file: LockFile = { get exists() { return text !== null; }, textSync: () => text ?? '',
    write: value => { text = value; }, delete: () => { text = null; } };
  const lock = fileLock(() => file, () => clock);
  assert.equal(await lock.acquire('a', clock + LOCK_MS), true);
  assert.equal(await lock.acquire('b', clock + LOCK_MS), false);
  await lock.release('b');
  assert.notEqual(text, null, 'only its owner releases it');
  clock += LOCK_MS + 1;
  assert.equal(await lock.acquire('b', clock + LOCK_MS), true, 'expired');
  await lock.release('b');
  assert.equal(text, null);
  // A write cut short (a crash, a full disk) never switches the lock off for good.
  for (const unreadable of ['', '{"owner":"a","unt', 'null', '42']) {
    text = unreadable;
    assert.equal(await lock.acquire('c', clock + LOCK_MS), true);
    assert.equal(await lock.acquire('d', clock + LOCK_MS), false, 'written over: the lock works again');
    text = unreadable;
    await lock.release('d');
    assert.equal(text, null, 'deleted on release');
  }
  // An expiry written before the clock was set back does not hold the lock for months.
  text = JSON.stringify({ owner: 'e', until: clock + 90 * 86400000 });
  assert.equal(await lock.acquire('f', clock + LOCK_MS), true);
  // A file that cannot be read or written never blocks syncs.
  const broken = fileLock(() => ({ exists: true, textSync: () => { throw new Error('io'); },
    write: () => { throw new Error('io'); }, delete: () => { throw new Error('io'); } }));
  assert.equal(await broken.acquire('g', clock + LOCK_MS), true);
  await broken.release('g');
});

test('four pulls in ten minutes stay under the phone budget, even after an incomplete full reread', async () => {
  // The server allows 200 requests per phone and 10 minutes. Worst case: three sleep writers,
  // the 6-hourly full reread (30 days, 60 nights) due on the first pull, and one night the
  // server refuses (422), which keeps the full reread due (retryFull) until it is accepted.
  const date = (daysAgo: number) => new Date(start - daysAgo * 86400000).toISOString().slice(0, 10);
  const days = (count: number): Daily[] => [weighIn, ...Array.from({ length: count }, (_, i) => ({ ...steps, date: date(i) }))];
  const nights = (count: number): AppleNight[] => [night, ...Array.from({ length: count * 3 },
    (_, i) => ({ ...night, date: date(Math.floor(i / 3)), source_id: `writer-${i % 3}` }))];
  let state: BackgroundState = { ...emptyState(1), enabled: true, generation: 'g1', tokens: { weight: 'w1' } };
  // The progress entry only, as background.ts writes it; the control state stays the screen's.
  const store = { load: async () => state, save: async (progress: BackgroundProgress) => { state = { ...state, ...progress }; } };
  const requests: string[] = [];
  let refused = true, previous: SyncReport | null = null;
  const refuses = (body: unknown) => refused && (body as AppleNight).date === date(40) && (body as AppleNight).source_id === 'writer-2';
  const pull = async (clock: number, data?: { days: Daily[]; nights: AppleNight[] }) => {
    // As App.tsx decides and composes them, inside syncExclusively.
    const full = fullDue(previous, clock);
    const read = data ?? { days: days(full ? 30 : 7), nights: nights(full ? 60 : 7) };
    const skip = await foregroundSkip(1, store, async value => `#${value}`, clock);
    previous = await syncHealth({ userId: 1, full, previous, now: () => new Date(clock).toISOString(),
      daily: async () => ({ days: read.days, notices: [], complete: true }),
      sleep: async () => ({ nights: read.nights, notices: [], complete: true }),
      send: async (path, body) => {
        requests.push(path);
        if (refuses(body)) throw new ApiError(422);
        return { received: 1, checked_at: new Date(clock).toISOString() };
      },
      save: async () => {}, progress: () => {}, unsent: skip.unsent, sent: skip.sent });
    await skip.commit();
    return previous;
  };
  const first = await pull(start);
  assert.equal(first.complete, false); assert.equal(first.retryFull, true, 'the refused night keeps the full reread due');
  assert.equal(requests.length, 1 + 1 + 60 * 3, 'the full reread sends everything once');
  for (const minute of [2, 5, 9]) assert.equal((await pull(start + minute * 60000)).received, 0);
  assert.equal(requests.length, 182, 'no second full attempt within ten minutes, nothing changed: nothing sent');
  // Ten minutes later the full reread is due again: only the night never confirmed is sent.
  refused = false;
  assert.equal((await pull(start + 11 * 60000)).complete, true);
  assert.deepEqual(requests.slice(182), ['/api/mobile/nights']);
  // A corrected weigh-in is the only row of the next pull; the tokens written meanwhile stay.
  await pull(start + 12 * 60000, { days: days(7).map(d => d === weighIn ? { ...d, value: 69.9 } : d), nights: nights(7) });
  assert.deepEqual(requests.slice(183), ['/api/mobile/daily']);
  assert.deepEqual(state.tokens, { weight: 'w1' });
  assert.equal(state.enabled, true);
  // A background run right after finds the same data already confirmed.
  const f = fixture();
  f.stored.state = state;
  f.data.days = [{ ...weighIn, value: 69.9 }, steps];
  assert.equal(await createEngine(f.deps).request('task'), 'unchanged');
  assert.equal(f.sent.length, 0);
});
