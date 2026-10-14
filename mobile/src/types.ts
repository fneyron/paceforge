export type Metric = 'weight' | 'body_fat' | 'steps' | 'hr_day' | 'resp_day';
export type Daily = { date: string; metric: Metric; value: number; unit: string; sources: string[]; measured_at?: string };
// prev_token: kept after a rotation until the new token works (see api.authorized).
export type Pair = { token: string; user_id: number; name: string; prev_token?: string; rotated_at?: string };
// quota: Health Connect refused further reads for now (background quota).
// unread: requested categories not read in full (error, time limit, quota).
export type HealthResult = { days: Daily[]; notices: string[]; complete: boolean; quota?: boolean; unread?: Metric[] };
/** Read time budgets: the whole collection, and each native query. */
export type ReadBudget = { total?: number; perRead?: number };
/** What a background run reads: `daysBack` 0 when nothing changed since the
 * stored Health Connect change tokens (one per category, keyed by metric);
 * `quota` when Health Connect refused. */
export type ChangePlan = { daysBack: number; metrics?: Metric[]; tokens: Record<string, string>; quota?: boolean };
export type SleepResult = { nights: import('./night-samples').AppleNight[]; notices: string[]; complete: boolean };
export const labels: Record<Metric, string> = { weight: 'Poids', body_fat: 'Masse grasse', steps: 'Pas', hr_day: 'FC sur 24 h', resp_day: 'Respiration sur 24 h' };
export function localDay(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
export function windows(now = new Date(), count = 30) {
  return Array.from({ length: count }, (_, i) => {
    const start = new Date(now); start.setHours(0, 0, 0, 0); start.setDate(start.getDate() - i);
    const end = new Date(start); end.setDate(end.getDate() + 1);
    return { start, end: end > now ? now : end, date: localDay(start) };
  });
}
export const limits: Record<Metric, [number, number]> = { weight: [25, 300], body_fat: [1, 75], steps: [0, 200000], hr_day: [25, 230], resp_day: [4, 60] };
export function valid(d: Daily): boolean { const [lo, hi] = limits[d.metric]; return Number.isFinite(d.value) && d.value >= lo && d.value <= hi && d.sources.length > 0; }

export function latestWeighIn(metric: 'weight' | 'body_fat', date: string,
  readings: { value: number; at: string; source: string }[], now = new Date()): Daily | undefined {
  return readings.map(r => ({ date, metric, value: r.value, measured_at: r.at,
    unit: metric === 'weight' ? 'kg' : '%', sources: r.source ? [r.source] : [] }))
    .filter(d => valid(d) && Number.isFinite(Date.parse(d.measured_at)) && Date.parse(d.measured_at) <= +now)
    .sort((a, b) => Date.parse(b.measured_at) - Date.parse(a.measured_at))[0];
}
