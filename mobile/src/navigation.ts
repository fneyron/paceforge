import { ORIGIN, trusted } from './api';

export type Tab = 'health' | 'activities' | 'courses' | 'devices';
export const tabs = [['health', 'Santé'], ['activities', 'Activités'], ['courses', 'Courses']] as const;
export const destinations: Record<Exclude<Tab, 'devices'>, string> = { health: '/sante', activities: '/activities', courses: '/simulator' };
export function isBridgeUrl(url: string): boolean {
  return trusted(url) && ['/mobile/session', '/mobile/connections', '/mobile/connect'].includes(new URL(url).pathname);
}
export function screenUrl(url: string): string {
  return trusted(url) && !isBridgeUrl(url) ? url : ORIGIN + '/sante';
}
export function refreshable(url: string): boolean {
  if (!trusted(url)) return false;
  const path = new URL(url).pathname;
  // No refresh gesture on account forms or unsaved course planning.
  return path === '/sante' || path === '/activities';
}
export function tabForUrl(url: string): Tab | null {
  if (!trusted(url)) return null;
  const path = new URL(url).pathname;
  if (path === '/accueil') return 'health';
  if (path === '/sante' || path.startsWith('/sante/')) return 'health';
  if (path === '/activities' || path.startsWith('/activity/')) return 'activities';
  if (path === '/simulator' || path.startsWith('/simulator/')) return 'courses';
  if (path === '/settings' || path.startsWith('/connections/') || path.startsWith('/mobile/')) return 'devices';
  return null;
}

// The same website retains its complete navigation in Safari. Inside the app,
// native navigation owns the header and tabs; account/theme controls live in Settings.
export const appShellScript = `
  if (location.origin === ${JSON.stringify(ORIGIN)} && !document.getElementById('paceforge-native-shell')) {
    const style = document.createElement('style');
    style.id = 'paceforge-native-shell';
    style.textContent = '.pf-tabbar,.pf-top{display:none!important}';
    (document.head || document.documentElement).appendChild(style);
  }
  true;
`;
