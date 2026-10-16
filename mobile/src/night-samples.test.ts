import test from 'node:test';
import assert from 'node:assert/strict';
import { episodes, nightReadings, SleepSample, writer } from './night-samples';
const now = new Date('2026-10-26T18:00:00Z');
function sample(start: string, end: string, value=3, device='watch1'): SleepSample {
  return { startDate:new Date(start),endDate:new Date(end),value,
    sourceRevision:{ source:{ name:'Watch',bundleIdentifier:'com.apple.health' },productType:'Watch7' },
    device:{ localIdentifier:device },metadata:{HKTimeZone:'Europe/Paris'} };
}
const main = sample('2026-10-24T21:00:00Z','2026-10-25T06:00:00Z');
test('short overlapping sync includes late nights but not the full history', () => {
  const old = sample('2026-10-10T21:00:00Z', '2026-10-11T06:00:00Z');
  assert.equal(episodes([main, old], 'UTC', now, 7).length, 1);
  assert.equal(episodes([main, old], 'UTC', now, 60).length, 2);
});
test('DST, overlapping stages and naps do not become multiple nights or double duration', () => {
  const e=episodes([main,{...main,value:1},sample('2026-10-25T01:00:00Z','2026-10-25T01:15:00Z',2),
    sample('2026-10-25T13:00:00Z','2026-10-25T13:30:00Z'), {...main,value:0}], 'UTC',now);
  assert.equal(e.length,1); assert.equal(e[0].date,'2026-10-25'); assert.equal(e[0].sleep.length,3); assert.equal(e[0].naps.length,1);
});
test('different devices and sources stay separate; manual and in-bed entries excluded', () => {
  const e=episodes([main,{...main,device:{localIdentifier:'watch2'}},{...main,metadata:{HKWasUserEntered:true}},
    {...main,value:0}], 'UTC',now);
  assert.equal(e.length,2); assert.notEqual(e[0].key,e[1].key);
  assert.equal(writer(main),writer({...main,sourceRevision:{...main.sourceRevision, source:{...main.sourceRevision.source,name:'New name'}}}));
});
test('heart samples require the same device and real sleep, with explicit wake winning', () => {
  const e=episodes([main,sample('2026-10-25T01:00:00Z','2026-10-25T01:15:00Z',2)],'UTC',now)[0];
  const reading=(at:string,quantity=50)=>({...main,startDate:new Date(at),quantity});
  const data=[reading('2026-10-25T00:30:00Z'),reading('2026-10-25T01:05:00Z',190),
    {...reading('2026-10-25T02:00:00Z'),device:{localIdentifier:'other'}},reading('2026-10-25T12:00:00Z'),reading('2026-10-25T03:00:00Z',NaN)];
  assert.deepEqual(nightReadings(data,e,25,230),[{at:'2026-10-25T00:30:00.000Z',value:50}]);
});
test('implausible naps are left out, the night of that day is kept', () => {
  // A writer that marks its wake periods (sessions stay apart): two sleeps of five hours
  // in the day make more than ten hours of naps, which the server refuses with the night.
  const awake = sample('2026-10-25T02:00:00Z', '2026-10-25T02:05:00Z', 2);
  const inside = (hour: string) => sample(`2026-10-25T${hour}:00:00Z`, `2026-10-25T${hour}:05:00Z`, 2);
  const dozes = [sample('2026-10-25T08:00:00Z', '2026-10-25T13:00:00Z'), inside('10'), sample('2026-10-25T15:00:00Z', '2026-10-25T20:30:00Z'), inside('17')];
  const [night] = episodes([main, awake, ...dozes], 'UTC', now);
  assert.equal(night.date, '2026-10-25'); assert.equal(night.sleep.length, 2); assert.deepEqual(night.naps, []);
  // Under ten hours they are naps.
  const [kept] = episodes([main, awake, dozes[0], inside('10'), sample('2026-10-25T15:00:00Z', '2026-10-25T19:30:00Z'), inside('17')], 'UTC', now);
  assert.equal(kept.sleep.length, 2); assert.equal(kept.naps.length, 2);
});
test('a long wake inside a night is kept in it; a doze, short naps or an awake-marked session stay apart', () => {
  // Two blocks of « asleep » around a two-hour wake: one night of eight hours, no nap.
  const silent = [sample('2026-10-24T21:00:00Z', '2026-10-25T01:00:00Z', 1), sample('2026-10-25T03:00:00Z', '2026-10-25T07:00:00Z', 1)];
  const [night] = episodes(silent, 'UTC', now);
  assert.equal(night.sleep.length, 2); assert.deepEqual(night.naps, []);
  // Less than an hour of sleep is never joined: a doze before bed stays a nap of its own day.
  const dozed = Object.fromEntries(episodes([sample('2026-10-24T18:00:00Z', '2026-10-24T18:40:00Z', 1), ...silent], 'UTC', now).map(e => [e.date, e]));
  assert.equal(dozed['2026-10-25'].sleep.length, 2); assert.deepEqual(dozed['2026-10-25'].naps, []);
  assert.deepEqual(dozed['2026-10-24'].sleep, []); assert.equal(dozed['2026-10-24'].naps.length, 1);
  // Four short naps less than three hours apart after the night: naps, never a 21-hour session.
  const naps = ['09', '12', '15', '18'].map(hour => sample(`2026-10-25T${hour}:00:00Z`, `2026-10-25T${hour}:30:00Z`, 1));
  const napped = episodes([...silent, ...naps], 'UTC', now);
  assert.equal(napped.length, 1); assert.equal(napped[0].sleep.length, 2); assert.equal(napped[0].naps.length, 4);
  // The same shape whatever the window read.
  assert.deepEqual(episodes([...silent, ...naps], 'UTC', now, 7), episodes([...silent, ...naps], 'UTC', now, 60));
  // The same gap from a writer that records its wake periods: two sessions, the shorter is a nap.
  const marking = [sample('2026-10-24T22:00:00Z', '2026-10-25T01:00:00Z'), sample('2026-10-25T00:30:00Z', '2026-10-25T00:40:00Z', 2),
    sample('2026-10-25T03:00:00Z', '2026-10-25T07:00:00Z')];
  const [split] = episodes(marking, 'UTC', now);
  assert.equal(split.sleep.length, 1); assert.equal(split.naps.length, 1);
});
test('local wake date follows the night timezone, and a short nap has no main-night summary', () => {
  const s=sample('2026-10-25T14:00:00Z','2026-10-25T22:00:00Z'); s.metadata={HKTimeZone:'Asia/Tokyo'};
  assert.equal(episodes([s],'UTC',now)[0].date,'2026-10-26');
  const nap=episodes([sample('2026-10-25T13:00:00Z','2026-10-25T13:30:00Z')],'UTC',now)[0];
  assert.equal(nap.sleep.length,0); assert.equal(nap.naps.length,1);
  assert.deepEqual(episodes([],'UTC',now),[]);
});
