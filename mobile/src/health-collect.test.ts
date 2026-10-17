import test from 'node:test';
import assert from 'node:assert/strict';
import { collectDaily, collectNights, HealthQueries } from './health-collect';
import { healthReader, HealthAuthorizationRequired, HealthDataLocked } from './health-read';

const now = () => new Date('2026-10-12T12:00:00Z');
function queries(overrides: Partial<HealthQueries> = {}): HealthQueries {
  return { now, digest: async s => s, queryCategorySamples: async () => [],
    queryQuantitySamples: async () => [], queryStatisticsForQuantity: async () => ({ sources: [] }), ...overrides };
}
const quiet = () => {};

test('optional empty Apple categories are complete reads, without fabricated measures', async () => {
  const d = queries();
  assert.deepEqual(await collectDaily(d, quiet, 7), {
    days: [], complete: true, notices: ['Aucune mesure quotidienne lisible. Apple ne distingue pas un accès refusé d’une absence de données.'],
  });
  assert.deepEqual(await collectNights(d, quiet), { nights: [], complete: true, notices: [] });
});

test('one failing category preserves the latest weighing and reports the category, not permissions', async () => {
  const d = queries({
    queryQuantitySamples: (async (id: string) => id.endsWith('BodyMass') ? [{ quantity: 70,
      startDate: new Date('2026-10-12T08:00:00Z'), sourceRevision: { source: { name: 'Balance' } } }] : []) as unknown as HealthQueries['queryQuantitySamples'],
    queryStatisticsForQuantity: async id => { if (id.endsWith('RespiratoryRate')) throw new HealthDataLocked(); return { sources: [] }; },
  });
  const result = await collectDaily(d, quiet, 1);
  assert.equal(result.complete, false);
  assert.equal(result.days.length, 1);
  assert.equal(result.days[0].value, 70);
  assert.equal(result.days[0].measured_at, '2026-10-12T08:00:00.000Z');
  assert.match(result.notices.join(' '), /Respiration sur 24 h.*Déverrouille/);
  assert.doesNotMatch(result.notices.join(' '), /autorisations/);
});

const sleepSample = { startDate: new Date('2026-10-11T22:00:00Z'), endDate: new Date('2026-10-12T06:00:00Z'), value: 3,
  sourceRevision: { source: { name: 'Apple Watch', bundleIdentifier: 'com.apple.health' }, productType: 'Watch' },
  device: { localIdentifier: 'test-watch' }, metadata: { HKTimeZone: 'Europe/Paris' } };
function sleepQueries(overrides: Partial<HealthQueries> = {}): HealthQueries {
  return queries({ queryCategorySamples: (async () => [sleepSample]) as unknown as HealthQueries['queryCategorySamples'], ...overrides });
}

test('an existing pairing with newly added sleep permissions gets an actionable notice, without querying or prompting', async () => {
  let queried = false;
  const result = await collectNights(queries({
    prepareRead: async id => { assert.equal(id, 'HKCategoryTypeIdentifierSleepAnalysis'); throw new HealthAuthorizationRequired(); },
    queryCategorySamples: async () => { queried = true; return []; },
  }), quiet);
  assert.equal(queried, false);
  assert.equal(result.complete, false);
  assert.equal(result.nights.length, 0);
  assert.match(result.notices.join(' '), /Touche « Synchroniser » pour l’accorder/);
  assert.doesNotMatch(result.notices.join(' '), /[Dd]éverrouill/);
});

test('after permissions have been requested, sleep imports without treating optional empty heart readings as errors', async () => {
  const prepared: string[] = [];
  const result = await collectNights(sleepQueries({ prepareRead: async id => { prepared.push(id); } }), quiet);
  assert.equal(result.complete, true);
  assert.equal(result.nights.length, 1);
  assert.equal(result.nights[0].date, '2026-10-12');
  assert.equal(result.nights[0].sleep.length, 1);
  assert.deepEqual(result.nights[0].hr, []);
  assert.deepEqual(result.notices, []);
  assert.equal(prepared.length, 4);
});

