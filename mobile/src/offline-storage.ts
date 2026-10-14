import { File, Paths } from 'expo-file-system';
import { OfflineLibrary, readOfflinePlan } from './offline';
import { OFFLINE_INDEX as INDEX, secure } from './secure';

const fileAt = (slot: number) => new File(Paths.document, `paceforge-plans-v1-${slot}.json`);
const empty: OfflineLibrary = { userId: null, plans: [] };

// A locked keychain throws KeychainLocked: never read as « no copies ». An index
// rewritten by an interrupted keychain migration is recovered from its copy.
async function index(): Promise<{ userId: number; slot: 0 | 1 } | null> {
  const raw = await secure.recover(INDEX);
  if (!raw) return null;
  try {
    const value = JSON.parse(raw);
    return Number.isSafeInteger(value?.userId) && value.userId > 0 && [0, 1].includes(value.slot) ? value : null;
  } catch { return null; }
}

export async function loadOfflineLibrary(): Promise<OfflineLibrary> {
  const owner = await index();
  if (!owner) return empty;
  const file = fileAt(owner.slot);
  if (!file.exists || file.size > 4000000) throw new Error('invalid offline file');
  const data = JSON.parse(await file.text());
  if (data.userId !== owner.userId || !Array.isArray(data.plans) || data.plans.length > 20) throw new Error('invalid offline owner');
  const plans = data.plans.map(readOfflinePlan);
  if (plans.some((p: ReturnType<typeof readOfflinePlan>) => !p || p.user_id !== data.userId)) return empty;
  return { userId: data.userId, plans };
}

/** Two slots: publish the SecureStore pointer only after a complete file write.
 * Expo's overwrite move deletes its destination first, so it is not an atomic replacement.
 * An interrupted same-account save leaves the previous slot available on restart. */
export async function storeOfflineLibrary(library: OfflineLibrary): Promise<void> {
  const previous = await index();
  // Also its copy, which recover() would otherwise bring back.
  if (previous?.userId !== library.userId) await secure.erase(INDEX);
  if (library.userId !== null) {
    const slot = previous?.slot === 0 ? 1 : 0;
    fileAt(slot).write(JSON.stringify(library));
    await secure.set(INDEX, JSON.stringify({ userId: library.userId, slot }));
    // A cleanup error does not undo the committed copy. The old slot is never read.
    try { const old = fileAt(1 - slot); if (old.exists) old.delete(); } catch { /* overwritten on next save */ }
  } else {
    for (const slot of [0, 1]) { const old = fileAt(slot); if (old.exists) old.delete(); }
  }
}
