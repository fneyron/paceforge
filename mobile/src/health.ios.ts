import { isHealthDataAvailable, isProtectedDataAvailableAsync, getRequestStatusForAuthorization, AuthorizationRequestStatus,
  requestAuthorization, queryQuantitySamples, queryCategorySamples, queryStatisticsForQuantity,
  configureBackgroundTypes, clearBackgroundTypes, disableAllBackgroundDelivery, subscribeToChanges, UpdateFrequency } from '@kingstinct/react-native-healthkit';
import * as Crypto from 'expo-crypto';
import { collectDaily, collectNights, identifiers, HealthQueries } from './health-collect';
import { HealthAuthorizationRequired, HealthDataLocked } from './health-read';
import type { ChangePlan, Metric, ReadBudget } from './types';

const queries: HealthQueries = { queryQuantitySamples, queryCategorySamples, queryStatisticsForQuantity,
  prepareRead: async identifier => {
    if (!await isProtectedDataAvailableAsync()) throw new HealthDataLocked();
    const status = await getRequestStatusForAuthorization({ toRead: [identifier] });
    // This only tells us whether a permission sheet is needed, never whether
    // read access was granted. A denied optional category can still read empty.
    // Updating an already-paired app must not query newly added categories
    // before their permission has been requested, nor prompt during auto-sync.
    if (status === AuthorizationRequestStatus.shouldRequest) throw new HealthAuthorizationRequired();
  },
  digest: (key: string) => Crypto.digestStringAsync(Crypto.CryptoDigestAlgorithm.SHA256, key) };

// A new weigh-in (Senssun → Santé) or a night recorded by a watch wakes the app. The
// night's HRV is read with the night (a later night update is sent again): no HRV wakes.
export const backgroundTypes = ['HKQuantityTypeIdentifierBodyMass', 'HKQuantityTypeIdentifierBodyFatPercentage',
  'HKCategoryTypeIdentifierSleepAnalysis'] as const;

export async function authorize(_background = false): Promise<void> {
  if (!isHealthDataAvailable()) throw new Error('Apple Santé n’est pas disponible sur cet appareil.');
  await requestAuthorization({ toRead: [...Object.values(identifiers), 'HKCategoryTypeIdentifierSleepAnalysis', 'HKQuantityTypeIdentifierHeartRateVariabilitySDNN'] });
}
export const collect = (progress: (s: string) => void, daysBack = 30, budget?: ReadBudget, _metrics?: Metric[]) =>
  collectDaily(queries, progress, daysBack, budget);
export const collectSleep = (progress: (s: string) => void, daysBack = 60, budget?: ReadBudget) =>
  collectNights(queries, progress, daysBack, budget);

/** False on a locked phone: the HealthKit store is encrypted and every read fails. */
export const protectedDataAvailable = (): Promise<boolean> => isProtectedDataAvailableAsync();
export async function hasBackgroundAccess(): Promise<boolean> { return isHealthDataAvailable(); }
/** Foreground, after authorize(). Saved natively, the observers are registered again
 * during every launch, before JavaScript runs (no AppDelegate change). */
export async function enableHealthBackground(): Promise<void> {
  await configureBackgroundTypes([...backgroundTypes], UpdateFrequency.immediate);
}
export async function disableHealthBackground(): Promise<void> {
  await clearBackgroundTypes();
  await disableAllBackgroundDelivery();
}
/** One callback per type, the last one wins natively: subscribe only from background.ts. */
export function watchHealthChanges(onChange: () => void): () => void {
  const subscriptions = backgroundTypes.map(type => subscribeToChanges(type, () => onChange()));
  return () => subscriptions.forEach(s => s.remove());
}
/** HealthKit wakes the app on a change: every background run reads the short window. */
export async function changedMetrics(tokens: Record<string, string>, _budget?: ReadBudget): Promise<ChangePlan> {
  return { daysBack: 3, tokens };
}
