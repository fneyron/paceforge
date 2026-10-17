/* Refresh the signed-in dashboard in place. iOS owns the pull gesture; no
   touchmove interception, nested scroller, or document reload during reading. */
(function () {
  'use strict';
  if (!['/sante', '/activities'].includes(location.pathname)) return;
  var running = null, touched = false, movedAt = 0, toast, hideTimer;
  window.addEventListener('touchstart', function () { touched = true; }, { passive: true });
  function released(event) { touched = !!event?.touches?.length; movedAt = Date.now(); }
  window.addEventListener('touchend', released, { passive: true });
  window.addEventListener('touchcancel', released, { passive: true });
  window.addEventListener('blur', released);
  document.addEventListener('visibilitychange', function () { if (document.hidden) released(); });
  window.addEventListener('scroll', function () { movedAt = Date.now(); }, { passive: true });
  function pause(ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); }
  function nativeState(active) {
    window.ReactNativeWebView?.postMessage(JSON.stringify({ type: 'refresh-state', active: active }));
  }
  function clearStatus() {
    if (toast) toast.hidden = true;
    // Clear only the web/provider result; native health has its own receipt.
    if (window.paceforgeNativeRefresh >= 2) window.ReactNativeWebView?.postMessage(JSON.stringify({ type: 'refresh-notice', message: '' }));
  }
  function status(message, retry, loading) {
    clearTimeout(hideTimer);
    document.querySelectorAll('[data-sync-issue]').forEach(function (el) { el.hidden = true; });
    // Recent native builds own the single progress/error surface. Older builds
    // and browsers retain this inline fallback, never a floating overlay.
    if (window.paceforgeNativeRefresh >= 2 && window.ReactNativeWebView) {
      if (retry) window.ReactNativeWebView.postMessage(JSON.stringify({ type: 'refresh-notice', message: message }));
      return;
    }
    if (!toast) {
      toast = document.createElement('div'); toast.className = 'pf-refresh-status';
      toast.setAttribute('role', 'status'); toast.setAttribute('aria-live', 'polite');
      document.getElementById('main-content').before(toast);
    }
    toast.replaceChildren(); toast.hidden = false;
    toast.classList.toggle('is-loading', !!loading);
    var text = document.createElement('span'); text.textContent = message; toast.appendChild(text);
    if (retry) {
      var link = document.createElement('a'); link.href = '/settings#connections'; link.textContent = 'Connexions'; toast.appendChild(link);
      var close = document.createElement('button'); close.type = 'button'; close.textContent = '×';
      close.setAttribute('aria-label', 'Fermer le message'); close.onclick = function () { toast.hidden = true; };
      toast.appendChild(close);
    } else if (!loading) hideTimer = setTimeout(function () { toast.hidden = true; }, 3000);
  }
  async function request(url, options, format) {
    var controller = new AbortController(), timer = setTimeout(function () { controller.abort(); }, 30000);
    try {
      var response = await fetch(url, Object.assign({ credentials: 'same-origin', cache: 'no-store' }, options, { signal: controller.signal }));
      if (response.redirected || response.status === 401 || response.status === 403) throw new Error('Session expirée. Reconnecte-toi dans Réglages.');
      if (!response.ok) throw new Error('Actualisation indisponible. Les dernières données restent affichées.');
      return await (format === 'json' ? response.json() : response.text());
    } catch (error) {
      if (error instanceof TypeError || error.name === 'AbortError') throw new Error('Connexion interrompue. Les dernières données restent affichées.');
      throw error;
    } finally { clearTimeout(timer); }
  }
  async function json(url, method) {
    return request(url, { method: method || 'GET', headers: { Accept: 'application/json', 'X-PaceForge-Refresh': '1' } }, 'json');
  }
  async function watches() {
    var result = await json('/sante/sync', 'POST'), errors = result.errors || [], pending = result.watches || [];
    var until = Date.now() + 120000;
    while (pending.length && Date.now() < until) {
      await pause(3000);
      var state = await json('/sante/sync-status?' + new URLSearchParams({ since: result.since, watches: pending.join(',') }));
      pending = state.pending; errors = errors.concat(state.errors || []);
    }
    // Still running is not a failed import. The server poller keeps watching.
    if (pending.length) status(pending.join(' et ') + ' : synchronisation en cours.', false, false);
    return errors;
  }
  // Keep explicitly opened disclosures and the selected DATE, not an index
  // that could point to a different measurement after a new day arrived.
  function chartKey(fig, index) {
    var panel = fig.closest('[id]'), range = fig.closest('[data-range-panel]');
    return [panel?.id || '', range?.getAttribute('data-range-panel') || '', fig.getAttribute('aria-label') || '', index].join('|');
  }
  function foldKey(el) {
    return el.id || (el.parentElement.closest('[id]')?.id || '') + ':' + el.querySelector('summary')?.textContent.trim();
  }
  function snapshot(main) {
    var folds = {}, charts = {}, anchor = null;
    main.querySelectorAll('details').forEach(function (el) { folds[foldKey(el)] = el.open; });
    main.querySelectorAll('figure[data-viz]').forEach(function (fig, i) {
      if (!fig.classList.contains('is-inspecting')) return;
      try {
        var data = JSON.parse(fig.querySelector('.pf-viz-data').textContent);
        charts[chartKey(fig, i)] = data.d[Number(fig.querySelector('.pf-viz-range').value)];
      } catch (_) { /* Charts without a date are left at rest. */ }
    });
    main.querySelectorAll('[id]').forEach(function (el) {
      var rect = el.getBoundingClientRect();
      if (rect.height && rect.top <= 100 && rect.bottom > 0 && (!anchor || rect.top > anchor.top)) anchor = { id: el.id, top: rect.top };
    });
    return { folds: folds, charts: charts, anchor: anchor, y: window.scrollY };
  }
  function keepOlderWeeks(old, next) {
    var list = next.querySelector('#activity-list'), current = old.querySelector('#activity-list');
    if (!list || !current || !list.lastElementChild) return;
    var oldest = list.lastElementChild.id, retained = false;
    current.querySelectorAll(':scope > section[id]').forEach(function (week) {
      if (week.id < oldest && !next.querySelector('#' + CSS.escape(week.id))) {
        list.appendChild(week.cloneNode(true)); retained = true;
      }
    });
    // Already opened older weeks stay a cached snapshot, with their pagination.
    if (retained && old.querySelector('#load-more') && next.querySelector('#load-more')) next.querySelector('#load-more').replaceWith(old.querySelector('#load-more').cloneNode(true));
  }
  function keepCheckin(old, next, requested) {
    var current = old.querySelector('#ressenti'), replacement = next.querySelector('#ressenti');
    if (!current || !replacement || old.dataset.account !== next.dataset.account ||
        current.elements?.day?.value !== replacement.elements?.day?.value) return;
    // Keep the live node: an in-flight HTMX save still owns it. A tap completed
    // during the GET is newer than its response, and so is the track of dots it
    // redrew. No answer goes into browser storage, and a new account or calendar
    // day never inherits one.
    if (current.classList.contains('htmx-request') || current !== requested) {
      replacement.replaceWith(current);
      var track = old.querySelector('#ressenti-courbe'), stale = next.querySelector('#ressenti-courbe');
      if (track && stale) stale.replaceWith(track);
    }
  }
  async function applyDocument(doc, href, requestedCheckin, valid) {
    if (requestedCheckin === undefined) requestedCheckin = document.querySelector('#main-content #ressenti');
    var next = doc.getElementById('main-content');
    if (!next) throw new Error('Reconnecte-toi dans Réglages pour actualiser.');
    // Never swap under a moving finger or during momentum scrolling.
    while (touched || Date.now() - movedAt < 180) await pause(100);
    if (href !== location.href || (valid && !valid())) return;
    var old = document.getElementById('main-content');
    if (old.dataset.account !== next.dataset.account) throw new Error('Le compte a changé. Ouvre Réglages puis reviens sur cette page.');
    if (old.dataset.build && next.dataset.build && old.dataset.build !== next.dataset.build) {
      window.paceforgeTabs?.clear(); location.replace(href); return;
    }
    var saved = snapshot(old);
    keepOlderWeeks(old, next);
    keepCheckin(old, next, requestedCheckin);
    next.querySelectorAll('details').forEach(function (el) {
      var key = foldKey(el);
      if (key in saved.folds) el.open = saved.folds[key];
    });
    window.PFViz?.dispose(old);
    // Let HTMX clean up its old pollers/listeners before the detached DOM goes.
    if (window.htmx) window.htmx.remove(old); else old.remove();
    var nav = document.querySelector('.pf-tabbar');
    document.body.insertBefore(next, nav || null);
    window.paceforgeConfigurePage?.();
    window.htmx?.process(next); window.PFViz?.scan(next); window.lucide?.createIcons();
    next.querySelectorAll('figure[data-viz]').forEach(function (fig, i) {
      var date = saved.charts[chartKey(fig, i)];
      if (!date) return;
      try {
        var data = JSON.parse(fig.querySelector('.pf-viz-data').textContent), index = data.d.indexOf(date);
        var input = fig.querySelector('.pf-viz-range');
        if (index >= 0 && input) { input.value = index; input.dispatchEvent(new Event('input')); }
      } catch (_) { /* An unavailable old selection returns to the latest data. */ }
    });
    var anchor = saved.anchor && document.getElementById(saved.anchor.id);
    var y = anchor ? window.scrollY + anchor.getBoundingClientRect().top - saved.anchor.top : saved.y;
    // Override the site's smooth anchor scrolling: restoration happens before paint.
    window.scrollTo({ top: y, behavior: 'instant' });
    document.dispatchEvent(new CustomEvent('pf:refreshed'));
  }
  async function replacePage() {
    var requestedCheckin = document.querySelector('#main-content #ressenti');
    var href = location.href, epoch = window.paceforgeTabs?.epoch();
    var html = await request(href, { headers: { Accept: 'text/html' } });
    await applyDocument(new DOMParser().parseFromString(html, 'text/html'), href, requestedCheckin,
      function () { return epoch === window.paceforgeTabs?.epoch(); });
  }
  function refresh(sources) {
    if (running) return running;
    window.paceforgeTabs?.invalidate();
    nativeState(true);
    if (sources) status('Actualisation…', false, true);
    running = (async function () {
      var errors = [];
      if (sources) {
        var results = await Promise.allSettled([watches(), json('/api/sync', 'POST').then(function (r) { return r.errors || []; })]);
        results.forEach(function (r) { errors = errors.concat(r.status === 'fulfilled' ? r.value : [r.reason.message]); });
      }
      if (sources) window.paceforgeTabs?.invalidate();
      try { await replacePage(); }
      catch (error) { errors.push(error.message); }
      var issue = document.querySelector('[data-sync-issue]');
      if (issue) errors.push(issue.dataset.syncIssue);
      if (errors.length) status(errors[0], true, false);
      else clearStatus();
    })().finally(function () { running = null; nativeState(false); });
    return running;
  }
  function loaded() {
    var existingIssue = document.querySelector('[data-sync-issue]');
    if (existingIssue && window.paceforgeNativeRefresh >= 2) status(existingIssue.dataset.syncIssue, true, false);
    else if (window.paceforgeNativeRefresh >= 2) clearStatus();
  }
  window.paceforgeRefresh = { refresh: refresh, applyDocument: applyDocument, loaded: loaded };
  loaded();
  document.addEventListener('htmx:beforeOnLoad', function (event) {
    var detail = event.detail;
    if (detail?.elt?.id === 'sante-sync' && detail.xhr.getResponseHeader('HX-Refresh') === 'true') {
      event.preventDefault(); refresh(false);
    }
  });
})();
