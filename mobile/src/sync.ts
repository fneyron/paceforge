import { ApiError } from './api';
import type { HealthResult, SleepResult } from './types';

export type WeightReceipt = { value: number; measured_at: string };
export type SyncReport = {
  userId: number; checkedAt: string; lastReceivedAt: string; fullAt: string;
  complete: boolean; reconnect: boolean; retryFull: boolean; received: number; notices: string[];
  weightRead: WeightReceipt | null; weightReceived: WeightReceipt | null;
};
type Receipt = { received: number; checked_at: string; latest?: { weight?: WeightReceipt } };
type Dependencies = {
  userId: number; full: boolean; previous: SyncReport | null;
  daily: () => Promise<HealthResult>; sleep: () => Promise<SleepResult>;
  send: (path: string, body: unknown) => Promise<unknown>;
  save: (report: SyncReport) => Promise<void>; progress: (text: string) => void;
  now?: () => string;
};

function receipt(raw: unknown): Receipt {
  const r = raw as Receipt | null;
  if (!r || !Number.isSafeInteger(r.received) || r.received < 0 || !Number.isFinite(Date.parse(r.checked_at))) {
    throw new Error('Le serveur n’a pas confirmé la réception. Réessaie la synchronisation.');
  }
  return r;
}
function weight(raw: WeightReceipt | undefined): WeightReceipt | null {
  return raw && Number.isFinite(raw.value) && raw.value >= 25 && raw.value <= 300 &&
    Number.isFinite(Date.parse(raw.measured_at)) ? { value: raw.value, measured_at: raw.measured_at } : null;
}

// Short overlapping reads catch late writes. Full history is reread at least
// every six hours and on every manual sync. Partial attempts never defer a retry
// for six hours; neither empty reads nor HTTP success imply a fresh measurement.
export function due(report: SyncReport | null, lastAttempt: number, now = Date.now()): boolean {
  const stamp = Math.max(Date.parse(report?.checkedAt || '') || 0, lastAttempt);
  return stamp > now + 300000 || now - stamp >= (report?.complete ? 15 : 5) * 60000;
}
export function fullDue(report: SyncReport | null, now = Date.now()): boolean {
  const stamp = Date.parse(report?.fullAt || '') || 0;
  return !!report?.retryFull || stamp > now + 300000 || now - stamp >= 6 * 3600000;
}

export async function syncHealth(d: Dependencies): Promise<SyncReport> {
  const now = d.now || (() => new Date().toISOString());
  const previous = d.previous?.userId === d.userId ? d.previous : null;
  const report: SyncReport = { userId: d.userId, checkedAt: now(), complete: false, reconnect: false, retryFull: d.full || !!previous?.retryFull,
    lastReceivedAt: previous?.lastReceivedAt || '', fullAt: previous?.fullAt || '', received: 0, notices: [],
    weightRead: null, weightReceived: previous?.weightReceived || null };
  let complete = true;
  const save = async () => { report.checkedAt = now(); await d.save({ ...report, notices: [...report.notices] }); };
  const failed = (e: unknown, stage: string) => {
    complete = false;
    report.reconnect = e instanceof ApiError && e.status === 401;
    report.notices.unshift(`${stage} : ${e instanceof Error ? e.message : 'Synchronisation interrompue. Réessaie.'}`);
  };
  const confirm = (r: Receipt) => {
    report.received += r.received;
    if (r.received) report.lastReceivedAt = r.checked_at;
  };
  // Persist "incomplete" first, including if iOS suspends/kills us during a read.
  await save();
  let dailyStage = 'Lecture des mesures quotidiennes';
  try {
    const result = await d.daily();
    complete &&= result.complete;
    if (result.complete) report.notices.push(...result.notices);
    else report.notices.unshift(...result.notices);
    const latest = result.days.filter(x => x.metric === 'weight' && x.measured_at)
      .sort((a, b) => Date.parse(b.measured_at!) - Date.parse(a.measured_at!))[0];
    report.weightRead = weight(latest as WeightReceipt | undefined);
    dailyStage = 'Envoi des mesures quotidiennes';
    d.progress('Envoi des mesures quotidiennes…');
    const r = receipt(await d.send('/api/mobile/daily', { days: result.days }));
    confirm(r);
    // Missing metadata (e.g. an older server) must not turn a local read into
    // proof of server receipt. Only the explicit server snapshot is shown.
    if (r.latest) report.weightReceived = weight(r.latest.weight);
    await save();
  } catch (e) {
    failed(e, dailyStage); await save();
    // Authentication/transport failures cannot be repaired by reading 60 nights.
    report.notices.push('Les nuits restent à synchroniser.');
    await save(); return report;
  }
  try {
    const result = await d.sleep();
    complete &&= result.complete;
    if (result.complete) report.notices.push(...result.notices);
    else report.notices.unshift(...result.notices);
    for (const [i, night] of result.nights.entries()) {
      d.progress(`Envoi des nuits · ${i + 1}/${result.nights.length}…`);
      try {
        confirm(receipt(await d.send('/api/mobile/nights', night)));
      } catch (e) {
        failed(e, 'Envoi du sommeil');
        // One malformed night must not block other dates. A network/account
        // failure stops the queue; the next overlapping read safely retries it.
        if (!(e instanceof ApiError) || e.status !== 422) break;
      }
    }
  } catch (e) { failed(e, 'Lecture du sommeil'); }
  report.complete = complete;
  if (complete && d.full) { report.fullAt = now(); report.retryFull = false; }
  report.notices = [...new Set(report.notices)];
  await save();
  return report;
}

export function reportText(r: SyncReport): string {
  const title = !r.complete ? 'Synchronisation incomplète. Les mesures déjà reçues sont conservées.' :
    r.received ? 'Synchronisation terminée.' : 'Aucune nouvelle mesure reçue.';
  return [title, ...r.notices].join('\n');
}

export function syncIssue(r: SyncReport, service: string): string {
  if (r.complete) return '';
  if (r.reconnect) return 'Reconnecte le téléphone dans Réglages.';
  return `${service} · ${r.notices[0] || 'Synchronisation interrompue. Réessaie.'}`;
}
