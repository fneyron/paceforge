/* Refresh the signed-in dashboard in place. iOS owns the pull gesture; no
   touchmove interception, nested scroller, or document reload during reading. */
(function () {
  'use strict';
  if (!['/sante', '/activities'].includes(location.pathname)) return;
  var running = null, touched = false, movedAt = 0, toast, hideTimer;
  window.addEventListener('touchstart', function () { touched = true; }, { passive: true });
  function released() { touched = false; movedAt = Date.now(); }
  window.addEventListener('touchend', released, { passive: true });
  window.addEventListener('touchcancel', released, { passive: true });
  window.addEventListener('scroll', function () { movedAt = Date.now(); }, { passive: true });
  function pause(ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); }
  function nativeState(active) {
    window.ReactNativeWebView?.postMessage(JSON.stringify({ type: 'refresh-state', active: active }));
  }
  function status(message, retry, loading) {
    clearTimeout(hideTimer);
    if (!toast) {
      toast = document.createElement('div'); toast.className = 'pf-refresh-status';
      toast.setAttribute('role', 'status'); toast.setAttribute('aria-live', 'polite');
      document.body.appendChild(toast);
    }
    toast.replaceChildren(); toast.hidden = false;
    toast.classList.toggle('is-loading', !!loading);
    var text = document.createElement('span'); text.textContent = message; toast.appendChild(text);
    if (retry) {
      var button = document.createElement('button'); button.type = 'button'; button.textContent = 'Réessayer';
      button.onclick = function () { refresh(true); }; toast.appendChild(button);
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
    if (pending.length) errors.push('La synchronisation continue. Réessaie dans un instant.');
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
  async function replacePage() {
    var href = location.href, html = await request(href, { headers: { Accept: 'text/html' } });
    var doc = new DOMParser().parseFromString(html, 'text/html');
    var next = doc.getElementById('main-content');
    if (!next) throw new Error('Reconnecte-toi dans Réglages pour actualiser.');
    // Never swap under a moving finger or during momentum scrolling.
    while (touched || Date.now() - movedAt < 180) await pause(100);
    if (href !== location.href) return;
    var old = document.getElementById('main-content');
    if (old.dataset.account !== next.dataset.account) throw new Error('Le compte a changé. Ouvre Réglages puis reviens sur cette page.');
    var saved = snapshot(old);
    keepOlderWeeks(old, next);
    next.querySelectorAll('details').forEach(function (el) {
      var key = foldKey(el);
      if (key in saved.folds) el.open = saved.folds[key];
    });
    window.PFViz?.dispose(old);
    // Let HTMX clean up its old pollers/listeners before the detached DOM goes.
    if (window.htmx) window.htmx.remove(old); else old.remove();
    var nav = document.querySelector('.pf-tabbar');
    document.body.insertBefore(next, nav || null);
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
  function refresh(sources) {
    if (running) return running;
    nativeState(true);
    if (sources) status('Actualisation…', false, true);
    running = (async function () {
      var errors = [];
      if (sources) {
        var results = await Promise.allSettled([watches(), json('/api/sync', 'POST').then(function (r) { return r.errors || []; })]);
        results.forEach(function (r) { errors = errors.concat(r.status === 'fulfilled' ? r.value : [r.reason.message]); });
      }
      try { await replacePage(); }
      catch (error) { errors.push(error.message); }
      if (errors.length) status(errors[0], true, false);
      else if (sources) status('Actualisation terminée.', false, false);
    })().finally(function () { running = null; nativeState(false); });
    return running;
  }
  window.paceforgeRefresh = { refresh: refresh };
  document.addEventListener('htmx:beforeOnLoad', function (event) {
    var detail = event.detail;
    if (detail?.elt?.id === 'sante-sync' && detail.xhr.getResponseHeader('HX-Refresh') === 'true') {
      event.preventDefault(); refresh(false);
    }
  });
})();
