// HealthKit timestamps stay absolute until the server assigns the local wake day.
// Keep each writer/device separate: SDNN and RMSSD are never interconverted.
export type SourceSample = {
  sourceRevision: { source: { bundleIdentifier: string; name: string }; productType?: string | null };
  device?: { localIdentifier?: string | null; model?: string | null; hardwareVersion?: string | null } | null;
  metadata?: { HKTimeZone?: string; HKWasUserEntered?: boolean } | null;
};
export type SleepSample = SourceSample & { startDate: Date; endDate: Date; value: number };
export type Stage = 'asleep' | 'awake' | 'core' | 'deep' | 'rem';
export type Interval = { start: string; end: string; stage: Stage };
export type Reading = { at: string; value: number };
export type AppleNight = { date: string; source_id: string; source_name: string; timezone: string;
  sleep: Interval[]; naps: Interval[][]; hr: Reading[]; hrv_sdnn: Reading[]; resp: Reading[] };
export type Episode = { date: string; key: string; source_name: string; timezone: string; sleep: Interval[]; naps: Interval[][] };
const stages: Record<number, Stage | undefined> = { 1: 'asleep', 2: 'awake', 3: 'core', 4: 'deep', 5: 'rem' };
export function writer(s: SourceSample): string {
  return JSON.stringify([s.sourceRevision.source.bundleIdentifier,
    s.device?.localIdentifier || [s.device?.model || '', s.device?.hardwareVersion || '', s.sourceRevision.productType || ''].join('/')]);
}
function zoneFor(s: SourceSample, fallback: string): string {
  const zone = s.metadata?.HKTimeZone || fallback;
  try { new Intl.DateTimeFormat('en', { timeZone: zone }).format(); return zone; } catch { return fallback; }
}
function dayIn(at: number, zone: string): string {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date(at));
  const get = (key: string) => parts.find(p => p.type === key)?.value;
  return `${get('year')}-${get('month')}-${get('day')}`;
}
function asleepDuration(parts: Interval[]): number {
  const edges = [...new Set(parts.flatMap(s => [Date.parse(s.start), Date.parse(s.end)]))].sort((a,b) => a-b);
  return edges.slice(1).reduce((total, b, i) => {
    const a = edges[i], active = parts.filter(s => Date.parse(s.start) <= a && Date.parse(s.end) >= b);
    return total + (active.some(s => s.stage !== 'awake') && !active.some(s => s.stage === 'awake') ? b-a : 0);
  }, 0);
}
export function episodes(samples: readonly SleepSample[], fallback: string, now = new Date(), daysBack = 60): Episode[] {
  const groups = new Map<string, { key: string; source_name: string; timezone: string; parts: Interval[] }>();
  for (const s of samples) {
    const stage = stages[s.value], a = s.startDate.getTime(), b = s.endDate.getTime();
    if (!stage || s.metadata?.HKWasUserEntered || !Number.isFinite(a+b) || b <= a || b > now.getTime() || b-a > 18*3600000) continue;
    const key = writer(s), timezone = zoneFor(s, fallback), id = JSON.stringify([key, timezone]);
    if (!groups.has(id)) groups.set(id, { key, source_name: s.sourceRevision.source.name.slice(0,150), timezone, parts: [] });
    groups.get(id)!.parts.push({ start: s.startDate.toISOString(), end: s.endDate.toISOString(), stage });
  }
  const result: Episode[] = [];
  for (const group of groups.values()) {
    // Sessions of one writer. A writer that records « awake » marks its own wake periods,
    // so a silence of more than 90 min ends a session (a nap is not the night). A writer
    // that never records « awake » leaves a silence for each wake period: only a silence of
    // more than three hours ends one of its sessions, so a long wake in the night does not
    // turn the first hours of sleep into a nap. The server counts measured sleep only; a
    // silence inside a session stays unknown, never asleep.
    const silence = group.parts.some(p => p.stage === 'awake') ? 90*60000 : 180*60000;
    const clusters: Interval[][] = [];
    let end = 0;
    for (const part of group.parts.sort((a,b) => Date.parse(a.start)-Date.parse(b.start))) {
      if (!clusters.length || Date.parse(part.start)-end > silence) clusters.push([]);
      clusters.at(-1)!.push(part); end = Math.max(end, Date.parse(part.end));
    }
    const days = new Map<string, Interval[][]>();
    for (let cluster of clusters) {
      const asleep = cluster.filter(p => p.stage !== 'awake');
      if (!asleep.length) continue;
      const a = Math.min(...asleep.map(p => Date.parse(p.start))), b = Math.max(...asleep.map(p => Date.parse(p.end)));
      if (b-a > 18*3600000) continue;
      // Leading/trailing wake is not part of a sleep episode or its wake day.
      cluster = cluster.filter(p => Date.parse(p.end) > a && Date.parse(p.start) < b).map(p => ({ ...p,
        start: new Date(Math.max(a, Date.parse(p.start))).toISOString(), end: new Date(Math.min(b, Date.parse(p.end))).toISOString() }));
      if (cluster.length > 1000) continue;
      const date = dayIn(b, group.timezone);
      if (!days.has(date)) days.set(date, []);
      days.get(date)!.push(cluster);
    }
    const cutoff = new Date(dayIn(now.getTime(), group.timezone)+'T00:00:00Z'); cutoff.setUTCDate(cutoff.getUTCDate()-daysBack+1);
    for (const [date, parts] of days) {
      if (date < cutoff.toISOString().slice(0,10)) continue;
      parts.sort((a,b) => asleepDuration(b)-asleepDuration(a));
      const sleep = asleepDuration(parts[0]) >= 180*60000 ? parts.shift()! : [];
      // Implausible naps (more than twelve, or more than ten hours: the server refuses the
      // packet) are left out; the night of that day is still sent.
      const shortOnes = parts.filter(p => Date.parse(p.at(-1)!.end)-Date.parse(p[0].start) <= 6*3600000);
      const naps = shortOnes.length <= 12 && shortOnes.reduce((n,p) => n+asleepDuration(p),0) <= 600*60000 ? shortOnes : [];
      if (sleep.length || naps.length) result.push({ date, key: group.key, timezone: group.timezone, source_name: group.source_name, sleep, naps });
    }
  }
  return result.sort((a,b) => b.date.localeCompare(a.date));
}
export function nightReadings(samples: readonly (SourceSample & { startDate: Date; quantity: number })[], episode: Episode, lo: number, hi: number): Reading[] {
  return samples.filter(s => writer(s) === episode.key && !s.metadata?.HKWasUserEntered &&
    Number.isFinite(s.quantity) && lo <= s.quantity && s.quantity <= hi &&
    episode.sleep.some(p => p.stage !== 'awake' && Date.parse(p.start) <= s.startDate.getTime() && s.startDate.getTime() < Date.parse(p.end)) &&
    !episode.sleep.some(p => p.stage === 'awake' && Date.parse(p.start) <= s.startDate.getTime() && s.startDate.getTime() < Date.parse(p.end)))
    .map(s => ({ at: s.startDate.toISOString(), value: s.quantity }));
}
