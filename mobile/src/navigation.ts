import { ORIGIN, trusted } from './api';

export type Tab = 'health' | 'activities' | 'courses' | 'devices';
export const tabs = [['health', 'Santé'], ['activities', 'Activités'], ['courses', 'Courses']] as const;
export const destinations: Record<Exclude<Tab, 'devices'>, string> = { health: '/sante', activities: '/activities', courses: '/simulator' };
export function prepareNavigationScript(nonce: string): string {
  return `Promise.resolve().then(() => window.paceforgeBeforeLeave?.()).then(() => {
    window.paceforgeRememberPage?.();
    window.ReactNativeWebView.postMessage(JSON.stringify({type:'navigation-ready',nonce:${JSON.stringify(nonce)},page_url:location.href,ok:true}));
  }).catch(() => window.ReactNativeWebView.postMessage(JSON.stringify({type:'navigation-ready',nonce:${JSON.stringify(nonce)},page_url:location.href,ok:false}))); true;`;
}
// Older server documents report an explicit fallback. The native save nonce
// remains separate from the web navigation nonce and is still checked first.
export function tabNavigationScript(destination: string, nonce: string): string {
  return `(async function () {
    const fallback = () => window.ReactNativeWebView.postMessage(JSON.stringify({type:'tab-navigation',nonce:${JSON.stringify(nonce)},url:${JSON.stringify(destination)},page_url:location.href,status:'fallback'}));
    try { if (!window.paceforgeTabs || !await window.paceforgeTabs.navigate(${JSON.stringify(destination)}, ${JSON.stringify(nonce)})) fallback(); }
    catch (_) { fallback(); }
  })(); true;`;
}
// WKWebView may report the frame's original full-load request URL after a
// history change. Only this same trusted reading frame can carry its current
// logical URL; routed pairing, plan and authentication guards remain exact.
export function messagePageUrl(transport: string, logical: string, frame: string, data: { page_url?: unknown }): string {
  return refreshable(transport) && refreshable(logical) && sameScreen(transport, frame) && data?.page_url === logical
    ? logical : transport;
}
export function presentationScript(fontScale: number, healthName: string): string {
  return `window.paceforgeConfigurePage = () => {
    document.documentElement.style.fontSize = ${JSON.stringify(16 * fontScale + 'px')};
    document.documentElement.classList.toggle('pf-large-text', ${fontScale >= 1.4});
    document.querySelectorAll('[data-phone-connect]').forEach(el => el.href = 'paceforge://health');
    document.querySelectorAll('[data-phone-health]').forEach(el => el.textContent = ${JSON.stringify(healthName)});
  }; window.paceforgeConfigurePage(); true;`;
}
export function isBridgeUrl(url: string): boolean {
  return trusted(url) && ['/mobile/session', '/mobile/connections', '/mobile/connect'].includes(new URL(url).pathname);
}
export function screenUrl(url: string): string {
  return trusted(url) && !isBridgeUrl(url) ? url : ORIGIN + '/sante';
}
export function sameScreen(left: string, right: string): boolean {
  if (!trusted(left) || !trusted(right) || isBridgeUrl(left) || isBridgeUrl(right)) return false;
  const a = new URL(left), b = new URL(right);
  // Native navigation markers do not change the screen. Real filters and hash
  // destinations do: returning to a saved tab must not discard either of them.
  a.searchParams.delete('app_nav'); b.searchParams.delete('app_nav');
  return a.href === b.href;
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
  if (location.origin === ${JSON.stringify(ORIGIN)}) {
    window.paceforgeNativeRefresh = 2;
    document.documentElement.classList.add('pf-native-refresh');
    if (!window.paceforgeAppearanceObserver) {
      const reportAppearance = () => window.ReactNativeWebView?.postMessage(JSON.stringify({ type: 'appearance', dark: document.documentElement.classList.contains('dark') }));
      window.paceforgeAppearanceObserver = new MutationObserver(reportAppearance);
      window.paceforgeAppearanceObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
      reportAppearance();
    }
  }
  if (location.origin === ${JSON.stringify(ORIGIN)} && !document.getElementById('paceforge-native-shell')) {
    const style = document.createElement('style');
    style.id = 'paceforge-native-shell';
    style.textContent = '.pf-tabbar,.pf-top{display:none!important}';
    (document.head || document.documentElement).appendChild(style);
  }
  true;
`;
