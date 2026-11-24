import { ORIGIN, trusted } from './api';

// Coaching › « Mon agenda » › « Calendrier de l’iPhone » (docs/coaching.md): the phone's own calendars (iCloud and
// every account the phone syncs) read on the phone with EventKit, and ONLY busy intervals sent to the server — a start
// and an end in UTC seconds, never a title, a place, a calendar name or an attendee. Pure: the native reading is in
// phone-calendar-native.ts, App.tsx wires both.

export const HORIZON_DAYS = 21; // the horizon the server keeps (coaching_ical.HORIZON_DAYS)
export const AWAY_DAYS = 2; // an all-day event of 2 days or more is « away » (coaching_ical.AWAY_DAYS)
export const MAX_INTERVALS = 1500; // the server's bound (coaching_agenda.PHONE_MAX)
export const MAX_ATTENDEE_READS = 150; // choix pratique: invitations checked for « declined », beyond them busy
// PaceForge's own feed, should the athlete have subscribed to it on the iPhone (« Calendrier Apple »): its sessions are
// never busy time (the name the feed gives itself, coaching_ics.NAME; the server also drops the plan's own slots).
export const OWN_CALENDAR = 'PaceForge · Coaching';
export const STATES = ['none', 'fresh', 'stale', 'refused'] as const;
export type State = typeof STATES[number];

export type PhoneEvent = {
  startDate: string | Date;
  endDate: string | Date;
  allDay: boolean;
  availability?: string | null; // EventKit: busy, free, tentative, unavailable, notSupported
  status?: string | null; // none, confirmed, tentative, canceled
  declined?: boolean; // the phone's owner declined the invitation
};
export type Interval = { start: number; end: number };

const DAY = 86400000;
// The rules of the other sources (coaching_accounts, coaching_ical): free and tentative are not busy (a tentative
// meeting is not a meeting yet), a cancelled or declined event neither; busy, unavailable (absent) and an
// availability not given count as busy — iCloud events usually give none.
export function counts(e: PhoneEvent): boolean {
  if (e.declined) return false;
  if (e.status === 'canceled' || e.status === 'tentative') return false;
  return e.availability !== 'free' && e.availability !== 'tentative';
}

function time(value: string | Date): number {
  const t = value instanceof Date ? value.getTime() : Date.parse(value);
  return Number.isFinite(t) ? t : NaN;
}

function localMidnight(t: number): number {
  const d = new Date(t);
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

function addDays(midnight: number, days: number): number {
  const d = new Date(midnight);
  return new Date(d.getFullYear(), d.getMonth(), d.getDate() + days).getTime();
}

function merge(list: Interval[]): Interval[] {
  const out: Interval[] = [];
  for (const i of [...list].sort((a, b) => a.start - b.start || a.end - b.end)) {
    const last = out[out.length - 1];
    if (last && i.start <= last.end) last.end = Math.max(last.end, i.end);
    else out.push({ ...i });
  }
  return out;
}

/** The busy intervals of the next `days` days, in UTC seconds. A timed event: from its start to its end, cut to the
 * horizon. An all-day event: ignored for a single day (a reminder, a birthday); 2 days or more, sent from one local
 * midnight to another, which the server reads as « away » (as Google's intervals). Merged, at most MAX_INTERVALS. */
export function busyIntervals(events: PhoneEvent[], now: Date, days = HORIZON_DAYS): Interval[] {
  const lo = now.getTime(), hi = lo + days * DAY;
  const timed: Interval[] = [], away: Interval[] = [];
  for (const e of events) {
    if (!e || !counts(e)) continue;
    let a = time(e.startDate), b = time(e.endDate);
    if (!Number.isFinite(a) || !Number.isFinite(b) || b <= a) continue;
    if (e.allDay) {
      // EventKit ends an all-day event at the end of its last day (or at the next midnight): count whole local days
      const first = localMidnight(a), next = addDays(localMidnight(b - 1), 1);
      let n = 0;
      for (let m = first; m < next && n <= AWAY_DAYS; m = addDays(m, 1)) n++;
      if (n < AWAY_DAYS) continue;
      a = first; b = next;
    }
    const start = Math.max(a, lo), end = Math.min(b, hi);
    if (end <= start) continue;
    // an absence already begun is cut to now: no longer midnight to midnight, it is read as busy time, the same days
    (e.allDay && start === a && end === b ? away : timed).push({ start: Math.floor(start / 1000), end: Math.ceil(end / 1000) });
  }
  return [...merge(timed), ...merge(away)].sort((x, y) => x.start - y.start || x.end - y.end).slice(0, MAX_INTERVALS);
}

/** What the page asks of the app (coaching-agenda.js): only from the athlete's own Coaching page, one of three actions.
 * `connect`: the system prompt, then a reading; `refresh`: a reading without asking, as the page loads (its state
 * says why); `settings`: the phone's settings, after a refusal. */
export type PhoneRequest = { action: 'connect' | 'refresh' | 'settings'; state: State };
export function phoneRequest(pageUrl: string, data: unknown): PhoneRequest | null {
  if (!trusted(pageUrl) || new URL(pageUrl).pathname !== '/coaching') return null;
  const d = data as { type?: unknown; action?: unknown; state?: unknown } | null;
  if (!d || d.type !== 'phone-calendar') return null;
  if (d.action !== 'connect' && d.action !== 'refresh' && d.action !== 'settings') return null;
  const state = STATES.includes(d.state as State) ? d.state as State : 'none';
  if (d.action === 'refresh' && state !== 'stale' && state !== 'refused') return null;
  return { action: d.action, state };
}

/** The script that hands a reading (or the phone's refusal) to the page, which posts it to the server in its own
 * session (window.paceforgePhoneCalendar.send, coaching-agenda.js): only on the athlete's Coaching page of this site.
 * `reload`: the page comes back on « Mon agenda » to show the source's new state. */
export function phoneSendScript(body: { intervals: Interval[] } | { refused: true }, reload: boolean): string {
  return `(function () {
    if (location.origin !== ${JSON.stringify(ORIGIN)} || location.pathname !== '/coaching') return;
    if (window.paceforgePhoneCalendar && typeof window.paceforgePhoneCalendar.send === 'function')
      window.paceforgePhoneCalendar.send(${JSON.stringify(body)}, ${reload ? 'true' : 'false'});
  })(); true;`;
}

// The shell's capability, announced to the pages (iPhone only): an app installed before it never shows the button.
export const phoneCalendarShellScript = `
  if (location.origin === ${JSON.stringify(ORIGIN)}) {
    window.paceforgeNativeCalendar = 1;
    document.documentElement.classList.add('pf-native-calendar');
  }
  true;
`;
