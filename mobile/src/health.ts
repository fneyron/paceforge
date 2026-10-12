// TypeScript fallback. Metro resolves .ios.ts / .android.ts for native builds.
import type { HealthResult, SleepResult } from './types';
export async function authorize(): Promise<void> { throw new Error('Installe une version native iPhone ou Android pour lire les données de santé.'); }
export async function collect(_progress: (s: string) => void, _daysBack = 30): Promise<HealthResult> { await authorize(); return { days: [], notices: [], complete: false }; }
export async function collectSleep(_progress: (s: string) => void, _daysBack = 60): Promise<SleepResult> { return { nights: [], notices: [], complete: true }; }
