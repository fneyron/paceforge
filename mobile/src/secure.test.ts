import test from 'node:test';
import assert from 'node:assert/strict';
import { createSecure, INSTALLED, isLockedError, KEYS, KeychainLocked, MIGRATED, NativeStore, OFFLINE_INDEX } from './secure-core';

const AFTER_FIRST_UNLOCK = 'after-first-unlock-this-device-only', WHEN_UNLOCKED = 'when-unlocked';
// Like the iOS keychain: setting an existing item only changes its value, never its accessibility.
function keychain(items: Record<string, string> = {}) {
  const entries = new Map(Object.entries(items).map(([k, v]) => [k, { value: v, accessible: WHEN_UNLOCKED }]));
  const state = { locked: false, failDeleteOf: '' as string, calls: [] as string[] };
  const refuse = (key: string) => {
    if (state.locked && entries.get(key)?.accessible !== AFTER_FIRST_UNLOCK) throw new Error("Calling the 'getValueWithKeyAsync' function has failed\n→ Caused by: User interaction is not allowed.");
  };
  const native: NativeStore = {
    async getItemAsync(key) { refuse(key); return entries.get(key)?.value ?? null; },
    async setItemAsync(key, value, options) {
      refuse(key);
      const existing = entries.get(key);
      const accessible = (options as { keychainAccessible?: string } | undefined)?.keychainAccessible ?? WHEN_UNLOCKED;
      entries.set(key, existing ? { ...existing, value } : { value, accessible });
      state.calls.push(`set ${key}`);
    },
    async deleteItemAsync(key) {
      if (key === state.failDeleteOf) { state.failDeleteOf = ''; throw new Error('killed'); }
      entries.delete(key); state.calls.push(`delete ${key}`);
    },
  };
  return { native, entries, state };
}
// The install marker: a file the system deletes with the app.
function documents(exists = false) {
  const state = { exists, failing: false };
  const marker = () => ({ get exists() { return state.exists; }, create() { if (state.failing) throw new Error('disk'); state.exists = true; } });
  return { state, marker };
}

test('migration re-creates each item readable after the first unlock, keeping its value', async () => {
  const k = keychain({ [KEYS.pair]: '{"token":"t","user_id":1,"name":"F"}', [KEYS.sync]: '{}', [KEYS.auto]: 'true', [OFFLINE_INDEX]: '{"userId":1,"slot":0}' });
  const secure = createSecure(k.native, { ios: true, accessible: AFTER_FIRST_UNLOCK });
  // A plain set keeps the old accessibility: that is why the items are deleted first.
  await k.native.setItemAsync(KEYS.auto, 'true', { keychainAccessible: AFTER_FIRST_UNLOCK });
  assert.equal(k.entries.get(KEYS.auto)?.accessible, WHEN_UNLOCKED);
  await secure.migrate();
  for (const key of [KEYS.pair, KEYS.sync, KEYS.auto, OFFLINE_INDEX]) assert.equal(k.entries.get(key)?.accessible, AFTER_FIRST_UNLOCK, key);
  assert.equal(k.entries.get(KEYS.pair)?.value, '{"token":"t","user_id":1,"name":"F"}');
  assert.equal(k.entries.get(MIGRATED)?.value, '1');
  assert.equal([...k.entries.keys()].some(key => key.endsWith('.migrating')), false);
  // On a locked phone the migrated items stay readable; a second migration does nothing.
  k.state.locked = true;
  assert.equal(await secure.get(KEYS.auto), 'true');
  const calls = k.state.calls.length;
  await createSecure(k.native, { ios: true, accessible: AFTER_FIRST_UNLOCK }).migrate();
  assert.equal(k.state.calls.length, calls);
});

test('a phone killed between delete and add keeps its pairing', async () => {
  const k = keychain({ [KEYS.pair]: 'pairing' });
  k.state.failDeleteOf = KEYS.pair + '.migrating'; // dies after the new item, before removing the copy
  const secure = createSecure(k.native, { ios: true, accessible: AFTER_FIRST_UNLOCK });
  await assert.rejects(secure.migrate());
  k.entries.delete(KEYS.pair); // worst case: also the moment between delete and add
  assert.equal(await secure.recover(KEYS.pair), 'pairing');
  await secure.migrate();
  assert.equal(k.entries.get(KEYS.pair)?.value, 'pairing');
  assert.equal(k.entries.has(KEYS.pair + '.migrating'), false);
  await secure.erase(KEYS.pair);
  assert.equal(await secure.recover(KEYS.pair), null, 'a disconnection cannot be undone by a leftover copy');
});

