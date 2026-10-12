import { initialize, requestPermission, getGrantedPermissions, aggregateRecord, readRecords } from 'react-native-health-connect';
import type { Permission, RecordType } from 'react-native-health-connect';
import { Daily, HealthResult, SleepResult, Metric, labels, latestWeighIn, valid, windows } from './types';
import { healthReader } from './health-read';
const records: Record<Metric, RecordType> = { weight: 'Weight', body_fat: 'BodyFat', steps: 'Steps', hr_day: 'HeartRate', resp_day: 'RespiratoryRate' };
export async function authorize(): Promise<void> {
  if (!await initialize()) throw new Error('Installe ou mets à jour Health Connect sur ce téléphone.');
  await requestPermission(Object.values(records).map(recordType => ({ accessType: 'read', recordType } as Permission)));
}
export async function collect(progress: (s: string) => void, daysBack = 30): Promise<HealthResult> {
  const read = healthReader(60000);
  if (!await read(() => initialize())) throw new Error('Health Connect n’est pas disponible.');
  const granted = await read(() => getGrantedPermissions());
  const allowed = new Set(granted.flatMap(p => 'recordType' in p && p.accessType === 'read' ? [p.recordType] : []));
  const days: Daily[] = [], errors = new Set<string>();
  for (const w of windows(new Date(), daysBack)) {
    progress(`Lecture de Health Connect · ${w.date}`);
    const timeRangeFilter = { operator: 'between' as const, startTime: w.start.toISOString(), endTime: w.end.toISOString() };
    for (const metric of Object.keys(records) as Metric[]) {
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
      } catch { errors.add(labels[metric]); }
    }
  }
  const notices = errors.size ? [`Lecture incomplète : ${[...errors].join(', ')}. Réessaie après avoir vérifié Health Connect.`] : [];
  const refused = (Object.keys(records) as Metric[]).filter(m => !allowed.has(records[m]));
  if (refused.length) notices.push(`Non autorisé : ${refused.map(m => labels[m]).join(', ')}.`);
  if (!days.length) notices.push('Aucune mesure lisible. Vérifie que tes applications alimentent Health Connect.');
  return { days, notices, complete: errors.size === 0 };
}
export async function collectSleep(_progress: (s: string) => void, _daysBack = 60): Promise<SleepResult> {
  return { nights: [], notices: [], complete: true };
}
