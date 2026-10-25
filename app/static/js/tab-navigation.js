/* The two reading tabs share one document and one authenticated web session.
   Visited views live only in this document's bounded RAM, never browser storage. */
(function () {
  'use strict';
  if (window.paceforgeTabs) return;
  var main = document.getElementById('main-content');
  if (!main?.dataset.account) return;
  var account = main.dataset.account, build = main.dataset.build, saved = new Map(), generation = 0, revision = 0;
  var mounted = location.href, controller = null, loading = null;
  var day = new Date().toDateString(), MAX_CHARS = 2000000;
  function eligible(href) {
    try { var u = new URL(href, location.href); return u.origin === location.origin && ['/sante', '/activities'].includes(u.pathname); }
    catch (_) { return false; }
  }
  if (!eligible(location.href)) return;
  function canonical(href) { var u = new URL(href, location.href); u.searchParams.delete('app_nav'); return u.pathname + u.search + u.hash; }
  function path(href) { return new URL(href, location.href).pathname; }
  function invalidate() { saved.clear(); revision++; }
  function cancel() { generation++; controller?.abort(); progress(false); }
  function clear() { invalidate(); cancel(); }
  function rememberLocation() {
    if (!loading && eligible(location.href)) {
      mounted = location.href;
      window.ReactNativeWebView?.postMessage(JSON.stringify({ type: 'tab-location', url: location.href, page_url: location.href }));
    }
  }
  function remember(force) {
    var current = document.getElementById('main-content');
    if (!current || (!force && current.hidden) || current.dataset.account !== account) return;
    var clone = current.cloneNode(true);
    // Clones have neither JS listeners nor PFViz's instance properties. The
    // initialized document is disposed below; the clone is rehydrated on return.
    if (clone.outerHTML.length > MAX_CHARS) { saved.delete(path(mounted)); return; }
    saved.set(path(mounted), { url: canonical(mounted), main: clone,
      state: window.PFPageState?.capture(), title: document.title, at: Date.now() });
  }
  function progress(active) {
    var current = document.getElementById('main-content');
    if (current) { current.hidden = active; current.setAttribute('aria-busy', String(active)); }
    if (active && !loading) {
      // real text for a screen reader (an aria-label on a status is not read), written once the region is in place
      loading = document.createElement('div'); loading.setAttribute('role', 'status');
      loading.className = 'pf-refresh-status is-loading';
      var said = document.createElement('span'); said.className = 'sr-only'; loading.appendChild(said);
      loading.style.cssText = 'margin:32px auto;min-height:44px'; current?.after(loading);
      setTimeout(function () { said.textContent = 'Chargement…'; }, 0);
    } else if (!active && loading) { loading.remove(); loading = null; }
  }
  function notify(nonce, url, status) {
    if (nonce) window.ReactNativeWebView?.postMessage(JSON.stringify({ type: 'tab-navigation', nonce: nonce, url: url, page_url: location.href, status: status }));
  }
  function updateNavigation() {
    document.querySelectorAll('.pf-nav a,.pf-tabbar a').forEach(function (a) {
      if (path(a.href) === location.pathname) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    });
  }
  function mount(next, target, state, title, push) {
    var old = document.getElementById('main-content');
    window.PFViz?.dispose(old);
    if (window.htmx) window.htmx.remove(old); else old.remove();
    if (push) history.pushState(null, '', target);
    mounted = location.href;
    if (title) document.title = title;
    next.hidden = false; next.removeAttribute('aria-busy');
    var nav = document.querySelector('.pf-tabbar'); document.body.insertBefore(next, nav || null);
    progress(false);
    window.paceforgeConfigurePage?.();
    window.htmx?.process(next); window.PFViz?.scan(next); window.lucide?.createIcons();
    updateNavigation();
    window.PFPageState?.restore(state || null, !location.hash || new URL(target).searchParams.has('app_nav'));
    if (!state && !location.hash) scrollTo({ top: 0, behavior: 'instant' });
    // the new tab's content takes the focus (it stayed on the tab link, now in the other page): a screen reader
    // reads the new page from its start, the scroll stays where it was restored
    try { next.focus({ preventScroll: true }); } catch (_) {}
    window.paceforgeRefresh?.loaded();
    document.dispatchEvent(new CustomEvent('pf:tabchange'));
  }
  function hardNavigation(target) {
    clear(); generation++; controller?.abort(); progress(true);
    // Re-enter the server's normal authenticated navigation. An old RAM view
    // must not survive a different account or an expired web session.
    location.replace(target);
  }
  async function get(target, ticket) {
    var observed = revision;
    controller = new AbortController(); var active = controller;
    var timeout = setTimeout(function () { active.abort(); }, 20000);
    try {
      var response = await fetch(target, { credentials: 'same-origin', cache: 'no-store',
        headers: { Accept: 'text/html' }, signal: active.signal });
      if (ticket !== generation) return null;
      if (response.redirected || [401, 403].includes(response.status)) { hardNavigation(response.url || target); return null; }
      if (!response.ok) throw new Error('navigation-unavailable');
      var doc = new DOMParser().parseFromString(await response.text(), 'text/html');
      if (ticket !== generation) return null;
      var next = doc.getElementById('main-content');
      if (!next || next.dataset.account !== account) { hardNavigation(response.url || target); return null; }
      // A deployment may change the HTML/JS contract while this shell is open.
      if (build && next.dataset.build && next.dataset.build !== build) { hardNavigation(target); return null; }
      if (observed !== revision) return get(target, ticket);
      return doc;
    } finally { clearTimeout(timeout); }
  }
  async function revalidate(target, ticket) {
    var requestedCheckin = document.querySelector('#main-content #ressenti');
    try {
      var doc = await get(target, ticket);
      if (!doc || ticket !== generation || canonical(location.href) !== canonical(target)) return;
      var observed = revision;
      await window.paceforgeRefresh?.applyDocument(doc, location.href, requestedCheckin,
        function () { return ticket === generation && observed === revision; });
      if (ticket !== generation) return;
      window.paceforgeRefresh?.loaded();
      document.dispatchEvent(new CustomEvent('pf:tabupdated'));
    } catch (_) { /* A failed quiet refresh keeps the dated, already visible view. */ }
  }
  async function waitCheckin(ticket) {
    var until = Date.now() + 20000;
    while (document.querySelector('#ressenti.htmx-request')) {
      if (ticket !== generation || Date.now() >= until) return false;
      await new Promise(function (resolve) { setTimeout(resolve, 30); });
    }
    return ticket === generation;
  }
  async function beforeLeave() {
    if (!await waitCheckin(generation)) throw new Error('checkin-save-pending');
  }
  async function navigate(href, nonce, push) {
    if (!eligible(href) || !eligible(mounted)) return false;
    var target = new URL(href, location.href).href;
    if (new Date().toDateString() !== day) { clear(); day = new Date().toDateString(); }
    var current = document.getElementById('main-content');
    if (!current || current.dataset.account !== account) { hardNavigation(target); return true; }
    if (push !== false && !loading) mounted = location.href;
    var ticket = ++generation; controller?.abort();
    if (document.querySelector('#ressenti.htmx-request')) {
      progress(true);
      if (!await waitCheckin(ticket)) {
        if (ticket === generation) { progress(false); notify(nonce, target, 'cancelled'); }
        return true;
      }
      remember(true);
    } else remember();
    var entry = saved.get(path(target));
    if (entry && entry.url === canonical(target) && Date.now() - entry.at < 300000) {
      saved.delete(path(target));
      mount(entry.main, target, entry.state, entry.title, push !== false);
      notify(nonce, target, 'ready');
      void revalidate(target, ticket);
      return true;
    }
    progress(true);
    try {
      var doc = await get(target, ticket);
      if (!doc || ticket !== generation) return true;
      mount(doc.getElementById('main-content'), target, null, doc.title, push !== false);
      notify(nonce, target, 'ready');
    } catch (error) {
      if (ticket === generation && error.name !== 'AbortError') hardNavigation(target);
      else if (ticket === generation) hardNavigation(target);
    }
    return true;
  }
  window.paceforgeTabs = { navigate: navigate, clear: clear, cancel: cancel, beforeLeave: beforeLeave, invalidate: invalidate, rememberLocation: rememberLocation, epoch: function () { return generation; } };
  document.addEventListener('click', function (event) {
    if (event.target.closest('[data-range]')) rememberLocation();
    var a = event.target.closest('a[href]');
    if (!a || event.defaultPrevented || event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || a.target || a.hasAttribute('download') || a.hasAttribute('hx-get')) return;
    if (!eligible(a.href)) return;
    var next = new URL(a.href);
    if (next.pathname === location.pathname && next.search === location.search) return;
    if (canonical(a.href) === canonical(location.href) && !loading) return;
    event.preventDefault(); void navigate(a.href);
  });
  window.addEventListener('hashchange', rememberLocation);
  window.addEventListener('popstate', function () {
    var previous = new URL(mounted);
    if (previous.pathname === location.pathname && previous.search === location.search) { rememberLocation(); return; }
    if (eligible(location.href)) void navigate(location.href, undefined, false);
    else clear();
  });
  window.addEventListener('pagehide', clear);
})();
