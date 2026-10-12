import { ORIGIN, trusted } from './api';

export type Tab = 'health' | 'activities' | 'courses' | 'devices';
export const tabs = [['health', 'Santé'], ['activities', 'Activités'], ['courses', 'Courses'], ['devices', 'Appareils']] as const;
export const destinations: Record<Exclude<Tab, 'devices'>, string> = { health: '/sante', activities: '/activities', courses: '/simulator' };
export function tabForUrl(url: string): Tab | null {
  if (!trusted(url)) return null;
  const path = new URL(url).pathname;
  if (path === '/sante' || path.startsWith('/sante/')) return 'health';
  if (path === '/activities' || path.startsWith('/activity/')) return 'activities';
  if (path === '/simulator' || path.startsWith('/simulator/')) return 'courses';
  if (path === '/settings' || path.startsWith('/connections/') || path.startsWith('/mobile/')) return 'devices';
  return null;
}

// The same website retains its complete navigation in Safari. Inside the app,
// native tabs own navigation; the web toolbar keeps profile/theme controls.
export const appShellScript = `
  if (location.origin === ${JSON.stringify(ORIGIN)} && !document.getElementById('paceforge-native-shell')) {
    const style = document.createElement('style');
    style.id = 'paceforge-native-shell';
    style.textContent = '.pf-tabbar,.pf-nav,.pf-top .pf-logo{display:none!important}.pf-top-in{justify-content:flex-end;height:42px}';
    (document.head || document.documentElement).appendChild(style);
  }
  true;
`;
