// Presentation only; measurements stay out of persistent browser storage.
(function () {
    'use strict';
    function currentMain() { return document.getElementById('main-content'); }
    function key() {
        var main = currentMain(), url = new URL(location.href); url.searchParams.delete('app_nav');
        return main && 'pf-view:' + main.dataset.account + ':' + url.pathname + url.search;
    }
    function capture() {
        var main = currentMain(), folds = {}, charts = {};
        if (!main) return null;
        main.querySelectorAll('details[id]').forEach(function (el) {
            if (!el.hasAttribute('data-transient') && !el.closest('[role="dialog"]') && el.id !== 'plan-more') folds[el.id] = el.open;
        });
        main.querySelectorAll('figure[data-viz-key].is-inspecting').forEach(function (fig) {
            try {
                var data = JSON.parse(fig.querySelector('.pf-viz-data').textContent);
                charts[fig.dataset.vizKey] = data.d[Number(fig.querySelector('.pf-viz-range').value)];
            } catch (_) {}
        });
        return { y: scrollY, folds: folds, charts: charts };
    }
    function remember() {
        var state = capture(), name = key();
        if (name && state) try { sessionStorage.setItem(name, JSON.stringify(state)); } catch (_) {}
        return state;
    }
    function restore(state, returning) {
        var main = currentMain();
        if (!main) return;
        if (returning === undefined) returning = new URL(location.href).searchParams.has('app_nav');
        try {
            if (state === undefined) state = JSON.parse(sessionStorage.getItem(key()));
            if (state) {
                Object.keys(state.folds || {}).forEach(function (id) {
                    var el = document.getElementById(id);
                    if (el?.tagName === 'DETAILS' && !el.hasAttribute('data-transient')) el.open = state.folds[id];
                });
                main.querySelectorAll('figure[data-viz-key]').forEach(function (fig) {
                    try {
                        var date = state.charts?.[fig.dataset.vizKey];
                        var data = JSON.parse(fig.querySelector('.pf-viz-data').textContent);
                        var index = data.d.indexOf(date), input = fig.querySelector('.pf-viz-range');
                        if (index >= 0 && input) { input.value = index; input.dispatchEvent(new Event('input')); }
                    } catch (_) {}
                });
            }
            if (location.hash && !returning) {
                var id = decodeURIComponent(location.hash.slice(1));
                var target = document.getElementById(id === 'forme' ? 'fc-facile' : id);
                for (var parent = target; parent; parent = parent.parentElement) if (parent.tagName === 'DETAILS') parent.open = true;
                if (target) { window.PFViz?.scan(target); scrollTo({ top: scrollY + target.getBoundingClientRect().top, behavior: 'instant' }); }
            } else if (state) scrollTo({ top: state.y, behavior: 'instant' });
        } catch (_) {}
    }
    window.paceforgeRememberPage = remember;
    window.PFPageState = { capture: capture, remember: remember, restore: restore };
    window.addEventListener('pagehide', remember);
    document.addEventListener('visibilitychange', function () { if (document.hidden) remember(); });
    // PFViz's DOM-ready setup completes before this frame; distant images do
    // not postpone restoration or leave a previously visited page at its top.
    function ready() { requestAnimationFrame(function () { restore(); }); }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ready, { once: true }); else ready();
    window.addEventListener('hashchange', function () { restore(null, false); });
    var leaving = false;
    document.addEventListener('click', function (event) {
        var a = event.target.closest('a[href]');
        if (!a || event.defaultPrevented || event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || a.target || a.hasAttribute('download')) return;
        var next = new URL(a.href, location.href);
        if (next.origin === location.origin && next.pathname === location.pathname && next.search === location.search) return;
        if (!window.paceforgeBeforeLeave || !['https:', 'http:', 'paceforge:'].includes(next.protocol)) return;
        event.preventDefault();
        if (leaving) return;
        leaving = true;
        window.paceforgeBeforeLeave().then(function () { remember(); location.href = next.href; })
            .catch(function () { /* The plan shows its save error and retry action. */ })
            .finally(function () { leaving = false; });
    });
})();
