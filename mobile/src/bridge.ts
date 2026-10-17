import { trusted } from './api';
import { Connections, readConnections } from './connections';

export type Pending =
  | { kind: 'pair'; verifier: string; challenge: string; url: string }
  | { kind: 'check'; nonce: string; url: string; consent: boolean; ask?: boolean; pageUrl?: string }
  | { kind: 'connections'; nonce: string; url: string; pageUrl?: string };
type Reply =
  | { kind: 'pair'; task: Extract<Pending, { kind: 'pair' }>; code: string }
  | { kind: 'check'; task: Extract<Pending, { kind: 'check' }>; userId: number }
  | { kind: 'login' | 'unavailable'; task: Extract<Pending, { kind: 'check' | 'connections' }> }
  | { kind: 'connections'; task: Extract<Pending, { kind: 'connections' }>; snapshot: Connections };

// Consume before any async work: retries cannot exchange a code or upload twice.
export function takeReply(pending: { current: Pending | null }, raw: string, url: string): Reply | null {
  const task = pending.current;
  if (!task) return null;
  let data;
  try { data = JSON.parse(raw); } catch { return null; }
  if (!data || typeof data !== 'object') return null;
  let reply: Reply | null = null;
  if (task.kind === 'connections') {
    const inline = task.pageUrl && trusted(url) && new URL(url).pathname === new URL(task.pageUrl).pathname && data.transport === 'inline';
    const routed = !task.pageUrl && trusted(url, '/mobile/connections');
    if (inline || routed) {
      const snapshot = readConnections(data, task.url, task.nonce);
      if (snapshot) reply = { kind: 'connections', task, snapshot };
      else if (inline && data.nonce === task.nonce && (data.type === 'connections-login' || data.type === 'connections-unavailable')) {
        reply = { kind: data.type === 'connections-login' ? 'login' : 'unavailable', task };
      }
    }
  } else if (task.kind === 'pair' && trusted(url, '/mobile/connect') && data.type === 'pair' &&
    data.challenge === task.challenge && typeof data.code === 'string' && /^[A-Za-z0-9_-]{43,128}$/.test(data.code)) {
    reply = { kind: 'pair', task, code: data.code };
  } else if (task.kind === 'check' && data.nonce === task.nonce) {
    const inline = task.pageUrl && trusted(url) && new URL(url).pathname === new URL(task.pageUrl).pathname && data.transport === 'inline';
    const routed = !task.pageUrl && trusted(url, '/mobile/session');
    if ((inline || routed) && data.type === 'session' && Number.isSafeInteger(data.user_id)) {
      reply = { kind: 'check', task, userId: data.user_id };
    } else if (inline && (data.type === 'session-login' || data.type === 'session-unavailable')) {
      reply = { kind: data.type === 'session-login' ? 'login' : 'unavailable', task };
    }
  }
  if (reply) pending.current = null;
  return reply;
}

export function acknowledge(task: Pending): string {
  const key = task.kind === 'pair' ? task.challenge : task.nonce;
  return `window.paceforgeBridge?.ack(${JSON.stringify(key)}, true); true;`;
}
export const retryBridgeScript = 'window.paceforgeBridge?.send(); true;';

// Same WebView/cookies, same nonce/account check, without navigating away from
// the dashboard. No HealthKit reads precede the verified response.
export function inlineSessionScript(task: Extract<Pending, { kind: 'check' }>): string {
  return inlineReadScript(task, 'session');
}
export function inlineConnectionsScript(task: Extract<Pending, { kind: 'connections' }>): string {
  return inlineReadScript(task, 'connections');
}
function inlineReadScript(task: Extract<Pending, { kind: 'check' | 'connections' }>, type: 'session' | 'connections'): string {
  return `(async function () {
    if (location.origin !== ${JSON.stringify(new URL(task.url).origin)}) return;
    const send = data => window.ReactNativeWebView?.postMessage(JSON.stringify({...data, transport:'inline', page_url:location.href, nonce:${JSON.stringify(task.nonce)}}));
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 25000);
    try {
      const response = await fetch(${JSON.stringify(task.url)}, {credentials:'same-origin', cache:'no-store', headers:{Accept:'application/json'}, signal:controller.signal});
      if (response.redirected || response.status === 401 || response.status === 403) send({type:'${type}-login'});
      else if (!response.ok) send({type:'${type}-unavailable'});
      else {
        const data = await response.json();
        if (data.type === '${type}' && data.nonce === ${JSON.stringify(task.nonce)}) send(data);
        else send({type:'${type}-unavailable'});
      }
    } catch (_) { send({type:'${type}-unavailable'}); }
    finally { clearTimeout(timer); }
  })(); true;`;
}
