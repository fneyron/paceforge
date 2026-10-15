// Keychain entries of the phone pairing and the offline index. On iOS they must
// stay readable on a locked phone (after the first unlock since the last reboot):
// background launches read the token and write the receipt while it is locked.
export const KEY = 'paceforge.phone.v1';
export const KEYS = { pair: KEY, sync: KEY + '.sync', last: KEY + '.last', auto: KEY + '.auto', background: KEY + '.bg', progress: KEY + '.bg.progress' } as const;
export const OFFLINE_INDEX = 'paceforge.offline.index.v1';
export const MIGRATED = 'paceforge.keychain.v2';

export type NativeStore = {
  getItemAsync(key: string, options?: object): Promise<string | null>;
  setItemAsync(key: string, value: string, options?: object): Promise<void>;
  deleteItemAsync(key: string, options?: object): Promise<void>;
};

/** The keychain cannot be read now (locked phone). It is never « not paired ». */
export class KeychainLocked extends Error {
  constructor() { super('keychain locked'); }
}

// errSecInteractionNotAllowed (-25308): an item readable only when unlocked.
export function isLockedError(error: unknown): boolean {
  if (error instanceof KeychainLocked) return true;
  const message = error instanceof Error ? error.message : typeof error === 'string' ? error : '';
  return /interaction is not allowed|errSecInteractionNotAllowed|-25308/i.test(message);
}

export function createSecure(native: NativeStore, options: { ios: boolean; accessible?: unknown }) {
  // Android ignores the iOS accessibility; its keystore entries stay usable when locked.
  const opts = options.ios ? { keychainAccessible: options.accessible } : {};
  const locked = (error: unknown) => isLockedError(error) ? new KeychainLocked() : error;
  async function get(key: string): Promise<string | null> {
    try { return await native.getItemAsync(key, opts); } catch (e) { throw locked(e); }
  }
  async function set(key: string, value: string): Promise<void> {
    try { await native.setItemAsync(key, value, opts); } catch (e) { throw locked(e); }
  }
  async function remove(key: string): Promise<void> {
    try { await native.deleteItemAsync(key, opts); } catch (e) { throw locked(e); }
  }
  /** Setting an existing item only changes its value: delete, then add, to apply `opts`.
   * A copy is written first, so a phone killed between delete and add loses nothing. */
  async function rewrite(key: string, value: string): Promise<void> {
    const copy = key + '.migrating';
    await set(copy, value);
    await remove(key);
    await set(key, value);
    await remove(copy);
  }
  /** The value, or the copy left by an interrupted rewrite. */
  async function recover(key: string): Promise<string | null> {
    return await get(key) ?? await get(key + '.migrating');
  }
  /** Deletes the item and any copy, which recover() would otherwise bring back. */
  async function erase(key: string): Promise<void> {
    await remove(key);
    await remove(key + '.migrating');
  }
  let migration: Promise<void> | null = null;
  /** Once, when unlocked: items created « when unlocked » get the new accessibility. */
  function migrate(): Promise<void> {
    if (!options.ios) return Promise.resolve();
    migration ??= (async () => {
      if (await get(MIGRATED) === '1') return;
      for (const key of [...Object.values(KEYS), OFFLINE_INDEX]) {
        const value = await recover(key);
        if (value !== null) await rewrite(key, value);
      }
      await set(MIGRATED, '1');
    })().catch(error => { migration = null; throw error; });
    return migration;
  }
  /** Settles after a migration started by the app, so background runs never interleave with it. */
  const settled = () => (migration ?? Promise.resolve()).catch(() => {});
  return { get, set, remove, rewrite, recover, erase, migrate, settled };
}
export type Secure = ReturnType<typeof createSecure>;
