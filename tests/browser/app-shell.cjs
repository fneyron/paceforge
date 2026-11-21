// The phone app, since its version 1.1.0, tells every page that it has the native Coaching tab (mobile/src/navigation.ts,
// appShellScript: window.paceforgeNativeCoaching and .pf-native-coaching on <html>, set at the document's start). The
// pages then leave out what only an app installed before that tab needs: the entry card at the top of Activités, the
// way back « ‹ Activités ». A test that shows a page as the app does adds this next to its ReactNativeWebView stub:
//   await page.addInitScript(coachingTab);
// What an older app shows instead is covered by coaching-visuals.cjs.
exports.coachingTab = () => {
  window.paceforgeNativeCoaching = 1;
  const mark = () => document.documentElement.classList.add('pf-native-coaching');
  if (document.documentElement) mark();
  else new MutationObserver((_, observer) => { if (document.documentElement) { observer.disconnect(); mark(); } }).observe(document, { childList: true });
};
