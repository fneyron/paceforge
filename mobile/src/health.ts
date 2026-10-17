// TypeScript fallback. Metro resolves .ios.ts / .android.ts for native builds;
// health-platforms.ts checks at compile time that both offer this same API.
import type { ChangePlan, HealthResult, Metric, ReadBudget, SleepResult } from './types';
export const backgroundTypes: readonly string[] = [];
export async function authorize(_background = false): Promise<void> { throw new Error('Installe une version native iPhone ou Android pour lire les données de santé.'); }
export async function collect(_progress: (s: string) => void, _daysBack = 30, _budget?: ReadBudget, _metrics?: Metric[]): Promise<HealthResult> { await authorize(); return { days: [], notices: [], complete: false }; }
export async function collectSleep(_progress: (s: string) => void, _daysBack = 60, _budget?: ReadBudget): Promise<SleepResult> { return { nights: [], notices: [], complete: true }; }
/** A permission sheet is due before reading (`manual`: the « Synchroniser » button). */
export async function authorizationNeeded(_manual = false): Promise<boolean> { return false; }
export async function protectedDataAvailable(): Promise<boolean> { return true; }
export async function hasBackgroundAccess(): Promise<boolean> { return false; }
export async function enableHealthBackground(): Promise<void> {}
export async function disableHealthBackground(): Promise<void> {}
export function watchHealthChanges(_onChange: () => void, _onError?: (type: string, message: string) => void): () => void { return () => {}; }
export async function changedMetrics(tokens: Record<string, string>, _budget?: ReadBudget): Promise<ChangePlan> { return { daysBack: 0, tokens }; }
