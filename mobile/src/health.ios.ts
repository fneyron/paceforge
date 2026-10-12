import { isHealthDataAvailable, requestAuthorization, queryQuantitySamples, queryCategorySamples, queryStatisticsForQuantity } from '@kingstinct/react-native-healthkit';
import * as Crypto from 'expo-crypto';
import { AppleNight, episodes, nightReadings } from './night-samples';
import type { QuantityTypeIdentifier } from '@kingstinct/react-native-healthkit';
import { Daily, HealthResult, SleepResult, Metric, labels, latestWeighIn, valid, windows } from './types';
import { healthReader } from './health-read';

const identifiers: Record<Metric, QuantityTypeIdentifier> = {
  weight: 'HKQuantityTypeIdentifierBodyMass', body_fat: 'HKQuantityTypeIdentifierBodyFatPercentage',
  steps: 'HKQuantityTypeIdentifierStepCount', hr_day: 'HKQuantityTypeIdentifierHeartRate', resp_day: 'HKQuantityTypeIdentifierRespiratoryRate',
};
const units = { weight: 'kg', body_fat: '%', steps: 'count', hr_day: 'count/min', resp_day: 'count/min' };
export async function authorize(): Promise<void> {
  if (!isHealthDataAvailable()) throw new Error('Apple Santé n’est pas disponible sur cet appareil.');
  await requestAuthorization({ toRead: [...Object.values(identifiers), 'HKCategoryTypeIdentifierSleepAnalysis', 'HKQuantityTypeIdentifierHeartRateVariabilitySDNN'] });
}
export async function collect(progress: (s: string) => void, daysBack = 30): Promise<HealthResult> {
  const days: Daily[] = [], errors = new Set<string>();
  const read = healthReader(60000);
  for (const w of windows(new Date(), daysBack)) {
    progress(`Lecture d’Apple Santé · ${w.date}`);
    for (const metric of Object.keys(identifiers) as Metric[]) {
      if (errors.has(labels[metric])) continue;
      try {
        const options = { unit: units[metric], filter: { date: { startDate: w.start, endDate: w.end, strictStartDate: true, strictEndDate: true } } };
        let value: number | undefined, measured_at: string | undefined, sources: string[] = [];
        if (metric === 'weight' || metric === 'body_fat') {
          const samples = await read(() => queryQuantitySamples(identifiers[metric], { ...options, limit: 100, ascending: false }));
          const day = latestWeighIn(metric, w.date, samples.map(s => ({
            value: s.quantity * (metric === 'body_fat' ? 100 : 1), at: s.startDate.toISOString(), source: s.sourceRevision.source.name,
          })));
          if (day) days.push(day);
          else if (samples.length) errors.add(labels[metric]);
          continue;
        } else {
          // HealthKit handles overlapping step sources; never sum raw phone/watch steps.
          const stats = await read(() => queryStatisticsForQuantity(identifiers[metric], [metric === 'steps' ? 'cumulativeSum' : 'discreteAverage'], options));
          value = metric === 'steps' ? stats.sumQuantity?.quantity : stats.averageQuantity?.quantity;
          sources = stats.sources.map(s => s.name);
        }
        const day: Daily = { date: w.date, metric, value: value as number, unit: metric === 'hr_day' ? 'bpm' : metric === 'resp_day' ? 'rpm' : units[metric], sources: [...new Set(sources)].slice(0, 20), measured_at };
        if (valid(day)) days.push(day);
      } catch { errors.add(labels[metric]); }
    }
  }
  const notices = errors.size ? [`Lecture incomplète : ${[...errors].join(', ')}. Vérifie les autorisations puis réessaie.`] : [];
  if (!days.length) notices.push('Aucune mesure quotidienne lisible. Apple ne distingue pas un accès refusé d’une absence de données.');
  return { days, notices, complete: errors.size === 0 };
}

export async function collectSleep(progress: (s: string) => void, daysBack = 60): Promise<SleepResult> {
  const notices: string[] = [];
  const now = new Date(), start = new Date(now); start.setDate(start.getDate()-daysBack-1); start.setHours(0,0,0,0);
  const nights: AppleNight[] = [], errors = new Set<string>();
  const read = healthReader(90000);
  try {
    const samples = await read(() => queryCategorySamples('HKCategoryTypeIdentifierSleepAnalysis', {
      limit: 50000, ascending: false, filter: { date: { startDate: start, endDate: now } },
    }));
    if (samples.length >= 50000) throw new Error('Fenêtre tronquée');
    for (const e of episodes(samples, Intl.DateTimeFormat().resolvedOptions().timeZone, now, daysBack)) {
      progress('Lecture des nuits d’Apple Santé…');
      const packet: AppleNight = { date: e.date, source_id: await Crypto.digestStringAsync(Crypto.CryptoDigestAlgorithm.SHA256, e.key),
        source_name: e.source_name, timezone: e.timezone, sleep: e.sleep, naps: e.naps, hr: [], hrv_sdnn: [], resp: [] };
      if (e.sleep.length) {
        const startDate = new Date(Math.min(...e.sleep.map(s => Date.parse(s.start))));
        const endDate = new Date(Math.max(...e.sleep.map(s => Date.parse(s.end))));
        const metrics = [
          ['hr', 'HKQuantityTypeIdentifierHeartRate', 'count/min', 25, 230, 5000],
          ['hrv_sdnn', 'HKQuantityTypeIdentifierHeartRateVariabilitySDNN', 'ms', 1, 400, 1000],
          ['resp', 'HKQuantityTypeIdentifierRespiratoryRate', 'count/min', 4, 60, 5000],
        ] as const;
        for (const [key, identifier, unit, lo, hi, limit] of metrics) {
          const label = key === 'hrv_sdnn' ? 'VFC nocturne' : key === 'hr' ? 'FC nocturne' : 'respiration nocturne';
          if (errors.has(label)) continue;
          try {
            const readings = await read(() => queryQuantitySamples(identifier, { unit, limit, ascending: true,
              filter: { date: { startDate, endDate, strictStartDate: true, strictEndDate: true } } }));
            if (readings.length >= limit) throw new Error('Fenêtre tronquée');
            packet[key] = nightReadings(readings, e, lo, hi);
          } catch { errors.add(label); }
        }
      }
      nights.push(packet);
    }
  } catch { errors.add('sommeil'); }
  if (errors.size) notices.push(`Lecture incomplète : ${[...errors].join(', ')}. Vérifie les autorisations.`);
  return { nights, notices, complete: errors.size === 0 };
}
