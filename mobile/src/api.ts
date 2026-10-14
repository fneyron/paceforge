export const ORIGIN = 'https://paceforge.fr';
export class ApiError extends Error {
  constructor(public status: number, field = '', public retryAfter = 0) {
    super(status === 401 ? 'Connexion expirée. Reconnecte Apple Santé ou Health Connect.' : status === 422 ?
      `Mesures refusées par PaceForge (422${field ? ` · ${field}` : ''}).` : `Envoi impossible (${status}). Réessaie dans quelques instants.`);
  }
  /** Too many requests or a server failure: retry later, never a reason to unpair. */
  get temporary() { return this.status === 429 || this.status >= 500; }
}
// FastAPI validation responses may contain raw measurements in `input`.
// Expose only a fixed label derived from field names, never that payload.
export function rejectedField(raw: unknown): string {
  const labels: Record<string, string> = { date: 'date', measured_at: 'date de mesure', value: 'valeur', unit: 'unité',
    sources: 'application source', source_id: 'source du sommeil', source_name: 'application source', timezone: 'fuseau horaire',
    sleep: 'sommeil', naps: 'siestes', hr: 'fréquence cardiaque', hrv_sdnn: 'VFC', resp: 'respiration' };
  const details = (raw as { detail?: unknown } | null)?.detail;
  if (!Array.isArray(details)) return '';
  for (const error of details) {
    if (!Array.isArray(error?.loc)) continue;
    for (const field of [...error.loc].reverse()) if (typeof field === 'string' && Object.hasOwn(labels, field)) return labels[field];
  }
  return '';
}
export type RequestOptions = { timeout?: number; headers?: Record<string, string> };
export async function api(path: string, method: string, body?: unknown, token?: string, options: RequestOptions = {}) {
  const controller = new AbortController();
  // Background runs pass a shorter delay: iOS suspends the app about 25 s after a HealthKit wake.
  const timer = setTimeout(() => controller.abort(), options.timeout ?? 25000);
  try {
    const response = await fetch(ORIGIN + path, { method, signal: controller.signal, headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}), ...options.headers }, body: body === undefined ? undefined : JSON.stringify(body) });
    if (!response.ok) {
      const field = response.status === 422 ? rejectedField(await response.json().catch(() => null)) : '';
      const retryAfter = response.status === 429 ? Number(response.headers?.get?.('Retry-After')) || 0 : 0;
      throw new ApiError(response.status, field, retryAfter);
    }
    return await response.json();
  } catch (e) {
    if (e instanceof Error && e.name === 'AbortError') throw new Error('Le serveur ne répond pas. Réessaie lorsque la connexion est disponible.');
    throw e;
  } finally { clearTimeout(timer); }
}
export function trusted(url: string, path?: string) {
  try { const u = new URL(url); return u.origin === ORIGIN && (!path || u.pathname === path); } catch { return false; }
}

export type Credentials = { token: string; prev_token?: string };
/** A 401 right after a rotation can mean the server never stored the new token:
 * retry once with the previous one, and keep using it when the server still knows it. */
export async function authorized<T>(pair: Credentials, request: (token: string) => Promise<T>,
  keep: (credentials: Credentials) => Promise<void>): Promise<T> {
  let result: T;
  try {
    result = await request(pair.token);
  } catch (e) {
    if (!(e instanceof ApiError) || e.status !== 401 || !pair.prev_token || pair.prev_token === pair.token) throw e;
    result = await request(pair.prev_token);
    await keep({ token: pair.prev_token }).catch(() => {});
    return result;
  }
  // The server retired the previous token on this first use of the new one.
  if (pair.prev_token) await keep({ token: pair.token }).catch(() => {});
  return result;
}

const ROTATE_EVERY = 30 * 24 * 3600000;
export function rotationDue(rotatedAt: string | undefined, now = Date.now()): boolean {
  const at = Date.parse(rotatedAt || '');
  return !Number.isFinite(at) || at > now + 300000 || now - at >= ROTATE_EVERY;
}
/** Foreground only. The server keeps the asking token valid while it is still used
 * (7 days after its last use), until the new one is first used: failing to store the
 * new token never unpairs the phone. */
export async function rotateToken<P extends Credentials & { user_id: number }>(pair: P,
  store: (next: P & { rotated_at: string }) => Promise<void>, send: typeof api = api, now = () => new Date().toISOString()) {
  const r = await send('/api/mobile/token', 'POST', undefined, pair.token, { timeout: 15000 }) as { token?: unknown; user_id?: unknown } | null;
  if (typeof r?.token !== 'string' || !/^[A-Za-z0-9_-]{43,128}$/.test(r.token) || r.user_id !== pair.user_id) {
    throw new Error('Nouveau jeton refusé.');
  }
  const next = { ...pair, token: r.token, prev_token: pair.token, rotated_at: now() };
  await store(next);
  return next;
}
