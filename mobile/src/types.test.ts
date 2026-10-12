import { test } from 'node:test';
import assert from 'node:assert/strict';
import { localDay, windows, valid, latestWeighIn } from './types';
import { trusted } from './api';

test('days follow the local clock and daylight saving, not 24h subtraction', () => {
  process.env.TZ = 'Europe/Paris';
  const result = windows(new Date('2026-10-26T12:00:00+01:00'));
  assert.equal(result.length, 30);
  const change = result.find(w => w.date === '2026-10-25')!;
  assert.equal((+change.end - +change.start) / 3600000, 25);
  assert.equal(localDay(new Date('2026-10-09T23:30:00Z')), '2026-10-10');
});
test('latest valid weighing survives an invalid newer sample and timestamps determine order', () => {
  const now = new Date('2026-10-12T12:00:00Z');
  const sample = (value: number, at: string) => ({ value, at: `2026-10-12T${at}:00Z`, source: 'Scale' });
  const result = latestWeighIn('weight', '2026-10-12', [sample(69, '07:00'), sample(0, '11:00'), sample(70, '08:00'), sample(80, '14:00')], now);
  assert.equal(result?.value, 70); assert.equal(result?.measured_at, '2026-10-12T08:00:00Z');
  assert.equal(latestWeighIn('weight', '2026-10-12', [], now), undefined);
  assert.equal(windows(now, 7).length, 7);
});
test('missing measurements never become zeros; valid zero steps remains measurable', () => {
  const base = { date: '2026-10-10', metric: 'steps' as const, unit: 'count', sources: ['watch'] };
  assert.equal(valid({ ...base, value: NaN }), false);
  assert.equal(valid({ ...base, value: 0 }), true);
  assert.equal(valid({ ...base, value: 0, sources: [] }), false);
  assert.equal(valid({ ...base, metric: 'weight', value: 0 }), false);
});
test('native bridge trusts only the production HTTPS origin and exact route', () => {
  assert.ok(trusted('https://paceforge.fr/mobile/connect?challenge=a', '/mobile/connect'));
  for (const url of ['https://paceforge.fr.evil.test/mobile/connect', 'http://paceforge.fr/mobile/connect', 'https://paceforge.fr@evil.test/mobile/connect', 'javascript:alert(1)', 'https://paceforge.fr/settings']) assert.equal(trusted(url, '/mobile/connect'), false);
});
