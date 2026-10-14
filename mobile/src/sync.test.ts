import test from 'node:test';
import assert from 'node:assert/strict';
import { ApiError, rejectedField } from './api';
import { due, fullDue, syncIssue, syncHealth, SyncReport } from './sync';
import type { Daily, HealthResult, SleepResult } from './types';
import type { AppleNight } from './night-samples';

const at = '2026-10-12T10:00:00Z';
const weighing: Daily = { date: '2026-10-12', metric: 'weight', value: 70, unit: 'kg',
  measured_at: '2026-10-12T07:00:00Z', sources: ['Scale'] };
const night = { date: '2026-10-12' } as AppleNight;
function fixture() {
  const events: string[] = [], saved: SyncReport[] = [];
  return { events, saved, deps: {
    userId: 1, full: true, previous: null as SyncReport | null, now: () => at,
    daily: async (): Promise<HealthResult> => { events.push('read daily'); return { days: [weighing], notices: [], complete: true }; },
    sleep: async (): Promise<SleepResult> => { events.push('read sleep'); return { nights: [night], notices: [], complete: true }; },
    send: async (path: string, _body: unknown): Promise<unknown> => {
      events.push(path); return { received: 1, checked_at: at, latest: { weight: weighing } };
    },
    save: async (r: SyncReport) => { saved.push(r); }, progress: (_text: string) => {},
  } };
}

test('daily measurements are confirmed before reading sleep; interrupted work stays partial', async () => {
  const { deps, events, saved } = fixture();
  const report = await syncHealth(deps);
  assert.deepEqual(events, ['read daily', '/api/mobile/daily', 'read sleep', '/api/mobile/nights']);
  assert.equal(saved[0].complete, false);
  assert.equal(saved[1].complete, false);
  assert.equal(saved[1].weightReceived?.value, 70);
  assert.equal(report.complete, true); assert.equal(report.received, 2);
  assert.equal(report.fullAt, at); assert.equal(report.retryFull, false);
});

test('failed sleep preserves the successful weighing and schedules a full retry', async () => {
  const { deps } = fixture();
  deps.sleep = async () => { throw Error('HealthKit unavailable'); };
  const report = await syncHealth(deps);
  assert.equal(report.complete, false); assert.equal(report.weightReceived?.value, 70);
  assert.equal(report.lastReceivedAt, at); assert.equal(report.fullAt, '');
  assert.equal(fullDue(report), true);
  assert.equal(due(report, 0, Date.parse(at) + 4 * 60000), false);
  assert.equal(due(report, 0, Date.parse(at) + 5 * 60000), true);
});

test('stale ignored upload shows the actual server weight, not the older local reading', async () => {
  const { deps } = fixture();
  deps.send = async () => ({ received: 0, checked_at: at, latest: { weight: { value: 71, measured_at: at } } });
  const report = await syncHealth(deps);
  assert.equal(report.received, 0); assert.equal(report.lastReceivedAt, '');
  assert.equal(report.weightRead?.value, 70); assert.equal(report.weightReceived?.value, 71);
});

test('empty reads never create a zero weighing or a successful delivery timestamp', async () => {
  const { deps } = fixture();
  deps.daily = async () => ({ days: [], notices: [], complete: true });
  deps.sleep = async () => ({ nights: [], notices: [], complete: true });
  deps.send = async () => ({ received: 0, checked_at: at, latest: {} });
  const report = await syncHealth(deps);
  assert.equal(report.complete, true); assert.equal(report.received, 0);
  assert.equal(report.lastReceivedAt, ''); assert.equal(report.weightRead, null); assert.equal(report.weightReceived, null);
});

test('transport/authentication errors stop further reads and never claim successful synchronization', async () => {
  for (const code of [401, 503]) {
    const { deps, events } = fixture(); deps.send = async () => { throw new ApiError(code); };
    const report = await syncHealth(deps);
    assert.equal(report.complete, false); assert.equal(report.reconnect, code === 401);
    assert.equal(report.received, 0); assert.equal(report.weightReceived, null);
    assert.deepEqual(events, ['read daily']);
  }
});

test('one rejected night does not block other nights; a partial read is still marked incomplete', async () => {
  const { deps } = fixture(); let nights = 0;
  deps.daily = async () => ({ days: [weighing], notices: [], complete: false });
  deps.sleep = async () => ({ nights: [night, night, night], notices: [], complete: true });
  deps.send = async path => {
    if (path.endsWith('/nights') && ++nights === 1) throw new ApiError(422);
    return { received: 1, checked_at: at };
  };
  const report = await syncHealth(deps);
  assert.equal(nights, 3); assert.equal(report.received, 3);
  assert.equal(report.complete, false); assert.equal(report.fullAt, '');
});

