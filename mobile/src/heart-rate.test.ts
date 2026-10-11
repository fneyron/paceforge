import { test } from 'node:test';
import assert from 'node:assert/strict';
import { decodeHeartRate, isBluetoothUuid, liveReading } from './heart-rate';
import { HeartEvents, HeartRateController, HeartTransport } from './heart-rate-controller';

test('Bluetooth measurements decode 8/16-bit BPM, contact and optional fields', () => {
  assert.equal(decodeHeartRate([0, 72]), 72);
  assert.equal(decodeHeartRate([1, 180, 0]), 180);
  assert.equal(decodeHeartRate([6, 82]), 82);
  assert.equal(decodeHeartRate([4, 82]), null); // sensor removed; don't retain the old BPM
  assert.equal(decodeHeartRate([30, 80, 100, 0, 0, 4, 0, 3]), 80); // energy + two RR intervals
  for (const bytes of [[], [0], [1, 82], [8, 82], [16, 82], [16, 82, 0], [0, 0],
    [0, 255], [1, 1, 1], [0, 75, 10], [32, 75], [0, -1], [0, 80.5], [0, NaN]]) {
    assert.equal(decodeHeartRate(bytes), null, String(bytes));
  }
});

test('expired or future readings are never shown as live; UUIDs are exact', () => {
  const reading = { bpm: 70, at: 1000 };
  assert.equal(liveReading(reading, 1001), reading);
  assert.equal(liveReading(reading, 16000), null);
  assert.equal(liveReading(reading, 999), null);
  assert.ok(isBluetoothUuid('0000180D-0000-1000-8000-00805F9B34FB', '180d'));
  assert.equal(isBluetoothUuid('181d', '180d'), false);
  assert.equal(isBluetoothUuid('0000180d-0000-1000-8000-unknown', '180d'), false);
});

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>(r => { resolve = r; });
  return { promise, resolve };
}

function fixture(overrides: Partial<HeartTransport> = {}, timeoutMs = 20000) {
  let events!: HeartEvents;
  const calls: string[] = [];
  const transport: HeartTransport = {
    prepare: async () => { calls.push('prepare'); },
    scan: async () => { calls.push('scan'); },
    stopScan: async () => { calls.push('stopScan'); },
    connect: async id => { calls.push(`connect:${id}`); },
    subscribe: async id => { calls.push(`subscribe:${id}`); },
    disconnect: async id => { calls.push(`disconnect:${id}`); },
    listen: handlers => { events = handlers; return () => { calls.push('unlisten'); }; },
    ...overrides,
  };
  const controller = new HeartRateController(transport, () => {}, timeoutMs);
  return { controller, events, calls };
}
const sensor = { id: 'sensor-1', name: 'COROS HR Monitor' };

test('only the selected connected sensor may update the live reading', async () => {
  const { controller, events, calls } = fixture();
  await controller.scan();
  events.discovered(sensor); events.discovered(sensor);
  assert.equal(controller.state.sensors.length, 1);
  assert.ok(!calls.some(c => c.startsWith('connect:'))); // discovery never pairs automatically
  await controller.connect(sensor);
  events.value('other', '180d', '2a37', [0, 72]);
  events.value(sensor.id, '180d', '2a38', [0, 72]);
  assert.equal(controller.state.reading?.bpm, undefined);
  events.value(sensor.id, '180d', '2a37', [0, 72]);
  assert.equal(controller.state.reading?.bpm, 72);
  events.value(sensor.id, '180d', '2a37', [4, 72]);
  assert.equal(controller.state.reading, null);
  events.value(sensor.id, '180d', '2a37', [0, 74]);
  events.disconnected(sensor.id);
  assert.equal(controller.state.reading, null);
  assert.equal(controller.state.phase, 'idle');
  events.value(sensor.id, '180d', '2a37', [0, 74]);
  assert.equal(controller.state.reading, null);
  controller.dispose();
});

test('background/cancel during permissions cannot start a later scan', async () => {
  const permission = deferred();
  const { controller, calls } = fixture({ prepare: () => permission.promise });
  const pending = controller.scan();
  controller.stop();
  permission.resolve(); await pending;
  assert.ok(!calls.includes('scan'));
  assert.equal(controller.state.phase, 'idle');
  controller.dispose();
});

test('cancelled and timed-out connections are released even if native connect resolves late', async () => {
  for (const timeout of [false, true]) {
    const connection = deferred();
    const { controller, calls } = fixture({ connect: () => connection.promise }, timeout ? 5 : 20000);
    await controller.scan();
    const connecting = controller.connect(sensor);
    await Promise.resolve();
    if (timeout) await new Promise(resolve => setTimeout(resolve, 15));
    else controller.stop();
    connection.resolve(); await connecting;
    assert.equal(controller.state.phase, 'idle');
    assert.ok(!calls.includes(`subscribe:${sensor.id}`));
    assert.ok(calls.includes(`disconnect:${sensor.id}`));
    controller.dispose();
  }
});

test('permission, notification and power failures leave no live value or active connection', async () => {
  const denied = fixture({ prepare: async () => { throw new Error('Permission refusée'); } });
  await denied.controller.scan();
  assert.equal(denied.controller.state.error, 'Permission refusée');
  assert.equal(denied.controller.state.phase, 'idle');
  denied.controller.dispose();
  const missing = fixture({ subscribe: async () => { throw new Error('Unsupported'); } });
  await missing.controller.scan(); await missing.controller.connect(sensor);
  assert.ok(missing.calls.includes(`disconnect:${sensor.id}`));
  assert.equal(missing.controller.state.phase, 'idle');
  missing.controller.dispose();
  const connected = fixture();
  await connected.controller.scan(); await connected.controller.connect(sensor);
  connected.events.value(sensor.id, '180d', '2a37', [0, 75]);
  connected.events.poweredOff();
  assert.equal(connected.controller.state.reading, null);
  connected.controller.dispose();
  assert.ok(connected.calls.includes('unlisten'));
});
