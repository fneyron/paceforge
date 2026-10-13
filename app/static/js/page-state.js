// Preserve presentation within this signed-in WebView session; never cache health data.
(function () {
    'use strict';
    var main = document.getElementById('main-content');
    if (!main) return;
    var url = new URL(location.href), returning = url.searchParams.has('app_nav'); url.searchParams.delete('app_nav');
    var key = 'pf-view:' + main.dataset.account + ':' + url.pathname + url.search;
    function remember() {
        var folds = {}, charts = {};
        document.querySelectorAll('details[id]').forEach(function (el) {
            if (!el.closest('[role="dialog"]') && el.id !== 'plan-more') folds[el.id] = el.open;
        });
        document.querySelectorAll('figure[data-viz-key].is-inspecting').forEach(function (fig) {
            try {
                var data = JSON.parse(fig.querySelector('.pf-viz-data').textContent);
                charts[fig.dataset.vizKey] = data.d[Number(fig.querySelector('.pf-viz-range').value)];
            } catch (_) {}
        });
        try { sessionStorage.setItem(key, JSON.stringify({ y: scrollY, folds: folds, charts: charts })); } catch (_) {}
    }
    window.paceforgeRememberPage = remember;
    function restore() {
        try {
            var saved = JSON.parse(sessionStorage.getItem(key));
            if (!saved) return;
            Object.keys(saved.folds || {}).forEach(function (id) {
                var el = document.getElementById(id); if (el?.tagName === 'DETAILS') el.open = saved.folds[id];
            });
            document.querySelectorAll('figure[data-viz-key]').forEach(function (fig) {
                try {
                    var date = saved.charts?.[fig.dataset.vizKey];
                    var data = JSON.parse(fig.querySelector('.pf-viz-data').textContent);
                    var index = data.d.indexOf(date), input = fig.querySelector('.pf-viz-range');
                    if (index >= 0 && input) { input.value = index; input.dispatchEvent(new Event('input')); }
                } catch (_) {}
            });
            if (location.hash && !returning) {
                var target = document.getElementById(decodeURIComponent(location.hash.slice(1)));
                for (var parent = target; parent; parent = parent.parentElement) if (parent.tagName === 'DETAILS') parent.open = true;
                if (target) requestAnimationFrame(function () { target.scrollIntoView({ block: 'start', behavior: 'instant' }); });
            } else requestAnimationFrame(function () { scrollTo({ top: saved.y, behavior: 'instant' }); });
        } catch (_) {}
    }
    window.addEventListener('pagehide', remember);
    document.addEventListener('visibilitychange', function () { if (document.hidden) remember(); });
    window.addEventListener('load', restore, { once: true });
    // Wait for the debounced plan save before an ordinary link leaves the document.
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
