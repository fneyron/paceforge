import { ORIGIN, trusted } from './api';

export type OfflinePoint = {
  name: string; km: number; clock_s: number | null; kind: 'none' | 'water' | 'full' | 'base';
  crew: boolean; drop_bag: boolean; cutoff_clock: string | null; cutoff_margin_s: number | null;
  pinned: boolean; passed: boolean;
};
export type OfflinePlan = {
  version: 1; user_id: number; route_id: number; name: string; saved_at: string; race_date: string | null;
  distance_km: number; elevation_m: number; start_s: number; planned: boolean; points: OfflinePoint[];
};
export type OfflineLibrary = { userId: number | null; plans: OfflinePlan[] };
const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const id = (v: unknown): v is number => finite(v) && Number.isSafeInteger(v) && v > 0;
const short = (v: unknown): v is string => typeof v === 'string' && v.length <= 255;

/** Validate and select fields: never persist arbitrary bridge data or HTML. */
export function readOfflinePlan(data: unknown): OfflinePlan | null {
  if (!data || typeof data !== 'object') return null;
  const p = data as OfflinePlan;
  if (p.version !== 1 || !id(p.user_id) || !id(p.route_id) || !short(p.name)
    || !short(p.saved_at) || !Number.isFinite(Date.parse(p.saved_at))
    || !(p.race_date === null || (short(p.race_date) && /^\d{4}-\d{2}-\d{2}$/.test(p.race_date)))
    || !finite(p.distance_km) || p.distance_km < 0 || !finite(p.elevation_m) || p.elevation_m < 0
    || !finite(p.start_s) || p.start_s < 0 || p.start_s >= 86400 || typeof p.planned !== 'boolean'
    || !Array.isArray(p.points) || p.points.length > 1000) return null;
  const points: OfflinePoint[] = [];
  for (const point of p.points) {
    if (!point || !short(point.name) || !finite(point.km) || point.km < 0
      || !(point.clock_s === null || (finite(point.clock_s) && point.clock_s >= 0))
      || !['none', 'water', 'full', 'base'].includes(point.kind)
      || !(point.cutoff_clock === null || (short(point.cutoff_clock) && /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(point.cutoff_clock)))
      || !(point.cutoff_margin_s === null || finite(point.cutoff_margin_s))
      || ['crew', 'drop_bag', 'pinned', 'passed'].some(key => typeof point[key as keyof OfflinePoint] !== 'boolean')) return null;
    const { name, km, clock_s, kind, crew, drop_bag, cutoff_clock, cutoff_margin_s, pinned, passed } = point;
    points.push({ name, km, clock_s, kind, crew, drop_bag, cutoff_clock, cutoff_margin_s, pinned, passed });
  }
  const { version, user_id, route_id, name, saved_at, race_date, distance_km, elevation_m, start_s, planned } = p;
  return { version, user_id, route_id, name, saved_at, race_date, distance_km, elevation_m, start_s, planned, points };
}

/** The copies once a page reports who is signed in. A sign-in page (`null`: the session
 * expired, or was closed elsewhere) keeps them: a runner opening the app on race morning
 * with a dead session still finds his plan. Only another account replaces them here; an
 * explicit logout (forget) and « Supprimer » (remove) are the other two ways out. */
export function reconcileAccount(library: OfflineLibrary, uid: number | null): OfflineLibrary {
  return uid === null || library.userId === uid ? library : { userId: uid, plans: [] };
}

export function withPlan(library: OfflineLibrary, plan: OfflinePlan): OfflineLibrary {
  const previous = library.userId === plan.user_id ? library.plans : [];
  const plans = [plan, ...previous.filter(p => p.route_id !== plan.route_id)];
  if (plans.length > 20 || JSON.stringify(plans).length > 1000000) throw new Error('Supprime une copie hors connexion avant d’en ajouter une autre.');
  return { userId: plan.user_id, plans };
}

export function clockLabel(seconds: number | null): string {
  if (seconds === null) return '—';
  const minutes = Math.floor(seconds / 60), days = Math.floor(minutes / 1440);
  return `${String(Math.floor(minutes / 60) % 24).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}${days ? ` · J+${days}` : ''}`;
}

export type OfflineRequest = { nonce: string; url: string; routeId?: number };
export function offlineReply(request: OfflineRequest | null, raw: string, url: string): Record<string, unknown> | null {
  if (!request || !trusted(url) || url !== request.url || raw.length > 256000) return null;
  try {
    const data = JSON.parse(raw);
    if (data?.nonce !== request.nonce || data.type !== (request.routeId ? 'offline-plan' : 'offline-account')) return null;
    return data;
  } catch { return null; }
}

export function offlineAccountScript(nonce: string): string {
  return `if (location.origin === ${JSON.stringify(ORIGIN)}) {
    document.documentElement.classList.add('pf-offline');
    const account = document.querySelector('[data-account]');
    // Public help/privacy pages omit identity even when the session is signed in.
    if (account || ['/', '/auth/login', '/auth/register', '/auth/check-email'].includes(location.pathname)) {
      window.ReactNativeWebView.postMessage(JSON.stringify({type:'offline-account',page_url:location.href,nonce:${JSON.stringify(nonce)},user_id:Number(account?.dataset.account)||null}));
    }
  } true;`;
}

export function offlineFetchScript(task: OfflineRequest): string {
  return `(async () => {
    if (location.origin !== ${JSON.stringify(ORIGIN)}) return;
    const send = data => window.ReactNativeWebView.postMessage(JSON.stringify({type:'offline-plan',nonce:${JSON.stringify(task.nonce)},...data}));
    try {
      const response = await fetch(${JSON.stringify(ORIGIN + '/api/simulator/routes/' + task.routeId + '/offline')}, {credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(25000)});
      if (!response.ok || response.redirected) throw new Error();
      const text = await response.text();
      if (text.length > 256000) throw new Error();
      send({plan:JSON.parse(text)});
    } catch { send({failed:true}); }
  })(); true;`;
}
