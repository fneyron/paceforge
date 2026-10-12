import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { ORIGIN } from './api';
import { acknowledge, inlineSessionScript, Pending, takeReply } from './bridge';

test('inline identity uses the same account check without navigating; query-only chart changes are allowed', () => {
  const task: Pending = { kind: 'check', nonce: 'a'.repeat(64), url: ORIGIN + '/mobile/session', pageUrl: ORIGIN + '/sante?r=14', consent: false };
  const pending = { current: task as Pending | null };
  const data = { type: 'session', nonce: task.nonce, user_id: 1, transport: 'inline' };
  for (const url of ['https://example.org/sante', ORIGIN + '/settings', ORIGIN + '/mobile/session']) {
    assert.equal(takeReply(pending, JSON.stringify(data), url), null);
  }
  for (const bad of [{ ...data, transport: undefined }, { ...data, nonce: 'old' }, { ...data, user_id: null }]) {
    assert.equal(takeReply(pending, JSON.stringify(bad), task.pageUrl!), null);
  }
  assert.equal(takeReply(pending, JSON.stringify(data), ORIGIN + '/sante?r=90#sommeil')?.kind, 'check');
  assert.equal(pending.current, null);
});

test('inline session script reports expired login, transport failures and malformed replies without trusting them', async () => {
  const task: Extract<Pending, { kind: 'check' }> = { kind: 'check', nonce: 'n'.repeat(64), url: ORIGIN + '/mobile/session', consent: false, pageUrl: ORIGIN + '/sante' };
  for (const scenario of ['ok', 'redirect', 'offline', 'bad-nonce', 'forbidden']) {
    const sent: any[] = [];
    const context = { location: { origin: ORIGIN }, AbortController, setTimeout, clearTimeout,
      window: { ReactNativeWebView: { postMessage: (raw: string) => sent.push(JSON.parse(raw)) } },
      fetch: async (_url: string, opts: any) => {
        assert.equal(opts.credentials, 'same-origin'); assert.equal(opts.cache, 'no-store');
        if (scenario === 'offline') throw new TypeError('offline');
        return { ok: scenario !== 'forbidden', redirected: scenario === 'redirect', status: scenario === 'forbidden' ? 403 : 200,
          json: async () => ({ type: 'session', nonce: scenario === 'bad-nonce' ? 'old' : task.nonce, user_id: 9 }) };
      } };
    runInNewContext(inlineSessionScript(task), context);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(sent.length, 1);
    const reply = takeReply({ current: task }, JSON.stringify(sent[0]), task.pageUrl!);
    assert.equal(reply?.kind, scenario === 'ok' ? 'check' : ['redirect', 'forbidden'].includes(scenario) ? 'login' : 'unavailable');
    if (reply?.kind === 'check') assert.equal(reply.userId, 9); // App still compares the paired account before health reads.
  }
});

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
  const navigations: string[] = [];
  const window: Record<string, any> = { location: { replace: (url: string) => navigations.push(url) },
    addEventListener: (_: string, callback: () => void) => { leave = callback; } };
  const context = { window, document: { getElementById: (id: keyof typeof nodes) => nodes[id] },
    setInterval: (callback: () => void) => { timer = callback; return 1; }, clearInterval: () => { timer = undefined; } };
  runInNewContext(readFileSync(new URL('../../app/static/js/mobile-bridge.js', import.meta.url), 'utf8'), context);
  return { payload, window, context, nodes, navigations, tick: () => timer?.(), active: () => !!timer, leave: () => leave() };
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
  assert.deepEqual(d.navigations, [], 'the updated native app owns its return destination');
  d.window.paceforgeBridge.send(); assert.equal(sent.length, 2);
});

test('an acknowledged bridge in an older installed app returns to Santé exactly once', () => {
  const d = delivery();
  d.window.paceforgeBridge.ack('wrong');
  assert.equal(d.navigations.length, 0);
  d.window.paceforgeBridge.ack(d.payload.nonce);
  assert.equal(d.navigations.length, 1);
  assert.match(d.navigations[0], /^\/sante\?app_nav=\d+$/);
  assert.equal(d.active(), false);
  d.window.paceforgeBridge.ack(d.payload.nonce);
  d.window.paceforgeBridge.retry();
  assert.equal(d.navigations.length, 1);
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
