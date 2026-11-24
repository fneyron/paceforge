import { test } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { busyIntervals, counts, MAX_INTERVALS, phoneCalendarShellScript, phoneRequest, phoneSendScript, PhoneEvent } from './phone-calendar';

process.env.TZ = process.env.TZ || 'Europe/Paris';
const now = new Date('2026-11-24T08:00:00Z');
const at = (iso: string) => new Date(iso);
const ev = (start: string, end: string, more: Partial<PhoneEvent> = {}): PhoneEvent =>
  ({ startDate: at(start), endDate: at(end), allDay: false, availability: 'busy', status: 'confirmed', ...more });
const s = (iso: string) => Date.parse(iso) / 1000;

test('the busy rules are the other sources\': free, tentative, cancelled and declined are not busy; no availability is', () => {
  assert.equal(counts({ startDate: now, endDate: now, allDay: false }), true, 'iCloud gives no availability: busy');
  for (const availability of ['busy', 'unavailable', 'notSupported', null, undefined]) assert.equal(counts({ startDate: now, endDate: now, allDay: false, availability }), true, String(availability));
  for (const availability of ['free', 'tentative']) assert.equal(counts({ startDate: now, endDate: now, allDay: false, availability }), false, availability);
  assert.equal(counts({ startDate: now, endDate: now, allDay: false, status: 'canceled' }), false);
  assert.equal(counts({ startDate: now, endDate: now, allDay: false, status: 'tentative' }), false);
  assert.equal(counts({ startDate: now, endDate: now, allDay: false, declined: true }), false);
});

test('only intervals in UTC seconds leave the phone, cut to the 21 days and merged', () => {
  const out = busyIntervals([
    ev('2026-11-25T09:00:00Z', '2026-11-25T10:00:00Z'),
    ev('2026-11-25T09:30:00Z', '2026-11-25T11:00:00Z'), // overlapping: merged
    ev('2026-11-25T12:00:00Z', '2026-11-25T13:00:00Z', { availability: 'free' }),
    ev('2026-11-24T07:00:00Z', '2026-11-24T09:00:00Z'), // begun: cut to now
    ev('2026-12-14T07:00:00Z', '2026-12-16T09:00:00Z'), // past the horizon: cut to it
    ev('2026-12-20T07:00:00Z', '2026-12-20T09:00:00Z'), // beyond: gone
    ev('2026-11-26T09:00:00Z', '2026-11-26T09:00:00Z'), // empty: gone
    { startDate: 'not a date', endDate: '2026-11-26T09:00:00Z', allDay: false },
  ], now);
  assert.deepEqual(out, [
    { start: s('2026-11-24T08:00:00Z'), end: s('2026-11-24T09:00:00Z') },
    { start: s('2026-11-25T09:00:00Z'), end: s('2026-11-25T11:00:00Z') },
    { start: s('2026-12-14T07:00:00Z'), end: s('2026-12-15T08:00:00Z') },
  ]);
  for (const i of out) assert.deepEqual(Object.keys(i), ['start', 'end'], 'never anything but a start and an end');
  assert.ok(busyIntervals(Array.from({ length: MAX_INTERVALS + 50 }, (_, k) =>
    ev(new Date(now.getTime() + (k * 2 + 1) * 60000).toISOString(), new Date(now.getTime() + (k * 2 + 2) * 60000).toISOString())), now).length <= MAX_INTERVALS);
});

test('an all-day event: a single day is ignored, two days or more go from one local midnight to another', () => {
  const day = (d: number) => new Date(2026, 10, d).getTime(); // local midnights (the phone's clock)
  const out = busyIntervals([
    // a birthday (EventKit ends an all-day event at the end of its day)
    { startDate: new Date(day(26)), endDate: new Date(day(27) - 1000), allDay: true },
    // a holiday, Friday and Saturday
    { startDate: new Date(day(27)), endDate: new Date(day(29) - 1000), allDay: true, availability: 'free' },
    { startDate: new Date(day(27)), endDate: new Date(day(29) - 1000), allDay: true },
  ], now);
  assert.deepEqual(out, [{ start: day(27) / 1000, end: day(29) / 1000 }]);
});

test('the page asks only from the athlete\'s own Coaching page, one of three actions', () => {
  const page = 'https://paceforge.fr/coaching?outil=agenda';
  assert.deepEqual(phoneRequest(page, { type: 'phone-calendar', action: 'connect', state: 'none' }), { action: 'connect', state: 'none' });
  assert.deepEqual(phoneRequest(page, { type: 'phone-calendar', action: 'settings' }), { action: 'settings', state: 'none' });
  assert.deepEqual(phoneRequest(page, { type: 'phone-calendar', action: 'refresh', state: 'stale' }), { action: 'refresh', state: 'stale' });
  assert.equal(phoneRequest(page, { type: 'phone-calendar', action: 'refresh', state: 'fresh' }), null, 'nothing to read again');
  assert.equal(phoneRequest(page, { type: 'phone-calendar', action: 'refresh', state: 'none' }), null, 'never connected without a tap');
  assert.equal(phoneRequest(page, { type: 'phone-calendar', action: 'read-titles' }), null);
  assert.equal(phoneRequest(page, { type: 'other', action: 'connect' }), null);
  assert.equal(phoneRequest(page, null), null);
  for (const url of ['https://paceforge.fr/coaching/athletes/3', 'https://paceforge.fr/sante', 'https://evil.test/coaching',
    'https://paceforge.fr.evil.test/coaching']) assert.equal(phoneRequest(url, { type: 'phone-calendar', action: 'connect' }), null, url);
});

test('the reading goes to the page, which posts it in its own session, only on the Coaching page of this site', () => {
  const sent: unknown[] = [];
  const run = (origin: string, pathname: string, script: string) =>
    vm.runInNewContext(script, { location: { origin, pathname }, window: { paceforgePhoneCalendar: { send: (...a: unknown[]) => sent.push(a) } }, JSON });
  const script = phoneSendScript({ intervals: [{ start: 1, end: 2 }] }, true);
  run('https://paceforge.fr', '/coaching', script);
  const plain = () => JSON.parse(JSON.stringify(sent)); // made in another realm
  assert.deepEqual(plain(), [[{ intervals: [{ start: 1, end: 2 }] }, true]]);
  run('https://paceforge.fr', '/sante', script);
  run('https://evil.test', '/coaching', script);
  run('https://paceforge.fr', '/coaching', phoneSendScript({ refused: true }, false));
  assert.deepEqual(plain().at(-1), [{ refused: true }, false]);
  assert.equal(sent.length, 2);
});

test('the capability is announced to this site only', () => {
  const shell = (origin: string) => {
    const classes = new Set<string>(); const window: Record<string, unknown> = {};
    vm.runInNewContext(phoneCalendarShellScript, { window, document: { documentElement: { classList: { add: (c: string) => classes.add(c) } } }, location: { origin } });
    return { classes, window };
  };
  const site = shell('https://paceforge.fr');
  assert.equal(site.window.paceforgeNativeCalendar, 1);
  assert.ok(site.classes.has('pf-native-calendar'));
  assert.equal(shell('https://example.org').classes.size, 0);
});
