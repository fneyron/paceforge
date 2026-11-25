import { ORIGIN, trusted } from './api';

export type Tab = 'health' | 'activities' | 'courses' | 'coaching' | 'devices';
// The site's order (its header and its phone bar): Santé first, the app's first screen, then Coaching, Courses, Activités.
export const tabs = [['health', 'Santé'], ['coaching', 'Coaching'], ['courses', 'Courses'], ['activities', 'Activités']] as const;
export const destinations: Record<Exclude<Tab, 'devices'>, string> = { health: '/sante', activities: '/activities', courses: '/simulator', coaching: '/coaching' };
export function prepareNavigationScript(nonce: string): string {
  return `Promise.resolve().then(() => window.paceforgeBeforeLeave?.()).then(() => window.paceforgeTabs?.beforeLeave?.()).then(() => {
    window.paceforgeRememberPage?.();
    window.ReactNativeWebView.postMessage(JSON.stringify({type:'navigation-ready',nonce:${JSON.stringify(nonce)},page_url:location.href,tabs_ready:typeof window.paceforgeTabs?.navigate==='function' && typeof window.paceforgeTabs?.cancel==='function' && typeof window.paceforgeTabs?.beforeLeave==='function',ok:true}));
  }).catch(() => window.ReactNativeWebView.postMessage(JSON.stringify({type:'navigation-ready',nonce:${JSON.stringify(nonce)},page_url:location.href,ok:false}))); true;`;
}
// Older server documents report an explicit fallback. The native save nonce
// remains separate from the web navigation nonce and is still checked first.
export function tabNavigationScript(destination: string, nonce: string): string {
  return `(async function () {
    const fallback = () => window.ReactNativeWebView.postMessage(JSON.stringify({type:'tab-navigation',nonce:${JSON.stringify(nonce)},url:${JSON.stringify(destination)},page_url:location.href,status:'fallback'}));
    try { if (typeof window.paceforgeTabs?.navigate !== 'function' || typeof window.paceforgeTabs?.cancel !== 'function' || typeof window.paceforgeTabs?.beforeLeave !== 'function' || !await window.paceforgeTabs.navigate(${JSON.stringify(destination)}, ${JSON.stringify(nonce)})) fallback(); }
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
  // No refresh gesture on account forms or unsaved course planning. Coaching has its own rule (reloadable).
  return path === '/sante' || path === '/activities';
}
// Coaching reads its plan again each time its page loads (the day, the new activities): there the pull gesture
// reloads the page itself, with no phone or provider sync and no soft replacement. Not on « Refaire le plan »,
// a form with answers to keep.
export function reloadable(url: string): boolean {
  if (!trusted(url)) return false;
  const u = new URL(url);
  return (u.pathname === '/coaching' && u.searchParams.get('refaire') !== '1') || u.pathname === '/coaching/athletes' ||
    /^\/coaching\/athletes\/\d+$/.test(u.pathname);
}
// The one link handed to the system that is not https: the Coaching calendar feed, so that Calendar offers the
// subscription. Exactly webcal://<the site's host>/coaching/cal/<token>.ics — no port, credentials, query or
// fragment, no other path — and nothing else gains a scheme exception. The WebView only asks about this origin
// (calendarOrigin in its originWhitelist); this check decides.
export const calendarOrigin = 'webcal://' + new URL(ORIGIN).host;
export function calendarFeedUrl(url: string): string | null {
  return typeof url === 'string' && url.startsWith(calendarOrigin + '/') &&
    /^\/coaching\/cal\/[A-Za-z0-9_-]{32,128}\.ics$/.test(url.slice(calendarOrigin.length)) ? url : null;
}
// Coaching › « Mon agenda »: the sign-in sheet of Google Agenda or Outlook. Exactly the site's
// /mobile/oauth/agenda/<google|microsoft> with a one-time ticket in its fragment (no query); nothing else opens a sheet
// from a page. The sheet closes on paceforge://agenda?connexion=…&resultat=…, a pair of codes « Mon agenda » says in
// one line (agendaReturnUrl): never a message, any page can link to the scheme.
const agendaProviders = ['google', 'microsoft'];
const agendaResults = ['ok', 'annule', 'expire', 'refus', 'organisation', 'panne'];
export const agendaScheme = 'paceforge://agenda';
export function agendaSheetUrl(url: string): { provider: string; url: string } | null {
  if (!trusted(url)) return null;
  const u = new URL(url);
  const match = /^\/mobile\/oauth\/agenda\/(google|microsoft)$/.exec(u.pathname);
  return match && !u.search && /^#[A-Za-z0-9_.:-]{20,512}$/.test(u.hash) ? { provider: match[1], url } : null;
}
export function agendaReturnUrl(provider: string, returned: string | null): string {
  let result = 'annule';
  try {
    const u = returned ? new URL(returned) : null;
    const code = u && returned!.startsWith(agendaScheme + '?') ? u.searchParams.get('resultat') : null;
    if (code && agendaResults.includes(code) && u!.searchParams.get('connexion') === provider) result = code;
  } catch { /* cancelled */ }
  const name = agendaProviders.includes(provider) ? provider : 'google';
  return `${ORIGIN}/coaching?outil=agenda&connexion=${name}&resultat=${result}#coach-tool-agenda`;
}
export function tabForUrl(url: string): Tab | null {
  if (!trusted(url)) return null;
  const path = new URL(url).pathname;
  if (path === '/accueil') return 'health';
  if (path === '/sante' || path.startsWith('/sante/')) return 'health';
  if (path === '/activities' || path.startsWith('/activity/')) return 'activities';
  if (path === '/simulator' || path.startsWith('/simulator/')) return 'courses';
  if (path === '/coaching' || path.startsWith('/coaching/')) return 'coaching';
  if (path === '/settings' || path.startsWith('/connections/') || path.startsWith('/mobile/')) return 'devices';
  return null;
}

// The same website retains its complete navigation in Safari. Inside the app,
// native navigation owns the header and tabs; account/theme controls live in Settings.
export const appShellScript = `
  if (location.origin === ${JSON.stringify(ORIGIN)}) {
    window.paceforgeNativeRefresh = 2;
    document.documentElement.classList.add('pf-native-refresh');
    // This shell has the native Coaching tab and hands the calendar's webcal:// link to the system: the pages hide
    // the entry card and « ‹ Activités » an older shell needs, and show « S’abonner au calendrier » (interface.css).
    window.paceforgeNativeCoaching = 1;
    document.documentElement.classList.add('pf-native-coaching');
    // It opens « Mon agenda »'s Google Agenda and Outlook sign-in in the system's sheet (Google refuses a WebView):
    // the pages show those buttons only then (an older shell keeps the address to paste).
    window.paceforgeNativeAgenda = 1;
    document.documentElement.classList.add('pf-native-agenda');
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
