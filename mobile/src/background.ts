// Imported by index.ts before registerRootComponent: a background launch runs this
// module without any screen. Never import React, App or the WebView here, and
// never log health values.
import { Platform } from 'react-native';
import * as TaskManager from 'expo-task-manager';
import * as BackgroundTask from 'expo-background-task';
import * as Crypto from 'expo-crypto';
import { File, Paths } from 'expo-file-system';
import { api } from './api';
import { changedMetrics, collect, collectSleep, disableHealthBackground, enableHealthBackground, hasBackgroundAccess,
  protectedDataAvailable, watchHealthChanges } from './health';
import { isLockedError, KEYS, readNow, secure } from './secure';
import { Block, BackgroundState, createEngine, emptyState, fileLock, LOCK_MS, progressOf, readPair, readReceipt, readState,
  TASK_INTERVAL_MINUTES, TASK_NAME } from './background-engine';
import type { SyncReport } from './sync';

const id = Array.from(Crypto.getRandomBytes(8), b => b.toString(16).padStart(2, '0')).join('');
// The file is created on use: nothing native may throw while this module is evaluated at launch.
export const syncLock = fileLock(() => new File(Paths.document, 'paceforge-sync.lock'));

// From the stored state, readable on a locked phone; null (unreadable) runs the engine anyway.
let enabledHint: boolean | null = (() => {
  const raw = readNow(KEYS.background);
  if (raw === undefined) return null;
  const state = readState(raw);
  return state.enabled && !state.blocked;
})();
async function loadState(): Promise<BackgroundState> { return readState(await secure.get(KEYS.background)); }
async function saveState(state: BackgroundState) {
  await secure.set(KEYS.background, JSON.stringify(state));
  enabledHint = state.enabled && !state.blocked;
}
// User decisions and native registration are ordered. A slow registration cannot
// finish after a newer logout and enable delivery again.
let controlQueue: Promise<unknown> = Promise.resolve(), controlGeneration = 0;
function control<T>(work: () => Promise<T>): Promise<T> {
  const job = controlQueue.then(work);
  controlQueue = job.catch(() => {});
  return job;
}
const generation = () => `${id}:${++controlGeneration}`;
const listeners = new Set<(report: SyncReport) => void>();

const engine = createEngine({
  id, now: Date.now,
  load: async () => {
    await controlQueue;
    await secure.settled();
    const [rawPair, auto, receipt, rawState, rawProgress] = await Promise.all([secure.recover(KEYS.pair), secure.get(KEYS.auto),
      secure.get(KEYS.sync), secure.get(KEYS.background), secure.get(KEYS.progress)]);
    const pair = readPair(rawPair), state = readState(rawState), progress = readState(rawProgress);
    // Existing installations kept progress in the control entry. It is read until
    // the first separate progress write; an old account/generation is never merged.
    if (state.userId === null && pair) state.userId = pair.user_id;
    if (rawProgress && progress.userId === state.userId && progress.generation === state.generation) Object.assign(state, progressOf(progress));
    return { pair, automatic: auto === 'true', receipt: readReceipt(receipt), state };
  },
  saveProgress: progress => secure.set(KEYS.progress, JSON.stringify(progress)),
  saveReceipt: async report => {
    await secure.set(KEYS.sync, JSON.stringify(report));
    if (report.lastReceivedAt) await secure.set(KEYS.last, report.lastReceivedAt);
  },
  savePair: pair => secure.rewrite(KEYS.pair, JSON.stringify(pair)),
  lock: syncLock,
  protectedData: protectedDataAvailable,
  changes: changedMetrics,
  readDaily: (daysBack, budget, metrics) => collect(() => {}, daysBack, budget, metrics),
  readNights: (daysBack, budget) => collectSleep(() => {}, daysBack, budget),
  send: (path, body, token, timeout, readAt) => api(path, 'POST', body, token, { timeout, headers: { 'X-PaceForge-Read-At': readAt } }),
  digest: value => Crypto.digestStringAsync(Crypto.CryptoDigestAlgorithm.SHA256, value),
  disable: () => disableNatively(),
  notify: report => listeners.forEach(listener => listener(report)),
  locked: isLockedError,
});

