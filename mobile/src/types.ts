export type Metric = 'weight' | 'body_fat' | 'steps' | 'hr_day' | 'resp_day';
export type Daily = { date: string; metric: Metric; value: number; unit: string; sources: string[]; measured_at?: string };
export type Pair = { token: string; user_id: number; name: string };
export type HealthResult = { days: Daily[]; notices: string[] };
export const labels: Record<Metric, string> = { weight: 'Poids', body_fat: 'Masse grasse', steps: 'Pas', hr_day: 'FC sur 24 h', resp_day: 'Respiration sur 24 h' };
export function localDay(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
export function windows(now = new Date()) {
  return Array.from({ length: 30 }, (_, i) => {
    const start = new Date(now); start.setHours(0, 0, 0, 0); start.setDate(start.getDate() - i);
    const end = new Date(start); end.setDate(end.getDate() + 1);
    return { start, end: end > now ? now : end, date: localDay(start) };
  });
}
export const limits: Record<Metric, [number, number]> = { weight: [25, 300], body_fat: [1, 75], steps: [0, 200000], hr_day: [25, 230], resp_day: [4, 60] };
export function valid(d: Daily): boolean { const [lo, hi] = limits[d.metric]; return Number.isFinite(d.value) && d.value >= lo && d.value <= hi && d.sources.length > 0; }
