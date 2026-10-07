/* PaceForge — pf-viz.js: the one interaction layer for every server-drawn chart.
   The server (app/services/viz.py + partials/_viz.html) draws every mark and prints every string in
   French; this file only moves the selection: crosshair, readout text, the selected mark, the
   accessible state. No colour and no number formatting here (interface.css styles the classes on
   --pf-* tokens, so the theme toggle needs no redraw), no dependency.
   Contract, inside <figure class="pf-viz" data-viz [data-viz-group="…"]>:
     svg.pf-viz-svg ....... fixed viewBox; optional g.pf-viz-cross (a line + one circle per y series, at x=0)
     [data-i="k"] ......... per-mark elements: the selected one gets .is-sel
     [data-r] ............. readout slots, filled in DOM order from D.r[k]
     [data-step="±1"] ..... the 44×44 ‹ › buttons
     [data-r-href] ........ a link following D.h[k] (hidden when null)
     input.pf-viz-range ... visually hidden native range: arrows ±1, PageUp/PageDown ±7, Home/End, Esc back
                            to the default; aria-valuetext = D.a[k]
     [data-live] .......... polite live region: speaks once after a pointer scrub or a step, only in the
                            figure being touched
     script.pf-viz-data ... {x, y, d (ISO dates), r, a, h, sel, link}
   Figures of one data-viz-group follow each other by date (silently); a figure without that date shows « — ».
   Range toggles: [data-viz-ranges] [data-range] show the matching [data-range-panel] in the closest
   [data-viz-scope] and write ?r= (history.replaceState). Lazy panels (hx-get, hx-trigger="click once")
   are picked up on htmx:afterSettle. */
