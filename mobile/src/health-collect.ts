import type { CategoryTypeIdentifier, QuantityTypeIdentifier } from '@kingstinct/react-native-healthkit';
import { AppleNight, episodes, nightReadings } from './night-samples';
import { Daily, HealthResult, SleepResult, Metric, labels, latestWeighIn, valid, windows } from './types';
import { healthReader, readIssue } from './health-read';

export type HealthQueries = Pick<typeof import('@kingstinct/react-native-healthkit'),
  'queryQuantitySamples' | 'queryCategorySamples' | 'queryStatisticsForQuantity'> & {
  digest: (key: string) => Promise<string>; now?: () => Date; reader?: ReturnType<typeof healthReader>;
  prepareRead?: (identifier: QuantityTypeIdentifier | CategoryTypeIdentifier) => Promise<void>;
};

function collectionReader(d: HealthQueries, budget: number) {
  const read = d.reader || healthReader(budget);
  const prepared = new Set<string>();
  return async <T>(identifier: QuantityTypeIdentifier | CategoryTypeIdentifier, query: () => Promise<T>) => {
    if (!prepared.has(identifier)) {
      if (d.prepareRead) await read(() => d.prepareRead!(identifier));
      prepared.add(identifier);
    }
    return read(query);
  };
}

export const identifiers: Record<Metric, QuantityTypeIdentifier> = {
  weight: 'HKQuantityTypeIdentifierBodyMass', body_fat: 'HKQuantityTypeIdentifierBodyFatPercentage',
  steps: 'HKQuantityTypeIdentifierStepCount', hr_day: 'HKQuantityTypeIdentifierHeartRate', resp_day: 'HKQuantityTypeIdentifierRespiratoryRate',
};
const units = { weight: 'kg', body_fat: '%', steps: 'count', hr_day: 'count/min', resp_day: 'count/min' };
export async function collectDaily(d: HealthQueries, progress: (s: string) => void, daysBack = 30): Promise<HealthResult> {
  const days: Daily[] = [], errors = new Map<string, string>();
  const read = collectionReader(d, 60000);
  const { queryQuantitySamples, queryStatisticsForQuantity } = d;
  for (const w of windows(d.now?.() || new Date(), daysBack)) {
    progress(`Lecture d’Apple Santé · ${w.date}`);
    // One day's five independent categories in parallel, newest day first.
    // Keep a bound on concurrent queries instead of launching the full history.
    await Promise.all((Object.keys(identifiers) as Metric[]).map(async metric => {
      if (errors.has(labels[metric])) return;
      try {
        const options = { unit: units[metric], filter: { date: { startDate: w.start, endDate: w.end, strictStartDate: true, strictEndDate: true } } };
        let value: number | undefined, measured_at: string | undefined, sources: string[] = [];
        if (metric === 'weight' || metric === 'body_fat') {
          const samples = await read(identifiers[metric], () => queryQuantitySamples(identifiers[metric], { ...options, limit: 100, ascending: false }));
          const day = latestWeighIn(metric, w.date, samples.map(s => ({
            value: s.quantity * (metric === 'body_fat' ? 100 : 1), at: s.startDate.toISOString(), source: s.sourceRevision.source.name,
          })));
          if (day) days.push(day);
          else if (samples.length) errors.set(labels[metric], `${labels[metric]} : les relevés reçus ne sont pas exploitables.`);
          return;
        } else {
          // HealthKit handles overlapping step sources; never sum raw phone/watch steps.
          const stats = await read(identifiers[metric], () => queryStatisticsForQuantity(identifiers[metric], [metric === 'steps' ? 'cumulativeSum' : 'discreteAverage'], options));
          value = metric === 'steps' ? stats.sumQuantity?.quantity : stats.averageQuantity?.quantity;
          // HealthKit quantities are doubles, including prorated step totals.
          // The import contract expects a count; a fraction must not reject the
          // entire daily batch (including otherwise valid weight measurements).
          if (metric === 'steps' && value !== undefined && Number.isFinite(value)) value = Math.round(value);
          sources = stats.sources.map(s => s.name);
        }
        const day: Daily = { date: w.date, metric, value: value as number, unit: metric === 'hr_day' ? 'bpm' : metric === 'resp_day' ? 'rpm' : units[metric], sources: [...new Set(sources)].slice(0, 20), measured_at };
        if (valid(day)) days.push(day);
      } catch (error) { errors.set(labels[metric], readIssue(labels[metric], error)); }
    }));
  }
  const notices = [...errors.values()];
  if (!days.length) notices.push('Aucune mesure quotidienne lisible. Apple ne distingue pas un accès refusé d’une absence de données.');
  return { days, notices, complete: errors.size === 0 };
}

