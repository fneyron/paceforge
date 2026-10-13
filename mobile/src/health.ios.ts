import { isHealthDataAvailable, isProtectedDataAvailableAsync, getRequestStatusForAuthorization, AuthorizationRequestStatus,
  requestAuthorization, queryQuantitySamples, queryCategorySamples, queryStatisticsForQuantity } from '@kingstinct/react-native-healthkit';
import * as Crypto from 'expo-crypto';
import { collectDaily, collectNights, identifiers, HealthQueries } from './health-collect';
import { HealthAuthorizationRequired, HealthDataLocked } from './health-read';

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

export async function authorize(): Promise<void> {
  if (!isHealthDataAvailable()) throw new Error('Apple Santé n’est pas disponible sur cet appareil.');
  await requestAuthorization({ toRead: [...Object.values(identifiers), 'HKCategoryTypeIdentifierSleepAnalysis', 'HKQuantityTypeIdentifierHeartRateVariabilitySDNN'] });
}
export const collect = (progress: (s: string) => void, daysBack = 30) => collectDaily(queries, progress, daysBack);
export const collectSleep = (progress: (s: string) => void, daysBack = 60) => collectNights(queries, progress, daysBack);
