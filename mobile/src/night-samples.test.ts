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
test('local wake date follows the night timezone, and a short nap has no main-night summary', () => {
  const s=sample('2026-10-25T14:00:00Z','2026-10-25T22:00:00Z'); s.metadata={HKTimeZone:'Asia/Tokyo'};
  assert.equal(episodes([s],'UTC',now)[0].date,'2026-10-26');
  const nap=episodes([sample('2026-10-25T13:00:00Z','2026-10-25T13:30:00Z')],'UTC',now)[0];
  assert.equal(nap.sleep.length,0); assert.equal(nap.naps.length,1);
  assert.deepEqual(episodes([],'UTC',now),[]);
});
