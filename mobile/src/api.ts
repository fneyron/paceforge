export const ORIGIN = 'https://paceforge.fr';
export async function api(path: string, method: string, body?: unknown, token?: string) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 25000);
  try {
    const response = await fetch(ORIGIN + path, { method, signal: controller.signal, headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) }, body: body === undefined ? undefined : JSON.stringify(body) });
    if (!response.ok) throw new Error(response.status === 401 ? 'Association expirée. Reconnecte ce téléphone.' : `Envoi impossible (${response.status}). Réessaie dans quelques instants.`);
    return await response.json();
  } catch (e) {
    if (e instanceof Error && e.name === 'AbortError') throw new Error('Le serveur ne répond pas. Réessaie lorsque la connexion est disponible.');
    throw e;
  } finally { clearTimeout(timer); }
}
export function trusted(url: string, path?: string) {
  try { const u = new URL(url); return u.origin === ORIGIN && (!path || u.pathname === path); } catch { return false; }
}
