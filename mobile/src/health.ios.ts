import { isHealthDataAvailable, requestAuthorization, queryQuantitySamples, queryCategorySamples, queryStatisticsForQuantity } from '@kingstinct/react-native-healthkit';
import * as Crypto from 'expo-crypto';
import { collectDaily, collectNights, identifiers } from './health-collect';

const queries = { queryQuantitySamples, queryCategorySamples, queryStatisticsForQuantity,
  digest: (key: string) => Crypto.digestStringAsync(Crypto.CryptoDigestAlgorithm.SHA256, key) };

export async function authorize(): Promise<void> {
  if (!isHealthDataAvailable()) throw new Error('Apple Santé n’est pas disponible sur cet appareil.');
  await requestAuthorization({ toRead: [...Object.values(identifiers), 'HKCategoryTypeIdentifierSleepAnalysis', 'HKQuantityTypeIdentifierHeartRateVariabilitySDNN'] });
}
export const collect = (progress: (s: string) => void, daysBack = 30) => collectDaily(queries, progress, daysBack);
export const collectSleep = (progress: (s: string) => void, daysBack = 60) => collectNights(queries, progress, daysBack);
