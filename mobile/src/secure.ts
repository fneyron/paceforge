import { Platform } from 'react-native';
import * as SecureStore from 'expo-secure-store';
import { createSecure } from './secure-core';

export { KEY, KEYS, OFFLINE_INDEX, KeychainLocked, isLockedError } from './secure-core';

const ios = Platform.OS === 'ios';
export const secure = createSecure(SecureStore, { ios, accessible: SecureStore.AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY });

/** Synchronous read at module load (background launch); null when the keychain is unreadable. */
export function readNow(key: string): string | null | undefined {
  try {
    return SecureStore.getItem(key, ios ? { keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY } : {});
  } catch { return undefined; }
}