test('a missing optional nightly permission preserves sleep and the other readings', async () => {
  const calls: string[] = [];
  const result = await collectNights(sleepQueries({
    prepareRead: async id => { if (id.endsWith('HeartRateVariabilitySDNN')) throw new HealthAuthorizationRequired(); },
    queryQuantitySamples: (async (id: string) => { calls.push(id); return id.endsWith('HeartRate')
      ? [{ ...sleepSample, startDate: new Date('2026-10-12T00:00:00Z'), quantity: 52 }] : []; }) as unknown as HealthQueries['queryQuantitySamples'],
  }), quiet);
  assert.equal(result.complete, false);
  assert.equal(result.nights.length, 1);
  assert.deepEqual(result.nights[0].hr, [{ at: '2026-10-12T00:00:00.000Z', value: 52 }]);
  assert.equal(calls.some(id => id.endsWith('HeartRateVariabilitySDNN')), false);
  assert.match(result.notices.join(' '), /VFC nocturne.*Touche « Synchroniser » pour l’accorder/);
});

test('processing failures are not reported as a locked phone or a failed HealthKit query', async () => {
  const result = await collectNights(sleepQueries({ digest: async () => { throw Error('unavailable'); } }), quiet);
  assert.equal(result.complete, false);
  assert.match(result.notices.join(' '), /relevés ont été lus.*traitement/);
  assert.doesNotMatch(result.notices.join(' '), /[Dd]éverrouill|lecture indisponible/);
});

test('a late permission check never starts a native query after its timeout', async () => {
  let resume: (() => void) | undefined, queried = false;
  const result = await collectNights(queries({ reader: healthReader(1000, 10),
    prepareRead: () => new Promise<void>(resolve => { resume = resolve; }),
    queryCategorySamples: async () => { queried = true; return []; },
  }), quiet);
  resume!(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(queried, false);
  assert.equal(result.complete, false);
  assert.match(result.notices.join(' '), /délai maximal/);
});

test('daily history is read newest first with at most five native queries in flight', async () => {
  let active = 0, peak = 0;
  const starts: number[] = [];
  const sample = async (options: { filter?: { date?: { startDate?: Date } } }) => {
    active++; peak = Math.max(peak, active); starts.push(+options.filter!.date!.startDate!);
    await new Promise(r => setTimeout(r, 2)); active--;
  };
  const d = queries({
    queryQuantitySamples: (async (_id: string, options: never) => { await sample(options); return []; }) as unknown as HealthQueries['queryQuantitySamples'],
    queryStatisticsForQuantity: async (_id, _stats, options) => { await sample(options!); return { sources: [] }; },
  });
  assert.equal((await collectDaily(d, quiet, 7)).complete, true);
  assert.equal(starts.length, 35); assert.equal(peak, 5); assert.equal(active, 0);
  assert.deepEqual(starts, [...starts].sort((a, b) => b - a));
});

test('an exhausted budget reports an interruption without launching more HealthKit queries', async () => {
  let calls = 0;
  const d = queries({ reader: healthReader(0),
    queryQuantitySamples: async () => { calls++; return []; },
    queryStatisticsForQuantity: async () => { calls++; return { sources: [] }; },
  });
  const result = await collectDaily(d, quiet, 30);
  assert.equal(calls, 0); assert.equal(result.complete, false); assert.equal(result.days.length, 0);
  assert.match(result.notices[0], /délai maximal/);
});

test('HealthKit fractional step totals become whole counts without changing heart-rate averages', async () => {
  const d = queries({ queryStatisticsForQuantity: async id => ({ sources: [{ name: 'iPhone', bundleIdentifier: 'com.apple.health' }],
    ...(id.endsWith('StepCount') ? { sumQuantity: { quantity: 1245.75, unit: 'count' } } :
      { averageQuantity: { quantity: id.endsWith('HeartRate') ? 64.25 : 15.5, unit: 'count/min' } }),
  }) });
  const result = await collectDaily(d, quiet, 1);
  assert.equal(result.complete, true);
  assert.equal(result.days.find(d => d.metric === 'steps')?.value, 1246);
  assert.equal(result.days.find(d => d.metric === 'hr_day')?.value, 64.25);
});
