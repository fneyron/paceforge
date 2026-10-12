import { trusted } from './api';

export const providers = ['coros', 'garmin', 'strava'] as const;
export type Provider = typeof providers[number];
export type ConnectionState = 'connected' | 'disconnected' | 'reconnect' | 'syncing' | 'attention';
export type Connections = { user_id: number; name: string; providers: Record<Provider, ConnectionState> };
const states: ConnectionState[] = ['connected', 'disconnected', 'reconnect', 'syncing', 'attention'];

export function readConnections(value: unknown, url: string, nonce: string): Connections | null {
  if (!trusted(url, '/mobile/connections') || !value || typeof value !== 'object') return null;
  const data = value as Record<string, unknown>;
  if (data.type !== 'connections' || data.nonce !== nonce || !Number.isSafeInteger(data.user_id) ||
      typeof data.name !== 'string' || !data.providers || typeof data.providers !== 'object') return null;
  const status = data.providers as Record<Provider, ConnectionState>;
  if (!providers.every(key => states.includes(status[key]))) return null;
  return { user_id: data.user_id as number, name: data.name, providers: status };
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
