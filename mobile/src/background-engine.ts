// Background sync without React, the WebView or the account check: wired to the
// natives by background.ts, tested here with fakes. Health values are never logged.
import { ApiError, authorized, Credentials } from './api';
import { syncHealth, SyncReport } from './sync';
import type { AppleNight } from './night-samples';
import type { ChangePlan, Daily, HealthResult, Metric, Pair, ReadBudget, SleepResult } from './types';

export const TASK_NAME = 'paceforge-health-sync';
export const TASK_INTERVAL_MINUTES = 60;
export const DEBOUNCE_MS = 1500;
// HealthKit keeps the app awake ~25 s after its native callback, which comes before
// JavaScript boots on a cold launch: leave a boot allowance and finish within 14 s
// of the JavaScript callback. A run starting at the very end gets a short attempt.
export const WAKE_MS = 14000;
const MIN_RUN_MS = 3000;
export const RUN_MS = 18000;
export const LOCK_MS = 60000;
// How often a wake checks whether the foreground sync has released the lock.
const RETRY_MS = 1000;
const NIGHTS = 3;
const DAILY: Required<ReadBudget> = { total: 8000, perRead: 3000 };
const SLEEP: Required<ReadBudget> = { total: 10000, perRead: 3000 };
const CHANGES: Required<ReadBudget> = { total: 4000, perRead: 3000 };
const SEND_MS = 8000;
// Hashes only skip identical re-sends: forgotten daily, so a loss on the server heals.
// Shared with the foreground sync (foregroundSkip), whose 7-day window fits in HASH_DAYS.
const HASHES_MS = 24 * 3600000, HASH_DAYS = 10;

// resume: a wake the foreground sync kept waiting, run once it is over.
export type Wake = 'healthkit' | 'task' | 'resume';
export type Outcome = 'off' | 'locked' | 'busy' | 'deferred' | 'unchanged' | 'sent' | 'partial' | 'reconnect' | 'failed';
// logout / mismatch: the web session left the paired account; off until it is confirmed again.
export type Block = '' | 'logout' | 'mismatch';
export type BackgroundState = {
  userId: number | null; enabled: boolean; blocked: Block;
  hashes: Record<string, string>; hashedAt: number;
  tokens: Record<string, string>; retryAt: number;
};
export const emptyState = (userId: number | null = null): BackgroundState =>
  ({ userId, enabled: false, blocked: '', hashes: {}, hashedAt: 0, tokens: {}, retryAt: 0 });
const strings = (v: unknown): Record<string, string> => v && typeof v === 'object' && !Array.isArray(v)
  ? Object.fromEntries(Object.entries(v).filter((e): e is [string, string] => typeof e[1] === 'string')) : {};
const time = (v: unknown) => typeof v === 'number' && Number.isFinite(v) ? v : 0;
export function readState(raw: string | null): BackgroundState {
  let v: Record<string, unknown> | null = null;
  try { v = raw ? JSON.parse(raw) : null; } catch { /* unreadable: start over */ }
  if (!v || typeof v !== 'object') return emptyState();
  return { userId: Number.isSafeInteger(v.userId) ? v.userId as number : null, enabled: v.enabled === true,
    blocked: v.blocked === 'logout' || v.blocked === 'mismatch' ? v.blocked : '',
    hashes: strings(v.hashes), hashedAt: time(v.hashedAt), tokens: strings(v.tokens), retryAt: time(v.retryAt) };
}
export function readPair(raw: string | null): Pair | null {
  try {
    const p = raw ? JSON.parse(raw) : null;
    return p && typeof p.token === 'string' && Number.isSafeInteger(p.user_id) ? p as Pair : null;
  } catch { return null; }
}
export function readReceipt(raw: string | null): SyncReport | null {
  try {
    const r = raw ? JSON.parse(raw) : null;
    return r && Number.isSafeInteger(r.userId) ? r as SyncReport : null;
  } catch { return null; }
}

/** Where the lock is kept: expo-file-system's File in the app. */
export type LockFile = { readonly exists: boolean; textSync(): string; write(text: string): void; delete(): void };
type Holder = { owner?: unknown; until?: unknown };
// null: not a holder (a write cut short, another format); read errors are left to the caller.
const holder = (text: string): Holder | null => {
  try { const v = JSON.parse(text); return v && typeof v === 'object' ? v : null; } catch { return null; }
};
/** Best effort across JavaScript runtimes (Android runs the task headless): one file,
 * a 60 s expiry. A file that cannot be parsed is written over, then checked again; one
 * that cannot be read or written never blocks syncs. */
