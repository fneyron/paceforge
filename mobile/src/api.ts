export const ORIGIN = 'https://paceforge.fr';
export class ApiError extends Error {
  constructor(public status: number, field = '') {
    super(status === 401 ? 'Connexion expirée. Reconnecte Apple Santé ou Health Connect.' : status === 422 ?
      `Mesures refusées par PaceForge (422${field ? ` · ${field}` : ''}).` : `Envoi impossible (${status}). Réessaie dans quelques instants.`);
  }
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
export async function api(path: string, method: string, body?: unknown, token?: string) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 25000);
  try {
    const response = await fetch(ORIGIN + path, { method, signal: controller.signal, headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) }, body: body === undefined ? undefined : JSON.stringify(body) });
    if (!response.ok) {
      const field = response.status === 422 ? rejectedField(await response.json().catch(() => null)) : '';
      throw new ApiError(response.status, field);
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
