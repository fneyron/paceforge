import { initialize, requestPermission, getGrantedPermissions, aggregateRecord, readRecords } from 'react-native-health-connect';
import type { Permission, RecordType } from 'react-native-health-connect';
import { Daily, HealthResult, Metric, labels, valid, windows } from './types';
const records: Record<Metric, RecordType> = { weight: 'Weight', body_fat: 'BodyFat', steps: 'Steps', hr_day: 'HeartRate', resp_day: 'RespiratoryRate' };
export async function authorize(): Promise<void> {
  if (!await initialize()) throw new Error('Installe ou mets à jour Health Connect sur ce téléphone.');
  await requestPermission(Object.values(records).map(recordType => ({ accessType: 'read', recordType } as Permission)));
}
export async function collect(progress: (s: string) => void): Promise<HealthResult> {
  if (!await initialize()) throw new Error('Health Connect n’est pas disponible.');
  const granted = await getGrantedPermissions();
  const allowed = new Set(granted.flatMap(p => 'recordType' in p && p.accessType === 'read' ? [p.recordType] : []));
  const days: Daily[] = [], errors = new Set<string>();
  for (const w of windows()) {
    progress(`Lecture de Health Connect · ${w.date}`);
    const timeRangeFilter = { operator: 'between' as const, startTime: w.start.toISOString(), endTime: w.end.toISOString() };
    for (const metric of Object.keys(records) as Metric[]) {
      if (!allowed.has(records[metric])) continue;
      try {
        let value: number | undefined, measured_at: string | undefined, sources: string[] = [];
        if (metric === 'steps') {
          const result = await aggregateRecord({ recordType: 'Steps', timeRangeFilter });
          value = result.COUNT_TOTAL; sources = result.dataOrigins;
        } else if (metric === 'hr_day') {
          const result = await aggregateRecord({ recordType: 'HeartRate', timeRangeFilter });
          value = result.MEASUREMENTS_COUNT > 0 ? result.BPM_AVG : undefined; sources = result.dataOrigins;
        } else if (metric === 'weight') {
          const result = await readRecords('Weight', { timeRangeFilter, ascendingOrder: false, pageSize: 1 });
          const last = result.records[0];
          if (last) { value = last.weight.inKilograms; measured_at = last.time; sources = [last.metadata?.dataOrigin ?? 'Health Connect']; }
        } else if (metric === 'body_fat') {
          const result = await readRecords('BodyFat', { timeRangeFilter, ascendingOrder: false, pageSize: 1 });
          const last = result.records[0];
          if (last) { value = last.percentage; measured_at = last.time; sources = [last.metadata?.dataOrigin ?? 'Health Connect']; }
        } else {
          let pageToken: string | undefined, total = 0, n = 0;
          do {
            const result = await readRecords('RespiratoryRate', { timeRangeFilter, pageSize: 1000, pageToken });
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
  if (!days.length) notices.push('Aucune mesure lisible sur 30 jours. Vérifie que tes applications alimentent Health Connect.');
  return { days, notices };
}