export async function collectNights(d: HealthQueries, progress: (s: string) => void, daysBack = 60): Promise<SleepResult> {
  const notices: string[] = [];
  const now = d.now?.() || new Date(), start = new Date(now); start.setDate(start.getDate()-daysBack-1); start.setHours(0,0,0,0);
  const nights: AppleNight[] = [], errors = new Map<string, string>();
  const read = collectionReader(d, 90000);
  const { queryCategorySamples, queryQuantitySamples } = d;
  let processing = false;
  try {
    const samples = await read('HKCategoryTypeIdentifierSleepAnalysis', () => queryCategorySamples('HKCategoryTypeIdentifierSleepAnalysis', {
      limit: 50000, ascending: false, filter: { date: { startDate: start, endDate: now } },
    }));
    if (samples.length >= 50000) return { nights, complete: false, notices: ['Sommeil : historique trop volumineux pour cette lecture. Aucune nuit n’a été remplacée.'] };
    processing = true;
    for (const e of episodes(samples, Intl.DateTimeFormat().resolvedOptions().timeZone, now, daysBack)) {
      progress('Lecture des nuits d’Apple Santé…');
      const packet: AppleNight = { date: e.date, source_id: await d.digest(e.key),
        source_name: e.source_name, timezone: e.timezone, sleep: e.sleep, naps: e.naps, hr: [], hrv_sdnn: [], resp: [] };
      if (e.sleep.length) {
        const startDate = new Date(Math.min(...e.sleep.map(s => Date.parse(s.start))));
        const endDate = new Date(Math.max(...e.sleep.map(s => Date.parse(s.end))));
        const metrics = [
          ['hr', 'HKQuantityTypeIdentifierHeartRate', 'count/min', 25, 230, 5000],
          ['hrv_sdnn', 'HKQuantityTypeIdentifierHeartRateVariabilitySDNN', 'ms', 1, 400, 1000],
          ['resp', 'HKQuantityTypeIdentifierRespiratoryRate', 'count/min', 4, 60, 5000],
        ] as const;
        await Promise.all(metrics.map(async ([key, identifier, unit, lo, hi, limit]) => {
          const label = key === 'hrv_sdnn' ? 'VFC nocturne' : key === 'hr' ? 'FC nocturne' : 'respiration nocturne';
          if (errors.has(label)) return;
          try {
            const readings = await read(identifier, () => queryQuantitySamples(identifier, { unit, limit, ascending: true,
              filter: { date: { startDate, endDate, strictStartDate: true, strictEndDate: true } } }));
            if (readings.length >= limit) throw new Error('Fenêtre tronquée');
            packet[key] = nightReadings(readings, e, lo, hi);
          } catch (error) { errors.set(label, readIssue(label, error)); }
        }));
      }
      nights.push(packet);
    }
  } catch (error) {
    errors.set('Sommeil', processing
      ? 'Sommeil : les relevés ont été lus, mais leur traitement a échoué. Vérifie que PaceForge est à jour.'
      : readIssue('Sommeil', error));
  }
  notices.push(...errors.values());
  return { nights, notices, complete: errors.size === 0 };
}
