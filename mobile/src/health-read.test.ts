import test from 'node:test';
import assert from 'node:assert/strict';
import { healthReader, HealthReadTimeout, HealthAuthorizationRequired, HealthDataLocked, readIssue } from './health-read';

test('a stalled native read times out; expiry does not start another query', async () => {
  const read = healthReader(10, 10);
  let resolve: ((value: number) => void) | undefined;
  await assert.rejects(read(() => new Promise<number>(done => { resolve = done; })), /trop longue/);
  resolve!(70); // a late native callback cannot change the rejected result
  let queried = false;
  await assert.rejects(read(async () => { queried = true; return 1; }), /trop longue/);
  assert.equal(queried, false);
});

test('fast reads and native errors retain their result without leaving a timeout', async () => {
  const read = healthReader(1000);
  assert.equal(await read(async () => 70), 70);
  await assert.rejects(read(async () => { throw Error('permission'); }), /permission/);
});

test('timeouts and native failures give distinct advice, without claiming permissions were denied', () => {
  assert.match(readIssue('Poids', new HealthReadTimeout()), /Poids.*délai maximal/);
  assert.match(readIssue('Sommeil', new HealthDataLocked()), /Sommeil.*Déverrouille/);
  assert.doesNotMatch(readIssue('Poids', Error('unknown')), /autorisations|refusé|[Dd]éverrouill/);
  assert.doesNotMatch(readIssue('Sommeil', new TypeError('bad sample')), /[Dd]éverrouill/);
});

test('new HealthKit permissions and native NSError codes give the correct next action', () => {
  for (const error of [new HealthAuthorizationRequired(), { domain: 'HKErrorDomain', code: 5 },
    Error('Error Domain=HKErrorDomain Code=5 "Authorization not determined"'), Error('Authorization not determined')]) {
    assert.match(readIssue('Sommeil', error), /Touche « Synchroniser » pour l’accorder/);
    assert.doesNotMatch(readIssue('Sommeil', error), /[Dd]éverrouill|refusé/);
  }
  for (const error of [{ domain: 'HKErrorDomain', code: 6 },
    Error('Error Domain=HKErrorDomain Code=6 "Protected health data is inaccessible"'), Error('Protected health data is inaccessible')]) {
    assert.match(readIssue('Sommeil', error), /Déverrouille/);
  }
  assert.doesNotMatch(readIssue('Sommeil', { domain: 'OtherDomain', code: 6 }), /Déverrouille/);
});
