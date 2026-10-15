import { Platform } from 'react-native';
import * as SecureStore from 'expo-secure-store';
import { File, Paths } from 'expo-file-system';
import { createSecure } from './secure-core';

export { KEY, KEYS, OFFLINE_INDEX, KeychainLocked, isLockedError } from './secure-core';

const ios = Platform.OS === 'ios';
// Opened on use: nothing native may throw while this module is evaluated at launch.
const marker = () => {
  const file = new File(Paths.document, 'paceforge-installed');
  return { get exists() { return file.exists; }, create: () => file.write('1') };
};
export const secure = createSecure(SecureStore, { ios, accessible: SecureStore.AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY, marker });

/** Synchronous read at module load (background launch); null when the keychain is unreadable. */
export function readNow(key: string): string | null | undefined {
  try {
    return SecureStore.getItem(key, ios ? { keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY } : {});
  } catch { return undefined; }
}
