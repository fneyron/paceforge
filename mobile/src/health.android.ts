import { initialize, requestPermission, getGrantedPermissions, aggregateRecord, readRecords, getChanges } from 'react-native-health-connect';
import type { Permission, RecordType } from 'react-native-health-connect';
import { ChangePlan, Daily, HealthResult, ReadBudget, SleepResult, Metric, labels, latestWeighIn, valid, windows } from './types';
import { healthReader } from './health-read';
const records: Record<Metric, RecordType> = { weight: 'Weight', body_fat: 'BodyFat', steps: 'Steps', hr_day: 'HeartRate', resp_day: 'RespiratoryRate' };
export const backgroundTypes: readonly string[] = Object.values(records);
// Background reads have a smaller Health Connect quota: once refused, stop until the next run.
const quotaError = (e: unknown) => /quota|rate.?limit/i.test(e instanceof Error ? e.message : '');
// Health Connect absent or too old: the message carries the store page (the alert makes it tappable).
const STORE = 'https://play.google.com/store/apps/details?id=com.google.android.apps.healthdata';
const unavailable = () => new Error(`Installe ou mets à jour Health Connect sur ce téléphone : ${STORE}`);
type Read = ReturnType<typeof healthReader>;
async function readable(read: Read) {
  const granted = await read(() => getGrantedPermissions());
  return new Set(granted.flatMap(p => 'recordType' in p && p.accessType === 'read' ? [p.recordType as string] : []));
}
/** `background` also asks READ_HEALTH_DATA_IN_BACKGROUND (automatic updates). */
export async function authorize(background = false): Promise<void> {
  if (!await initialize()) throw unavailable();
  const permissions = Object.values(records).map(recordType => ({ accessType: 'read', recordType } as Permission));
  await requestPermission(background ? [...permissions, { accessType: 'read', recordType: 'BackgroundAccessPermission' }] : permissions);
}
/** Health Connect has no « never asked » state: a permission not granted (background reading included) is
 * offered again by the « Synchroniser » button only, never by an automatic sync. */
export async function authorizationNeeded(manual = false): Promise<boolean> {
  if (!manual) return false;
  try {
    if (!await initialize()) return false;
    const granted = await readable(healthReader(10000));
    return [...Object.values(records), 'BackgroundAccessPermission'].some(type => !granted.has(type));
  } catch { return false; }
}
/** No feature check in this library version: the grant itself is the answer. */
export async function hasBackgroundAccess(): Promise<boolean> {
  try {
    if (!await initialize()) return false;
    return (await readable(healthReader(10000))).has('BackgroundAccessPermission');
  } catch { return false; }
}
export async function collect(progress: (s: string) => void, daysBack = 30, budget: ReadBudget = {}, metrics?: Metric[]): Promise<HealthResult> {
  const read = healthReader(budget.total ?? 60000, budget.perRead);
  if (!await read(() => initialize())) throw unavailable();
  const allowed = await readable(read);
  const wanted = metrics?.length ? metrics : Object.keys(records) as Metric[];
  const days: Daily[] = [], errors = new Set<string>();
  let quota = false;
  for (const w of windows(new Date(), daysBack)) {
    progress(`Lecture de Health Connect · ${w.date}`);
    const timeRangeFilter = { operator: 'between' as const, startTime: w.start.toISOString(), endTime: w.end.toISOString() };
    for (const metric of wanted) {
      if (!allowed.has(records[metric]) || errors.has(labels[metric])) continue;
      try {
        let value: number | undefined, measured_at: string | undefined, sources: string[] = [];
        if (metric === 'steps') {
          const result = await read(() => aggregateRecord({ recordType: 'Steps', timeRangeFilter }));
          value = result.COUNT_TOTAL; sources = result.dataOrigins;
        } else if (metric === 'hr_day') {
          const result = await read(() => aggregateRecord({ recordType: 'HeartRate', timeRangeFilter }));
          value = result.MEASUREMENTS_COUNT > 0 ? result.BPM_AVG : undefined; sources = result.dataOrigins;
        } else if (metric === 'weight' || metric === 'body_fat') {
          let pageToken: string | undefined, found: Daily | undefined, hadRecords = false;
          do {
            const options = { timeRangeFilter, ascendingOrder: false, pageSize: 100, pageToken };
            const result = metric === 'weight' ? await read(() => readRecords('Weight', options)) : await read(() => readRecords('BodyFat', options));
            hadRecords ||= !!result.records.length;
            found = latestWeighIn(metric, w.date, result.records.map(r => ({
              value: 'weight' in r ? r.weight.inKilograms : r.percentage,
              at: r.time, source: r.metadata?.dataOrigin ?? 'Health Connect',
            })));
            pageToken = result.pageToken;
          } while (!found && pageToken);
          if (found) days.push(found);
          else if (hadRecords) errors.add(labels[metric]);
          continue;
        } else {
          let pageToken: string | undefined, total = 0, n = 0;
          do {
            const result = await read(() => readRecords('RespiratoryRate', { timeRangeFilter, pageSize: 1000, pageToken }));
            for (const r of result.records) { total += r.rate; n++; sources.push(r.metadata?.dataOrigin ?? 'Health Connect'); }
            pageToken = result.pageToken;
          } while (pageToken);
          value = n ? total / n : undefined;
        }
        const day: Daily = { date: w.date, metric, value: value as number, unit: { weight: 'kg', body_fat: '%', steps: 'count', hr_day: 'bpm', resp_day: 'rpm' }[metric], sources: [...new Set(sources)].slice(0, 20), measured_at };
        if (valid(day)) days.push(day);
      } catch (e) {
        errors.add(labels[metric]);
        if (quotaError(e)) { quota = true; break; }
      }
    }
    if (quota) break;
  }
  const notices = errors.size ? [`Lecture incomplète : ${[...errors].join(', ')}. Réessaie après avoir vérifié Health Connect.`] : [];
  const refused = (Object.keys(records) as Metric[]).filter(m => !allowed.has(records[m]));
  if (refused.length) notices.push(`Non autorisé : ${refused.map(m => labels[m]).join(', ')}.`);
  if (!days.length) notices.push('Aucune mesure lisible. Vérifie que tes applications alimentent Health Connect.');
  // Categories not read in full keep their change token (background.ts reads them again).
  const unread = quota ? wanted : wanted.filter(m => errors.has(labels[m]));
  return { days, notices, complete: errors.size === 0, unread, ...(quota ? { quota } : {}) };
}
export async function collectSleep(_progress: (s: string) => void, _daysBack = 60, _budget?: ReadBudget): Promise<SleepResult> {
  return { nights: [], notices: [], complete: true };
}