// Must be defined in the global scope: the task can start this bundle without any screen.
TaskManager.defineTask(TASK_NAME, async () => {
  const outcome = await engine.request('task');
  return outcome === 'failed' ? BackgroundTask.BackgroundTaskResult.Failed : BackgroundTask.BackgroundTaskResult.Success;
});

let unwatch = () => {};
function watch() {
  try {
    unwatch();
    unwatch = watchHealthChanges(() => { if (enabledHint !== false) void engine.request('healthkit'); });
  } catch { unwatch = () => {}; } // no HealthKit here (some iPads): the app must still start
}
// iOS: at the top level, before the first render. HealthKit queues the changes that
// woke the app until this subscription; there is no headless JavaScript on iOS.
if (Platform.OS === 'ios') watch();

/** In the foreground, once the account and the toggle allow it. iOS registers the
 * types after the HealthKit permission sheet; Android needs the background permission. */
export async function enableBackgroundSync(): Promise<boolean> {
  return control(async () => {
    if (!await hasBackgroundAccess()) return false;
    const state = await loadState();
    if (state.blocked) return false;
    if (Platform.OS === 'ios') {
      await enableHealthBackground();
      watch(); // through the native background observers now registered
    }
    if (await BackgroundTask.getStatusAsync() === BackgroundTask.BackgroundTaskStatus.Available) {
      // Retries and catch-up: iOS BGProcessingTask (network required), Android WorkManager.
      await BackgroundTask.registerTaskAsync(TASK_NAME, { minimumInterval: TASK_INTERVAL_MINUTES });
    }
    await saveState({ ...state, enabled: true });
    return true;
  });
}
async function disableNatively() {
  await Promise.allSettled([
    Platform.OS === 'ios' ? disableHealthBackground() : Promise.resolve(),
    BackgroundTask.unregisterTaskAsync(TASK_NAME),
  ]);
  enabledHint = false;
}
/** Toggle off, disconnection, another account, web logout, refused token. */
export async function disableBackgroundSync(): Promise<void> {
  engine.cancel();
  await control(async () => {
    const state = await loadState();
    if (state.enabled) await saveState({ ...state, enabled: false, generation: generation() });
    await disableNatively();
  });
}
export async function blockBackgroundSync(reason: Block): Promise<void> {
  engine.cancel();
  await control(async () => {
    const state = await loadState();
    if (state.blocked !== reason) await saveState({ ...state, blocked: reason, generation: generation() });
  });
}
export async function backgroundBlock(): Promise<Block> { await controlQueue; return (await loadState()).blocked; }
/** A new pairing starts a new background state (hashes, change tokens, block). */
export async function resetBackgroundState(userId: number): Promise<void> {
  engine.cancel();
  await control(async () => {
    const state = await loadState();
    await saveState({ ...emptyState(userId), enabled: state.enabled, generation: generation() });
  });
}
export const cancelBackgroundSync = () => engine.cancel();

/** The foreground sync takes the same lock, waiting for a background run (18 s at most). */
export async function syncExclusively<T>(work: () => Promise<T>): Promise<T> {
  const owner = `${id}:foreground:${Date.now()}`, until = Date.now() + 25000;
  while (!await syncLock.acquire(owner, Date.now() + LOCK_MS)) {
    if (Date.now() >= until) throw new Error('Une synchronisation est encore en cours. Réessaie dans quelques instants.');
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  // Held for its real duration: renewed while the sync runs, gone 60 s after a crash.
  const renew = setInterval(() => { void syncLock.acquire(owner, Date.now() + LOCK_MS); }, LOCK_MS / 3);
  try { return await work(); } finally {
    clearInterval(renew);
    engine.clearPending();
    await syncLock.release(owner);
    // A HealthKit wake that found the lock taken past its window runs now.
    engine.resumeMissed();
  }
}
export const backgroundPending = () => engine.pending();
/** Receipts written by a background run of this runtime (iOS: the same process as the screen). */
export function onBackgroundReport(listener: (report: SyncReport) => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
