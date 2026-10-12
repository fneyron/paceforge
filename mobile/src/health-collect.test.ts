import test from 'node:test';
import assert from 'node:assert/strict';
import { collectDaily, collectNights, HealthQueries } from './health-collect';
import { healthReader } from './health-read';

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
    queryStatisticsForQuantity: async id => { if (id.endsWith('RespiratoryRate')) throw Error('locked'); return { sources: [] }; },
  });
  const result = await collectDaily(d, quiet, 1);
  assert.equal(result.complete, false);
  assert.equal(result.days.length, 1);
  assert.equal(result.days[0].value, 70);
  assert.equal(result.days[0].measured_at, '2026-10-12T08:00:00.000Z');
  assert.match(result.notices.join(' '), /Respiration sur 24 h.*déverrouillé/);
  assert.doesNotMatch(result.notices.join(' '), /autorisations/);
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