test('a locked keychain is reported as locked, never read as an empty value or missing pairing', async () => {
  const k = keychain({ [KEYS.pair]: 'pairing' });
  k.state.locked = true;
  const secure = createSecure(k.native, { ios: true, accessible: AFTER_FIRST_UNLOCK });
  await assert.rejects(secure.get(KEYS.pair), KeychainLocked);
  await assert.rejects(secure.recover(KEYS.pair), KeychainLocked);
  await assert.rejects(secure.migrate(), KeychainLocked);
  k.state.locked = false;
  await secure.migrate(); // retried once unlocked
  assert.equal(k.entries.get(KEYS.pair)?.accessible, AFTER_FIRST_UNLOCK);
  assert.ok(isLockedError(new Error('Error Domain=NSOSStatusErrorDomain Code=-25308')));
  assert.equal(isLockedError(new Error('The specified item could not be found in the keychain.')), false);
});

test('a reinstall starts from an empty keychain; an update of a build without the marker keeps its pairing', async () => {
  const items = { [KEYS.pair]: 'pairing', [KEYS.auto]: 'true', [KEYS.background]: '{"enabled":true}', [OFFLINE_INDEX]: '{"userId":1,"slot":0}' };
  const k = keychain(items), docs = documents();
  const open = () => createSecure(k.native, { ios: true, accessible: AFTER_FIRST_UNLOCK, marker: docs.marker });
  // The first launch of this build on an existing install: no marker yet, no stamp.
  await open().prepare();
  for (const key of Object.keys(items)) assert.equal(k.entries.get(key)?.value, items[key], key);
  assert.equal(docs.state.exists, true);
  assert.equal(k.entries.get(INSTALLED)?.value, '1');
  assert.equal(k.entries.get(MIGRATED)?.value, '1', 'the migration follows');
  // Any later launch: nothing to do.
  const calls = k.state.calls.length;
  await open().prepare();
  assert.equal(k.state.calls.length, calls);
  // Deleted and installed again: the documents are gone, the keychain outlived the app.
  docs.state.exists = false;
  await open().prepare();
  for (const key of Object.keys(items)) assert.equal(k.entries.has(key), false, key);
  assert.equal(docs.state.exists, true);
  assert.equal(k.entries.get(INSTALLED)?.value, '1');
  assert.equal(k.entries.get(MIGRATED)?.value, '1');
});

test('a marker that cannot be created never erases a later pairing; a locked keychain retries', async () => {
  const k = keychain({ [KEYS.pair]: 'old' }), docs = documents();
  k.entries.set(INSTALLED, { value: '1', accessible: AFTER_FIRST_UNLOCK }); // stamped by the previous install
  docs.state.failing = true;
  const open = () => createSecure(k.native, { ios: true, accessible: AFTER_FIRST_UNLOCK, marker: docs.marker });
  await assert.rejects(open().prepare(), /disk/);
  assert.equal(k.entries.has(KEYS.pair), false, 'the reinstall is erased');
  assert.equal(k.entries.has(INSTALLED), false, 'no stamp without a marker');
  assert.equal(k.entries.get(MIGRATED)?.value, '1', 'the migration still ran');
  await k.native.setItemAsync(KEYS.pair, 'new', { keychainAccessible: AFTER_FIRST_UNLOCK }); // paired again
  await assert.rejects(open().prepare(), /disk/);
  assert.equal(k.entries.get(KEYS.pair)?.value, 'new', 'kept: a disk that keeps failing is an update, never a reinstall');
  docs.state.failing = false;
  await open().prepare();
  assert.equal(k.entries.get(KEYS.pair)?.value, 'new');
  assert.equal(docs.state.exists, true); assert.equal(k.entries.get(INSTALLED)?.value, '1');
  // A locked phone (background launch) postpones the check; the same promise serves the engine.
  const locked = keychain({ [KEYS.pair]: 'pairing' }), present = documents(true);
  locked.state.locked = true;
  const secure = createSecure(locked.native, { ios: true, accessible: AFTER_FIRST_UNLOCK, marker: present.marker });
  await assert.rejects(secure.prepare(), KeychainLocked);
  assert.equal(locked.entries.get(KEYS.pair)?.value, 'pairing');
  locked.state.locked = false;
  await secure.prepare();
  assert.equal(locked.entries.get(INSTALLED)?.value, '1');
  assert.equal(locked.entries.get(KEYS.pair)?.accessible, AFTER_FIRST_UNLOCK);
});

test('Android keeps its keystore entries as they are', async () => {
  const k = keychain({ [KEYS.pair]: 'pairing' });
  const secure = createSecure(k.native, { ios: false, accessible: AFTER_FIRST_UNLOCK });
  await secure.migrate();
  await secure.set(KEYS.last, 'x');
  assert.deepEqual(k.state.calls, [`set ${KEYS.last}`]);
  assert.equal(k.entries.get(KEYS.last)?.accessible, WHEN_UNLOCKED, 'no iOS option is passed');
});
