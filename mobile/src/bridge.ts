import { trusted } from './api';
import { Connections, readConnections } from './connections';

export type Pending =
  | { kind: 'pair'; verifier: string; challenge: string; url: string }
  | { kind: 'check'; nonce: string; url: string; consent: boolean }
  | { kind: 'connections'; nonce: string; url: string };
type Reply =
  | { kind: 'pair'; task: Extract<Pending, { kind: 'pair' }>; code: string }
  | { kind: 'check'; task: Extract<Pending, { kind: 'check' }>; userId: number }
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
    const snapshot = readConnections(data, url, task.nonce);
    if (snapshot) reply = { kind: 'connections', task, snapshot };
  } else if (task.kind === 'pair' && trusted(url, '/mobile/connect') && data.type === 'pair' &&
    data.challenge === task.challenge && typeof data.code === 'string' && /^[A-Za-z0-9_-]{43,128}$/.test(data.code)) {
    reply = { kind: 'pair', task, code: data.code };
  } else if (task.kind === 'check' && trusted(url, '/mobile/session') && data.type === 'session' &&
    data.nonce === task.nonce && Number.isSafeInteger(data.user_id)) {
    reply = { kind: 'check', task, userId: data.user_id };
  }
  if (reply) pending.current = null;
  return reply;
}

export function acknowledge(task: Pending): string {
  const key = task.kind === 'pair' ? task.challenge : task.nonce;
  return `window.paceforgeBridge?.ack(${JSON.stringify(key)}); true;`;
}
export const retryBridgeScript = 'window.paceforgeBridge?.send(); true;';