export function fileLock(open: () => LockFile, now: () => number = Date.now) {
  return {
    async acquire(owner: string, until: number): Promise<boolean> {
      try {
        const file = open();
        const held = file.exists ? holder(file.textSync()) : null;
        // An expiry far beyond a lock period was written under another clock: not a holder.
        if (held && held.owner !== owner && typeof held.until === 'number' && held.until > now()
          && held.until <= now() + 2 * LOCK_MS) return false;
        file.write(JSON.stringify({ owner, until }));
        return holder(file.textSync())?.owner === owner;
      } catch { return true; }
    },
    async release(owner: string): Promise<void> {
      try {
        const file = open();
        if (!file.exists) return;
        const held = holder(file.textSync());
        if (!held || held.owner === owner) file.delete();
      } catch { /* expires */ }
    },
  };
}

export type Stored = { pair: Pair | null; automatic: boolean; receipt: SyncReport | null; state: BackgroundState };
export type EngineDeps = {
  id: string; now: () => number;
  load: () => Promise<Stored>;
  saveState: (state: BackgroundState) => Promise<void>;
  saveReceipt: (report: SyncReport) => Promise<void>;
  savePair: (pair: Pair) => Promise<void>;
  lock: { acquire: (owner: string, until: number) => Promise<boolean>; release: (owner: string) => Promise<void> };
  protectedData: () => Promise<boolean>;
  changes: (tokens: Record<string, string>, budget: ReadBudget) => Promise<ChangePlan>;
  readDaily: (daysBack: number, budget: ReadBudget, metrics?: Metric[]) => Promise<HealthResult>;
  readNights: (daysBack: number, budget: ReadBudget) => Promise<SleepResult>;
  send: (path: string, body: unknown, token: string, timeout: number, readAt: string) => Promise<unknown>;
  digest: (value: string) => Promise<string>;
  disable: () => Promise<void>;
  notify: (report: SyncReport) => void;
  locked: (error: unknown) => boolean;
  debounceMs?: number; retryMs?: number;
};

const dailyKey = (d: Daily) => `d|${d.date}|${d.metric}`;
const nightKey = (n: AppleNight) => `n|${n.date}|${n.source_id}`;
const dailyValue = (d: Daily) => JSON.stringify([d.value, d.unit, d.measured_at ?? null, d.sources]);
function recent(hashes: Record<string, string>, now: number) {
  const oldest = new Date(now - HASH_DAYS * 86400000).toISOString().slice(0, 10);
  return Object.fromEntries(Object.entries(hashes).filter(([key]) => (key.split('|')[1] || '') >= oldest));
}
/** The stored digests, or none once they are a day old. */
function freshHashes(state: BackgroundState, now: number): BackgroundState {
  return now - state.hashedAt > HASHES_MS ? { ...state, hashes: {}, hashedAt: now } : state;
}
/** Skip-if-unchanged for syncHealth: `unsent` leaves out the rows whose digest the server
 * already confirmed, `sent` records what it confirmed. `hashes` is updated in place. */
export function hashSkip(hashes: Record<string, string>, digest: (value: string) => Promise<string>) {
  let dailyDone = false;
  return {
    hashes,
    /** The daily rows were all confirmed, or all already known. */
    dailyDone: () => dailyDone,
    unsent: async (path: string, body: unknown): Promise<unknown | null> => {
      if (path === '/api/mobile/daily') {
        const days: Daily[] = [];
        for (const day of (body as { days: Daily[] }).days) {
          if (hashes[dailyKey(day)] !== await digest(dailyValue(day))) days.push(day);
        }
        if (!days.length) dailyDone = true;
        return days.length ? { days } : null;
      }
      const night = body as AppleNight;
      return hashes[nightKey(night)] === await digest(JSON.stringify(night)) ? null : night;
    },
    sent: async (path: string, body: unknown): Promise<void> => {
      if (path === '/api/mobile/daily') {
        dailyDone = true;
        for (const day of (body as { days: Daily[] }).days) hashes[dailyKey(day)] = await digest(dailyValue(day));
      } else {
        const night = body as AppleNight;
        hashes[nightKey(night)] = await digest(JSON.stringify(night));
      }
    },
  };
}
export type HashStore = { load: () => Promise<BackgroundState>; save: (state: BackgroundState) => Promise<void> };
/** The foreground sync shares the background run's digests, so a pull or a manual sync
 * never sends a day or a night the server already has: callbacks for syncHealth, then
 * `commit` once the sync is over, under the sync lock. */
