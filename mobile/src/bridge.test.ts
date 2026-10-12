import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { ORIGIN } from './api';
import { acknowledge, Pending, takeReply } from './bridge';

test('session replies must match the origin, route and active request, then are consumed exactly once', () => {
  const task: Pending = { kind: 'check', nonce: 'a'.repeat(64), url: ORIGIN + '/mobile/session', consent: false };
  const pending = { current: task as Pending | null };
  const data = { type: 'session', nonce: task.nonce, user_id: 2 };
  for (const raw of ['{broken', 'null', '{}', JSON.stringify({ ...data, nonce: 'old' }), JSON.stringify({ ...data, user_id: '2' })]) {
    assert.equal(takeReply(pending, raw, task.url), null);
    assert.equal(pending.current, task);
  }
  for (const url of ['https://example.org/mobile/session', ORIGIN + '/settings', ORIGIN + '/mobile/session/extra']) {
    assert.equal(takeReply(pending, JSON.stringify(data), url), null);
  }
  const reply = takeReply(pending, JSON.stringify(data), task.url + '?nonce=' + task.nonce);
  assert.equal(reply?.kind, 'check');
  assert.equal(pending.current, null);
  assert.equal(takeReply(pending, JSON.stringify(data), task.url), null);
  // A delayed response from the preceding request cannot consume a newer one.
  pending.current = { ...task, nonce: 'b'.repeat(64) };
  assert.equal(takeReply(pending, JSON.stringify(data), task.url), null);
  assert.ok(pending.current);
});

test('a repeated pairing message cannot exchange its one-use code twice', () => {
  const task: Pending = { kind: 'pair', challenge: 'a'.repeat(64), verifier: 'v'.repeat(64), url: ORIGIN + '/mobile/connect' };
  const pending = { current: task as Pending | null };
  const raw = JSON.stringify({ type: 'pair', challenge: task.challenge, code: 'c'.repeat(43) });
  assert.equal(takeReply(pending, raw, task.url)?.kind, 'pair');
  assert.equal(takeReply(pending, raw, task.url), null);
});

function delivery() {
  const payload = { type: 'session', nonce: 'a'.repeat(64), user_id: 2 };
  const nodes = { 'paceforge-bridge-message': { textContent: JSON.stringify(payload) },
    'bridge-status': { textContent: '' }, 'bridge-retry': { hidden: true } };
  let timer: (() => void) | undefined, leave = () => {};
  const window: Record<string, any> = { addEventListener: (_: string, callback: () => void) => { leave = callback; } };
  const context = { window, document: { getElementById: (id: keyof typeof nodes) => nodes[id] },
    setInterval: (callback: () => void) => { timer = callback; return 1; }, clearInterval: () => { timer = undefined; } };
  runInNewContext(readFileSync(new URL('../../app/static/js/mobile-bridge.js', import.meta.url), 'utf8'), context);
  return { payload, window, context, nodes, tick: () => timer?.(), active: () => !!timer, leave: () => leave() };
}

test('the server retries when the bridge appears late or loses a message and stops only on matching acknowledgement', () => {
  const d = delivery(); const sent: string[] = [];
  d.tick(); // No native bridge on first render.
  d.window.ReactNativeWebView = { postMessage() { throw new Error('restarting'); } };
  d.tick();
  d.window.ReactNativeWebView.postMessage = (message: string) => sent.push(message);
  d.tick(); d.tick(); // First message lost by the native consumer, second arrives.
  assert.equal(sent.length, 2);
  assert.deepEqual(JSON.parse(sent[1]), d.payload);
  d.window.paceforgeBridge.ack('old-request'); assert.equal(d.active(), true);
  runInNewContext(acknowledge({ kind: 'check', nonce: d.payload.nonce, url: ORIGIN + '/mobile/session', consent: false }), d.context);
  assert.equal(d.active(), false);
  d.window.paceforgeBridge.send(); assert.equal(sent.length, 2);
});

test('old app builds without acknowledgement have bounded retries and an explicit way out', () => {
  const d = delivery();
  for (let i = 0; i < 65; i++) d.tick();
  assert.equal(d.active(), false);
  assert.equal(d.nodes['bridge-retry'].hidden, false);
  assert.match(d.nodes['bridge-status'].textContent, /interrompu/);
  d.window.paceforgeBridge.retry();
  assert.equal(d.nodes['bridge-retry'].hidden, true);
  assert.equal(d.active(), true);
  d.leave(); assert.equal(d.active(), false);
});
