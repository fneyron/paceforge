import { trusted } from './api';

export const providers = ['coros', 'garmin', 'strava'] as const;
export type Provider = typeof providers[number];
export type ConnectionState = 'connected' | 'disconnected' | 'reconnect' | 'syncing' | 'attention';
/** What a connection's screen prints under its state: the last sync, a short label when it needs a look. */
export type ConnectionDetail = { lastSync: string | null; notice: string | null };
export type Connections = { user_id: number; name: string; providers: Record<Provider, ConnectionState>;
  details: Record<Provider, ConnectionDetail> };
/** The two actions of a connection's screen, and the link of COROS's or Strava's sign-in sheet. */
export type ConnectionAction = { provider: Provider; kind: 'sync' | 'disconnect' | 'link' };
export const providerNames: Record<Provider, string> = { coros: 'COROS', garmin: 'Garmin', strava: 'Strava' };
const states: ConnectionState[] = ['connected', 'disconnected', 'reconnect', 'syncing', 'attention'];
const empty: ConnectionDetail = { lastSync: null, notice: null };

function detail(value: unknown): ConnectionDetail {
  if (!value || typeof value !== 'object') return empty;
  const d = value as Record<string, unknown>;
  const lastSync = typeof d.last_sync === 'string' && Number.isFinite(Date.parse(d.last_sync)) ? d.last_sync : null;
  const notice = typeof d.notice === 'string' && d.notice.length <= 200 ? d.notice : null;
  return { lastSync, notice };
}

export function readConnections(value: unknown, url: string, nonce: string): Connections | null {
  if (!trusted(url, '/mobile/connections') || !value || typeof value !== 'object') return null;
  const data = value as Record<string, unknown>;
  if (data.type !== 'connections' || data.nonce !== nonce || !Number.isSafeInteger(data.user_id) ||
      typeof data.name !== 'string' || !data.providers || typeof data.providers !== 'object') return null;
  const status = data.providers as Record<Provider, ConnectionState>;
  if (!providers.every(key => states.includes(status[key]))) return null;
  // An older server sends no details: the screens then print the state alone.
  const details = (data.details && typeof data.details === 'object' ? data.details : {}) as Record<string, unknown>;
  return { user_id: data.user_id as number, name: data.name, providers: status,
    details: Object.fromEntries(providers.map(key => [key, detail(details[key])])) as Record<Provider, ConnectionDetail> };
}

/** An action's answer: its short result, and for « link » the sign-in sheet's page (PaceForge's, for that provider). */
export function readActionReply(value: unknown, action: ConnectionAction): { result: string | null; link: string | null } {
  const data = (value && typeof value === 'object' ? value : {}) as Record<string, unknown>;
  const result = typeof data.result === 'string' && data.result.length <= 200 ? data.result : null;
  const link = action.kind === 'link' && typeof data.link === 'string' &&
    trusted(data.link, `/mobile/oauth/${action.provider}`) ? data.link : null;
  return { result, link };
}

export function connectionLabel(state?: ConnectionState) {
  switch (state) {
    case 'connected': return 'Connecté';
    case 'syncing': return 'Synchronisation en cours';
    case 'reconnect': return 'À reconnecter';
    case 'attention': return 'Synchronisation à vérifier';
    case 'disconnected': return 'Non connecté';
    default: return 'État à vérifier';
  }
}

/** The outcome code the sign-in sheet closes with (paceforge://devices?connection=…&result=…), said in French. */
export function signInResult(url: string | null, provider: Provider): string {
  let code = '';
  try { code = url ? new URL(url).searchParams.get('result') || '' : ''; } catch { /* no code */ }
  return { ok: `${providerNames[provider]} connecté. Tes données arrivent.`, cancelled: 'Connexion annulée.',
    failed: 'La connexion n’a pas abouti. Réessaie.', expired: 'Le lien a expiré. Réessaie.' }[code] ?? '';
}