export async function foregroundSkip(userId: number, store: HashStore, digest: (value: string) => Promise<string>, now = Date.now()) {
  const stored = await store.load().catch(() => emptyState());
  const state = freshHashes(stored.userId === userId ? stored : { ...emptyState(userId), enabled: stored.enabled }, now);
  const skip = hashSkip({ ...state.hashes }, digest);
  return {
    unsent: skip.unsent, sent: skip.sent,
    /** Only the digests: the rest of the state (tokens, block, toggle) is not this sync's. */
    commit: async (): Promise<void> => {
      const latest = await store.load().catch(() => null);
      if (!latest || (latest.userId !== null && latest.userId !== userId)) return;
      await store.save({ ...latest, userId, hashes: recent(skip.hashes, now), hashedAt: state.hashedAt });
    },
  };
}

export function createEngine(deps: EngineDeps) {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let running: Promise<void> | null = null;
  let queue: { wake: Wake; resolve: (outcome: Outcome) => void }[] = [];
  let wakeEnd = 0, runs = 0, pendingHere = false, missed = false;

  /** Coalesces wakes (HealthKit calls once per changed type) into one run at a time. */
  function request(wake: Wake): Promise<Outcome> {
    if (wake === 'healthkit') wakeEnd = Math.max(wakeEnd, deps.now() + WAKE_MS);
    return new Promise(resolve => {
      queue.push({ wake, resolve });
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => { timer = null; void drain(); }, wake === 'healthkit' ? deps.debounceMs ?? DEBOUNCE_MS : 0);
    });
  }
  async function drain() {
    if (running) return; // the current loop takes the new wakes when it finishes
    running = (async () => {
      while (queue.length) {
        const batch = queue;
        queue = [];
        let outcome: Outcome;
        // Only HealthKit wakes are bounded by its window; the task and a resumed wake are not.
        const wake = batch.find(b => b.wake !== 'healthkit')?.wake ?? 'healthkit';
        try { outcome = await once(wake); } catch { outcome = 'failed'; }
        batch.forEach(b => b.resolve(outcome));
      }
    })();
    try { await running; } finally { running = null; }
  }

  async function once(wake: Wake): Promise<Outcome> {
    const start = deps.now();
    // A run starting long after its window (the app was suspended, then woken again or
    // opened) gets a full run: JavaScript only runs when iOS gives it time.
    const deadline = wake === 'healthkit' && start - wakeEnd < 30000
      ? Math.min(start + RUN_MS, Math.max(wakeEnd, start + MIN_RUN_MS)) : start + RUN_MS;
    const owner = `${deps.id}:${++runs}`, retry = deps.retryMs ?? RETRY_MS;
    // Android may run the task in another JavaScript runtime than the screen: the lock is
    // a file. The foreground sync holds it while it runs: wait within this wake's time,
    // otherwise keep the wake for when that sync finishes (resumeMissed).
    let waited = false;
    while (!await deps.lock.acquire(owner, deps.now() + LOCK_MS)) {
      if (deps.now() + retry >= deadline) { pendingHere = true; missed = true; return 'busy'; }
      waited = true;
      await new Promise(resolve => setTimeout(resolve, retry));
    }
    if (waited && deadline - deps.now() < MIN_RUN_MS) {
      // Freed too late in this wake's time to send anything: resumed at once instead,
      // like a wake that sync kept waiting (the drain loop runs it next, in full).
      await deps.lock.release(owner).catch(() => {});
      pendingHere = true;
      void request('resume');
      return 'busy';
    }
    try { return await run(deadline); } finally { await deps.lock.release(owner).catch(() => {}); }
  }

  async function run(deadline: number): Promise<Outcome> {
    let stored: Stored;
    try { stored = await deps.load(); } catch (e) {
      if (deps.locked(e)) { pendingHere = true; return 'locked'; }
      throw e;
    }
    const { pair, automatic, receipt } = stored;
    if (!pair || !automatic || (receipt?.userId === pair.user_id && receipt.reconnect)) return 'off';
    let state: BackgroundState = stored.state.userId === pair.user_id ? stored.state
      : { ...emptyState(pair.user_id), enabled: stored.state.enabled };
    if (state.blocked) return 'off';
    const now = deps.now();
    if (state.retryAt > now) return 'deferred';
    const previous = receipt?.userId === pair.user_id ? receipt : null;
    if (!await deps.protectedData()) {
      // Locked phone: the HealthKit store is encrypted. The next opening syncs at once.
      pendingHere = true;
      if (previous && !previous.pending) await deps.saveReceipt({ ...previous, pending: true }).catch(() => {});
      return 'locked';
    }
    state = freshHashes(state, now);
    const remaining = () => Math.max(0, deadline - deps.now());
    const within = (b: Required<ReadBudget>) => ({ total: Math.min(b.total, remaining()), perRead: b.perRead });
    const plan = await deps.changes(state.tokens, within(CHANGES));
    if (plan.quota) { await deps.saveState({ ...state, retryAt: now + 3600000 }); return 'deferred'; }
    if (plan.daysBack === 0) { await deps.saveState({ ...state, tokens: plan.tokens }); return 'unchanged'; }

    const skip = hashSkip({ ...state.hashes }, deps.digest);
    // The stored pairing the credentials come from, re-read if the app rotated the token.
    let base: Pair = pair;
    let credentials: Credentials = { token: pair.token, prev_token: pair.prev_token };
    let refused: unknown = null, quota = false, sent = 0, daily: HealthResult | null = null;
    const report = await syncHealth({
      userId: pair.user_id, full: false, previous, trigger: 'background', deadline, clock: deps.now,
      now: () => new Date(deps.now()).toISOString(),
      daily: async () => {
        daily = await deps.readDaily(plan.daysBack, within(DAILY), plan.metrics);
        quota = !!daily.quota;
        return daily;
      },
      sleep: () => deps.readNights(NIGHTS, within(SLEEP)),
      send: async (path, body, meta) => {
        const request = (token: string) => deps.send(path, body, token, Math.max(1000, Math.min(SEND_MS, remaining())), meta.readAt)
          .catch(e => { refused = e; throw e; });
        const keep = async (next: Credentials) => {
          credentials = next;
          await deps.savePair({ ...base, token: next.token, prev_token: next.prev_token });
        };
        try {
          return await authorized(credentials, request, keep);
        } catch (e) {
          // The app may have rotated the token while this run was suspended: once the new
          // token is used, the old one is refused. Retry with the stored one, never unpair.
          if (!(e instanceof ApiError) || e.status !== 401) throw e;
          const latest = (await deps.load().catch(() => null))?.pair;
          if (!latest || latest.user_id !== pair.user_id || latest.token === credentials.token) throw e;
          base = latest; // its rotation date and name, not the ones read when the run started
          credentials = { token: latest.token, prev_token: latest.prev_token };
          return authorized(credentials, request, keep);
        }
      },
      unsent: skip.unsent,
      sent: async (path, body) => { sent++; await skip.sent(path, body); },
      save: async r => { await deps.saveReceipt(r); deps.notify(r); },
      progress: () => {},
    });
    pendingHere = !!report.pending;
    if (report.reconnect) {
      // The token is refused: stop waking the app; Réglages asks to reconnect.
      await deps.disable().catch(() => {});
      await deps.saveState({ ...state, hashes: recent(skip.hashes, now), enabled: false });
      return 'reconnect';
    }
    const retryAt = refused instanceof ApiError && refused.status === 429 ? now + (refused.retryAfter || 900) * 1000
      : quota ? now + 3600000 : 0;
    // Health Connect tokens move on only for the categories read in full and on the
    // server: one cut by a quota refusal or a time limit is announced again next run.
    const read = daily as HealthResult | null; // assigned in the callback above
    const unread = new Set<string>(!read || quota ? Object.keys(plan.tokens)
      : read.unread ?? (read.complete ? [] : Object.keys(plan.tokens)));
    const tokens = skip.dailyDone()
      ? { ...state.tokens, ...Object.fromEntries(Object.entries(plan.tokens).filter(([metric]) => !unread.has(metric))) }
      : state.tokens;
    await deps.saveState({ ...state, hashes: recent(skip.hashes, now), tokens, retryAt });
    return !report.complete ? 'partial' : sent ? 'sent' : 'unchanged';
  }

  return {
    request,
    /** A locked phone or a time limit left work for the next opening. */
    pending: () => pendingHere,
    clearPending: () => { pendingHere = false; },
    /** After the foreground sync released the lock: run a wake it kept waiting. */
    resumeMissed: () => { if (missed) { missed = false; void request('resume'); } },
  };
}
export type Engine = ReturnType<typeof createEngine>;