(function () {
  "use strict";
  var motion = !(window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches);

  function nearest(xs, x) {
    var lo = 0, hi = xs.length - 1;
    if (hi <= 0) return 0;
    while (hi - lo > 1) { var m = (lo + hi) >> 1; if (xs[m] <= x) lo = m; else hi = m; }
    return (x - xs[lo] <= xs[hi] - x) ? lo : hi;
  }

  function setup(fig) {
    if (fig.__pfviz) return;
    var dataEl = fig.querySelector("script.pf-viz-data"), svg = fig.querySelector("svg.pf-viz-svg");
    if (!dataEl || !svg) return;
    var D;
    try { D = JSON.parse(dataEl.textContent); } catch (e) { return; }
    if (!D.x || !D.x.length) return;
    fig.__pfviz = true;
    var input = fig.querySelector("input.pf-viz-range"), live = fig.querySelector("[data-live]"),
        cross = svg.querySelector(".pf-viz-cross"), dots = cross ? cross.querySelectorAll("circle") : [],
        slots = fig.querySelectorAll("[data-r]"), marks = svg.querySelectorAll("[data-i]"),
        steps = fig.querySelectorAll("[data-step]"), hrefEl = fig.querySelector("[data-r-href]"),
        group = fig.getAttribute("data-viz-group"), dflt = D.sel == null ? D.x.length - 1 : D.sel,
        cur = -1, pending = null, liveT = null, drag = false;
    var byI = {};
    for (var m = 0; m < marks.length; m++) {
      var k = marks[m].getAttribute("data-i");
      (byI[k] = byI[k] || []).push(marks[m]);
    }

    function mark(i, on) {
      var ms = byI[i] || [];
      for (var j = 0; j < ms.length; j++) ms[j].classList.toggle("is-sel", on);
    }

    function nights(i) {
      var panel = D.link ? document.querySelector(D.link) : null;
      if (!panel) return;
      var shown = false, ns = panel.querySelectorAll("[data-night]");
      for (var n = 0; n < ns.length; n++) {
        var key = ns[n].getAttribute("data-night");
        if (key === "none") continue;
        var on = key === String(i);
        ns[n].toggleAttribute("hidden", !on);   // SVG elements have no .hidden property
        shown = shown || on;
      }
      var none = panel.querySelector('[data-night="none"]');
      if (none) none.toggleAttribute("hidden", shown);
    }

    function stepState() {
      for (var s = 0; s < steps.length; s++) {
        var dir = +steps[s].getAttribute("data-step");
        steps[s].disabled = cur < 0 ? false : (dir < 0 ? cur <= 0 : cur >= D.x.length - 1);
      }
    }

    // opts.silent: following another figure of the group (no event, no speech)
    function select(i, opts) {
      opts = opts || {};
      i = Math.max(0, Math.min(D.x.length - 1, i | 0));
      if (i === cur) return;
      mark(cur, false);
      cur = i; pending = null;
      mark(i, true);
      if (cross) {
        cross.style.visibility = "";
        cross.style.transform = "translateX(" + D.x[i] + "px)";
        for (var s = 0; s < dots.length; s++) {
          var y = D.y && D.y[s] ? D.y[s][i] : null;
          dots[s].style.visibility = y == null ? "hidden" : "";
          if (y != null) dots[s].setAttribute("cy", y);
        }
      }
      var r = (D.r && D.r[i]) || [];
      for (var k = 0; k < slots.length; k++) slots[k].textContent = r[k] || "";
      if (input) { input.value = i; input.setAttribute("aria-valuetext", D.a ? D.a[i] : r.join(" ")); }
      if (hrefEl) {
        var h = D.h && D.h[i];
        hrefEl.toggleAttribute("hidden", !h);
        if (h) { hrefEl.href = h.href || h; hrefEl.textContent = h.label || "Voir ›"; }
      }
      nights(i);
      stepState();
      if (!opts.silent && group && D.d) {
        document.dispatchEvent(new CustomEvent("pfviz:sel", { detail: { group: group, date: D.d[i], src: fig } }));
      }
    }

    function blank(date) {   // this figure has no such day: « — », no mark, no crosshair
      mark(cur, false);
      cur = -1; pending = date;
      if (cross) cross.style.visibility = "hidden";
      for (var k = 0; k < slots.length; k++) slots[k].textContent = k === 1 ? "—" : "";
      if (hrefEl) hrefEl.toggleAttribute("hidden", true);
      nights(-1);
      stepState();
    }

    function announce() {
      if (!live || !D.a || cur < 0) return;
      clearTimeout(liveT);
      liveT = setTimeout(function () { live.textContent = D.a[cur]; }, 350);
    }

    function indexAt(e) {
      var ctm = svg.getScreenCTM();
      if (!ctm) return cur;
      var p = svg.createSVGPoint();
      p.x = e.clientX; p.y = e.clientY;
      return nearest(D.x, p.matrixTransform(ctm.inverse()).x);
    }

    function from(dir) {   // where a step starts after following a date this figure lacks
      if (cur >= 0 || !pending || !D.d) return cur + dir;
      for (var i = 0; i < D.d.length; i++) if (D.d[i] > pending) return dir > 0 ? i : i - 1;
      return dir > 0 ? D.d.length : D.d.length - 1;
    }

    // a tap selects (the single-pointer alternative, WCAG 2.5.7); a horizontal drag scrubs;
    // touch-action: pan-y leaves vertical page scroll to the browser (which then sends pointercancel)
    svg.addEventListener("pointerdown", function (e) {
      if (e.button > 0) return;
      drag = true;
      try { svg.setPointerCapture(e.pointerId); } catch (_) {}
      select(indexAt(e));
    });
    svg.addEventListener("pointermove", function (e) {
      if (drag || e.pointerType === "mouse") select(indexAt(e));   // a mouse reads by hovering
    });
    svg.addEventListener("pointerup", function () { if (drag) { drag = false; announce(); } });
    svg.addEventListener("pointercancel", function () { drag = false; });

    for (var s = 0; s < steps.length; s++) {
      steps[s].addEventListener("click", function () {
        var dir = +this.getAttribute("data-step");
        select(from(dir));
        announce();
      });
    }

    if (input) {
      input.min = 0; input.max = D.x.length - 1; input.step = 1;
      input.addEventListener("input", function () { select(+input.value); });
      input.addEventListener("keydown", function (e) {
        if (e.key === "Escape") { e.preventDefault(); select(dflt); }
        else if (e.key === "PageUp" || e.key === "PageDown") {   // a week at a time
          e.preventDefault(); select(Math.max(0, cur) + (e.key === "PageUp" ? 7 : -7));
        }
      });
    }

    if (group) {
      document.addEventListener("pfviz:sel", function (e) {
        var g = e.detail || {};
        if (g.src === fig || g.group !== group || !document.body.contains(fig)) return;
        var i = D.d ? D.d.indexOf(g.date) : -1;
        if (i >= 0) select(i, { silent: true }); else blank(g.date);
      });
    }

    select(dflt, { silent: true });
    fig.classList.add("pf-viz-on");
    if (motion && "IntersectionObserver" in window) {   // the marks draw once, when they scroll into view
      var io = new IntersectionObserver(function (es) {
        es.forEach(function (en) { if (en.isIntersecting) { fig.classList.add("pf-viz-in"); io.disconnect(); } });
      }, { threshold: 0.3 });
      io.observe(fig);
    } else fig.classList.add("pf-viz-in");
  }

  function ranges(box) {
    if (box.__pfviz) return;
    box.__pfviz = true;
    var btns = box.querySelectorAll("[data-range]"), scope = box.closest("[data-viz-scope]") || document;
    function show(r, save) {
      for (var b = 0; b < btns.length; b++) btns[b].setAttribute("aria-pressed", String(btns[b].getAttribute("data-range") === r));
      var panels = scope.querySelectorAll("[data-range-panel]");
      for (var p = 0; p < panels.length; p++) panels[p].toggleAttribute("hidden", panels[p].getAttribute("data-range-panel") !== r);
      if (save) {
        try { var u = new URL(location.href); u.searchParams.set("r", r); history.replaceState(history.state, "", u); } catch (_) {}
      }
      scan(scope);   // panels that were hidden get their interaction now
    }
    for (var b = 0; b < btns.length; b++) {
      btns[b].addEventListener("click", function () { show(this.getAttribute("data-range"), true); });
    }
  }

  function scan(root) {
    root = root && root.querySelectorAll ? root : document;
    root.querySelectorAll("[data-viz-ranges]").forEach(ranges);
    root.querySelectorAll("figure[data-viz]").forEach(setup);
  }

  window.PFViz = { scan: scan };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { scan(); });
  else scan();
  document.addEventListener("htmx:afterSettle", function (e) { scan(e.target); });
})();