test('cold start and foreground use the same short cadence; full history is periodically reread', async () => {
  assert(due(null, 0, Date.parse(at))); assert(fullDue(null, Date.parse(at)));
  const { deps } = fixture(); const report = await syncHealth(deps);
  assert(!due(report, 0, Date.parse(at) + 14 * 60000));
  assert(due(report, 0, Date.parse(at) + 15 * 60000));
  assert(!fullDue(report, Date.parse(at) + 15 * 60000));
  assert(fullDue(report, Date.parse(at) + 6 * 3600000));
  assert(!due(null, Date.parse(at), Date.parse(at) + 60000));
});

test('receipt state never crosses accounts and malformed acknowledgements are rejected', async () => {
  const { deps } = fixture(); const prior = await syncHealth(deps);
  deps.userId = 2; deps.previous = prior;
  deps.send = async () => ({ received: '1', checked_at: at });
  const report = await syncHealth(deps);
  assert.equal(report.complete, false); assert.equal(report.lastReceivedAt, '');
  assert.equal(report.weightReceived, null); assert.equal(report.fullAt, '');
});

test('sync warnings name the failed step, ahead of optional empty-data notices', async () => {
  const { deps } = fixture();
  deps.daily = async () => ({ days: [], complete: true, notices: ['Aucune mesure quotidienne lisible.'] });
  deps.sleep = async () => ({ nights: [], complete: false, notices: ['Sommeil : lecture interrompue par le délai maximal.'] });
  const report = await syncHealth(deps);
  assert.equal(syncIssue(report, 'Apple Santé'), 'Apple Santé · Sommeil : lecture interrompue par le délai maximal.');
  assert.equal(syncIssue({ ...report, complete: true }, 'Apple Santé'), '');
  assert.equal(syncIssue({ ...report, reconnect: true }, 'Apple Santé'), 'Reconnecte le téléphone dans Réglages.');
});

test('server rejection details expose only known field labels, never measurement payloads', () => {
  const field = rejectedField({ detail: [{ loc: ['body', 'days', 0, 'value'], msg: 'private message', input: 72.3 }] });
  assert.equal(field, 'valeur');
  assert.equal(new ApiError(422, field).message, 'Mesures refusées par PaceForge (422 · valeur).');
  for (const raw of [null, 'html', { detail: 'private value' }, { detail: [{ loc: ['body', 'secret'], input: 'secret' }] }]) {
    assert.equal(rejectedField(raw), '');
  }
});

test('background runs never reread the history, stop at their deadline and stay pending', async () => {
  const { deps, events, saved } = fixture();
  let clock = Date.parse(at);
  const report = await syncHealth({ ...deps, full: true, trigger: 'background', clock: () => clock,
    deadline: clock + 1000, daily: async () => { events.push('read daily'); clock += 2000; return { days: [weighing], notices: [], complete: true }; } });
  assert.deepEqual(events, ['read daily'], 'nothing sent after the deadline');
  assert.equal(report.pending, true); assert.equal(report.complete, false);
  assert.equal(report.fullAt, ''); assert.equal(saved.at(-1)?.trigger, 'background');
  const finished = await syncHealth({ ...fixture().deps, previous: { ...report, retryFull: true }, trigger: 'background' });
  assert.equal(finished.complete, true); assert.equal(finished.pending, false);
  assert.equal(finished.fullAt, '', 'a background run is never the full reread');
  assert.equal(finished.retryFull, true, 'the full reread stays due for the app');
});

test('unchanged payloads are skipped; only confirmed sends are recorded, with the read time', async () => {
  const { deps, events } = fixture();
  const recorded: string[] = [], metas: unknown[] = [];
  const report = await syncHealth({ ...deps,
    send: async (path, body, meta) => { metas.push(meta); return deps.send(path, body); },
    unsent: async path => path.endsWith('/daily') ? null : night,
    sent: async path => { recorded.push(path); } });
  assert.deepEqual(events, ['read daily', 'read sleep', '/api/mobile/nights']);
  assert.deepEqual(recorded, ['/api/mobile/nights']);
  assert.deepEqual(metas, [{ readAt: at }]);
  assert.equal(report.complete, true); assert.equal(report.received, 1);
  const failing = fixture();
  const kept: string[] = [];
  await syncHealth({ ...failing.deps, send: async () => { throw new ApiError(503); }, sent: async path => { kept.push(path); } });
  assert.deepEqual(kept, []);
});

test('a pending background run makes the next opening sync at once, at most once a minute', () => {
  const report = { complete: true, pending: true, checkedAt: at } as SyncReport;
  const now = Date.parse(at) + 60000;
  assert.equal(due(report, 0, now), true);
  assert.equal(due(report, now - 30000, now), false);
  assert.equal(due({ ...report, pending: false }, 0, now), false);
});