/** Which categories changed since the stored change tokens (one per category, keyed by
 * metric). A first token reads the last 3 days; an expired one (unused for 30 days) a week. */
export async function changedMetrics(tokens: Record<string, string>, budget: ReadBudget = {}): Promise<ChangePlan> {
  const read = healthReader(budget.total ?? 4000, budget.perRead ?? 3000);
  const next = { ...tokens }, changed = new Set<Metric>();
  let daysBack = 3;
  try {
    if (!await read(() => initialize())) return { daysBack: 0, tokens };
    const allowed = await readable(read);
    for (const metric of Object.keys(records) as Metric[]) {
      const recordType = records[metric];
      if (!allowed.has(recordType)) continue;
      let token = tokens[metric];
      if (!token) {
        next[metric] = (await read(() => getChanges({ recordTypes: [recordType] }))).nextChangesToken;
        changed.add(metric);
        continue;
      }
      for (let page = 0; page < 20; page++) {
        const r = await read(() => getChanges({ changesToken: token }));
        if (r.changesTokenExpired) {
          daysBack = 7; changed.add(metric);
          next[metric] = (await read(() => getChanges({ recordTypes: [recordType] }))).nextChangesToken;
          break;
        }
        if (r.upsertionChanges.length || r.deletionChanges.length) changed.add(metric);
        token = next[metric] = r.nextChangesToken;
        if (!r.hasMore) break;
      }
    }
  } catch (e) {
    if (quotaError(e)) return { daysBack: 0, tokens, quota: true };
    // Unknown state: read the short window of every category, keep the old tokens.
    return { daysBack: 3, tokens };
  }
  return { daysBack: changed.size ? daysBack : 0, metrics: [...changed], tokens: next };
}
export async function protectedDataAvailable(): Promise<boolean> { return true; }
// WorkManager (expo-background-task) runs the periodic read; there is no observer.
export async function enableHealthBackground(): Promise<void> {}
export async function disableHealthBackground(): Promise<void> {}
export function watchHealthChanges(_onChange: () => void, _onError?: (type: string, message: string) => void): () => void { return () => {}; }
