// TypeScript fallback. Metro resolves .ios.ts / .android.ts for native builds.
import type { HealthResult } from './types';
export async function authorize(): Promise<void> { throw new Error('Installe une version native iPhone ou Android pour lire les données de santé.'); }
export async function collect(_progress: (s: string) => void): Promise<HealthResult> { await authorize(); return { days: [], notices: [] }; }
