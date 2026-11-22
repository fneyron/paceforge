import { test } from 'node:test';
import assert from 'node:assert/strict';
import { RETRY_BUDGET_MS, retryDelay } from './retry';

test('a page the proxy answered during a deployment is asked for again, soon then every 3 s', () => {
  for (const status of [502, 503, 504]) assert.equal(retryDelay(status, 0, 0), 1000, String(status));
  assert.deepEqual([0, 1, 2, 3, 4, 9].map(tries => retryDelay(502, 0, tries)), [1000, 1500, 2250, 3000, 3000, 3000]);
});

test('the app\'s own errors are final: a missing page, a refused one, a failure of its code', () => {
  for (const status of [400, 401, 403, 404, 410, 429, 500]) assert.equal(retryDelay(status, 0, 0), null, String(status));
});

test('the retries of one page end within the budget, then the error is shown', () => {
  let elapsed = 0, tries = 0;
  for (let wait = retryDelay(503, elapsed, tries); wait !== null; wait = retryDelay(503, elapsed, tries)) { elapsed += wait; tries += 1; }
  assert.ok(elapsed <= RETRY_BUDGET_MS && elapsed > RETRY_BUDGET_MS - 3000, String(elapsed));
  assert.ok(tries >= 20, String(tries));
  assert.equal(retryDelay(503, RETRY_BUDGET_MS, 0), null);
});
