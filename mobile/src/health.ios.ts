import { isHealthDataAvailable, requestAuthorization, queryQuantitySamples, queryStatisticsForQuantity } from '@kingstinct/react-native-healthkit';
import type { QuantityTypeIdentifier } from '@kingstinct/react-native-healthkit';
import { Daily, HealthResult, Metric, labels, valid, windows } from './types';

const identifiers: Record<Metric, QuantityTypeIdentifier> = {
  weight: 'HKQuantityTypeIdentifierBodyMass', body_fat: 'HKQuantityTypeIdentifierBodyFatPercentage',
  steps: 'HKQuantityTypeIdentifierStepCount', hr_day: 'HKQuantityTypeIdentifierHeartRate', resp_day: 'HKQuantityTypeIdentifierRespiratoryRate',
};
const units = { weight: 'kg', body_fat: '%', steps: 'count', hr_day: 'count/min', resp_day: 'count/min' };
export async function authorize(): Promise<void> {
  if (!isHealthDataAvailable()) throw new Error('Apple Santé n’est pas disponible sur cet appareil.');
  await requestAuthorization({ toRead: Object.values(identifiers) });
}
export async function collect(progress: (s: string) => void): Promise<HealthResult> {
  const days: Daily[] = [], errors = new Set<string>();
  for (const w of windows()) {
    progress(`Lecture d’Apple Santé · ${w.date}`);
    for (const metric of Object.keys(identifiers) as Metric[]) {
      try {
        const options = { unit: units[metric], filter: { date: { startDate: w.start, endDate: w.end, strictStartDate: true, strictEndDate: true } } };
        let value: number | undefined, measured_at: string | undefined, sources: string[] = [];
        if (metric === 'weight' || metric === 'body_fat') {
          const samples = await queryQuantitySamples(identifiers[metric], { ...options, limit: 1, ascending: false });
          const sample = samples[0];
          if (sample) { value = sample.quantity; measured_at = sample.startDate.toISOString(); sources = [sample.sourceRevision.source.name]; }
        } else {
          // HealthKit handles overlapping step sources; never sum raw phone/watch steps.
          const stats = await queryStatisticsForQuantity(identifiers[metric], [metric === 'steps' ? 'cumulativeSum' : 'discreteAverage'], options);
          value = metric === 'steps' ? stats.sumQuantity?.quantity : stats.averageQuantity?.quantity;
          sources = stats.sources.map(s => s.name);
        }
        // HealthKit's % unit returns a fraction (0.20 means 20%).
        if (metric === 'body_fat' && value !== undefined) value *= 100;
        const day: Daily = { date: w.date, metric, value: value as number, unit: metric === 'hr_day' ? 'bpm' : metric === 'resp_day' ? 'rpm' : units[metric], sources: [...new Set(sources)].slice(0, 20), measured_at };
        if (valid(day)) days.push(day);
      } catch { errors.add(labels[metric]); }
    }
  }
  const notices = errors.size ? [`Lecture incomplète : ${[...errors].join(', ')}. Vérifie les autorisations puis réessaie.`] : [];
  if (!days.length) notices.push('Aucune mesure lisible sur 30 jours. Apple ne permet pas de distinguer une autorisation refusée d’une absence de données.');
  return { days, notices };
}
