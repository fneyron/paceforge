import test from 'node:test';
import assert from 'node:assert/strict';
import { healthReader } from './health-read';

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
