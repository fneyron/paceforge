/* PaceForge — pf-viz.js: the one interaction layer for every server-drawn chart.
   The server (app/services/viz.py + partials/_viz.html) draws every mark and prints every string in
   French; this file only moves the selection: crosshair, readout text, the selected mark, the
   accessible state. No colour and no number formatting here (interface.css styles the classes on
   --pf-* tokens, so the theme toggle needs no redraw), no dependency.
   Contract, inside <figure class="pf-viz" data-viz [data-viz-group="…"]>:
     svg.pf-viz-svg ....... fixed viewBox; optional g.pf-viz-cross (a line + one circle per y series, at x=0)
     [data-i="k"] ......... per-mark elements: the selected one gets .is-sel
     [data-r] ............. readout slots, filled in DOM order from D.r[k]
     [data-step="±1"] ..... the 44×44 ‹ › buttons, when the figure has them (aria-disabled at either end, never
                            disabled: they keep focus); Santé's cards have none: a tap selects, a drag scrubs
     data-tone ............ on the figure, D.t[k] (the selected point's tone: its readout's word wears it, by CSS)
     [data-r-href] ........ a link following D.h[k]: its row is always there (visibility only, so the plot never
                            moves under the finger); hidden, aria-hidden and out of the tab order when null
     input.pf-viz-range ... visually hidden native range: arrows ±1, PageUp/PageDown ±7, Home/End, Esc back
                            to the default; aria-valuetext = D.a[k]
     [data-live] .......... polite live region: speaks once after a pointer scrub or a step, only in the
                            figure being touched
     script.pf-viz-data ... {x, y, d (ISO dates), r, a, h, sel, link, rest?, restA?, back?, t?}
   Figures of one data-viz-group follow each other by date (silently); a figure without that date shows « — ».
   The readout's height is reserved for its tallest line (measured once the figure is laid out, again when
   its width changes), so selecting never moves the plot. A touch selects on a tap or a horizontal drag only:
   a vertical swipe scrolls the page and leaves the selection as it was.
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
        cur = -1, pending = null, liveT = null, drag = false, touch = null,
        readBox = fig.querySelector(".pf-viz-read"),
        // D.rest: a resting readout (« semaine type : 7h40 ») shown until a touch; it sits one step
        // after the last point (index D.x.length): › from the last point and Esc come back to it
        rest = D.rest || null, END = D.x.length, top = END - 1 + (rest ? 1 : 0),
        // D.back: where ‹ (or ←) from the resting readout lands, the latest point already lived (not a future slot)
        back = rest && D.back != null ? D.back : END - 1;
    if (rest) dflt = END;
    var byI = {};
    for (var m = 0; m < marks.length; m++) {
      var k = marks[m].getAttribute("data-i");
      (byI[k] = byI[k] || []).push(marks[m]);
    }

    function mark(i, on) {
      var ms = byI[i] || [];
      for (var j = 0; j < ms.length; j++) ms[j].classList.toggle("is-sel", on);
    }

    function tone(i) {   // a class-like attribute, never a colour: CSS colours the readout's word
      if (D.t) fig.setAttribute("data-tone", (i >= 0 && i < D.t.length && D.t[i]) || "");
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

    function stepState() {   // aria-disabled, never disabled: a focused button that disables itself drops focus
      for (var s = 0; s < steps.length; s++) {
        var dir = +steps[s].getAttribute("data-step");
        var off = cur < 0 ? false : (dir < 0 ? cur <= 0 : cur >= top);
        steps[s].setAttribute("aria-disabled", off ? "true" : "false");
      }
    }

    function link(h) {   // the row stays; only its content shows or not
      if (!hrefEl) return;
      hrefEl.classList.toggle("is-off", !h);
      if (h) {
        hrefEl.href = h.href || h; hrefEl.textContent = h.label || "Voir ›";
        hrefEl.removeAttribute("aria-hidden"); hrefEl.removeAttribute("tabindex");
      } else {
        hrefEl.setAttribute("aria-hidden", "true"); hrefEl.setAttribute("tabindex", "-1");
      }
    }

    // the readout's tallest line, so that no selection moves the plot: the longest readouts are measured
    // (a dozen at most), with the resting one and « — »; again when the figure's width changes
    var lastW = -1;
    function reserve() {
      if (!readBox || !slots.length) return;
      var w = readBox.clientWidth;
      if (!w || w === lastW) return;
      lastW = w;
      var rows = (D.r || []).map(function (r, i) { return { r: r || [], i: i }; });
      function len(r, k) { return ((r[k] || "") + "").length; }
      var cand = rows.slice().sort(function (a, b) { return (len(b.r, 1) + len(b.r, 2)) - (len(a.r, 1) + len(a.r, 2)); })
        .slice(0, 12).concat(rows.slice().sort(function (a, b) { return len(b.r, 0) - len(a.r, 0); }).slice(0, 4))
        .map(function (o) { return o.r; });
      if (rest) cand.push(rest);
      cand.push(["", "—", ""]);
      var keep = [], k;
      for (k = 0; k < slots.length; k++) keep.push(slots[k].textContent);
      readBox.style.minHeight = "";
      var max = 0;
      for (var c = 0; c < cand.length; c++) {
        for (k = 0; k < slots.length; k++) slots[k].textContent = cand[c][k] || "";
        max = Math.max(max, readBox.offsetHeight);
      }
      for (k = 0; k < slots.length; k++) slots[k].textContent = keep[k];
      if (max) readBox.style.minHeight = max + "px";
    }

    // opts.silent: following another figure of the group (no event, no speech)
    function select(i, opts) {
      opts = opts || {};
      i = Math.max(0, Math.min(top, i | 0));
      if (i === cur) return;
      if (rest && i === END) { resting(); return; }
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
      tone(i);
      if (input) { input.value = i; input.setAttribute("aria-valuetext", D.a ? D.a[i] : r.join(" ")); }
      link(D.h && D.h[i]);
      nights(i);
      stepState();
      if (!opts.silent && group && D.d) {
        document.dispatchEvent(new CustomEvent("pfviz:sel", { detail: { group: group, date: D.d[i], src: fig } }));
      }
    }

    function resting() {   // nothing selected: the server's resting readout
      mark(cur, false);
      cur = END; pending = null;
      if (cross) cross.style.visibility = "hidden";
      for (var k = 0; k < slots.length; k++) slots[k].textContent = rest[k] || "";
      tone(-1);
      if (input) { input.value = END; input.setAttribute("aria-valuetext", D.restA || rest.join(" ")); }
      link(null);
      nights(-1);
      stepState();
    }

    function blank(date) {   // this figure has no such day: « — », no mark, no crosshair
      mark(cur, false);
      cur = -1; pending = date;
      if (cross) cross.style.visibility = "hidden";
      for (var k = 0; k < slots.length; k++) slots[k].textContent = k === 1 ? "—" : "";
      tone(-1);
      link(null);
      nights(-1);
      stepState();
    }

    function announce() {
      if (!live || !D.a || cur < 0) return;
      clearTimeout(liveT);
      liveT = setTimeout(function () { live.textContent = cur === END ? (D.restA || "") : D.a[cur]; }, 350);
    }

    function indexAt(e) {
      var ctm = svg.getScreenCTM();
      if (!ctm) return cur;
      var p = svg.createSVGPoint();
      p.x = e.clientX; p.y = e.clientY;
      return nearest(D.x, p.matrixTransform(ctm.inverse()).x);
    }

    function from(dir) {   // where a step starts after following a date this figure lacks
      if (rest && cur === END && dir < 0) return back;
      if (cur >= 0 || !pending || !D.d) return cur + dir;
      for (var i = 0; i < D.d.length; i++) if (D.d[i] > pending) return dir > 0 ? i : i - 1;
      return dir > 0 ? D.d.length : D.d.length - 1;
    }

    // a tap selects (the single-pointer alternative, WCAG 2.5.7); a horizontal drag scrubs;
    // touch-action: pan-y leaves vertical page scroll to the browser (which then sends pointercancel).
    // A touch selects nothing until it is a tap (lifted within 8 px) or a scrub (8 px sideways first):
    // a swipe that scrolls the page never changes the selection. Mouse and pen select at once.
    var SLOP = 8;
    svg.addEventListener("pointerdown", function (e) {
      if (e.button > 0) return;
      if (e.pointerType === "touch") { touch = { x: e.clientX, y: e.clientY, scrub: false }; return; }
      drag = true;
      try { svg.setPointerCapture(e.pointerId); } catch (_) {}
      select(indexAt(e));
    });
    svg.addEventListener("pointermove", function (e) {
      if (touch) {
        if (!touch.scrub) {
          var dx = Math.abs(e.clientX - touch.x), dy = Math.abs(e.clientY - touch.y);
          if (dx <= SLOP || dx <= dy) return;
          touch.scrub = true;
          try { svg.setPointerCapture(e.pointerId); } catch (_) {}
        }
        select(indexAt(e));
        return;
      }
      if (drag || e.pointerType === "mouse") select(indexAt(e));   // a mouse reads by hovering
    });
    svg.addEventListener("pointerup", function (e) {
      if (touch) {
        var t = touch;
        touch = null;
        if (!t.scrub && Math.abs(e.clientX - t.x) <= SLOP && Math.abs(e.clientY - t.y) <= SLOP) select(indexAt(e));
        announce();
        return;
      }
      if (drag) { drag = false; announce(); }
    });
    svg.addEventListener("pointercancel", function () { touch = null; drag = false; });

    for (var s = 0; s < steps.length; s++) {
      steps[s].addEventListener("click", function () {
        if (this.getAttribute("aria-disabled") === "true") return;
        var dir = +this.getAttribute("data-step");
        select(from(dir));
        announce();
      });
    }

    if (input) {
      input.min = 0; input.max = top; input.step = 1;
      input.addEventListener("input", function () {
        var v = +input.value;
        select(rest && cur === END && v === END - 1 ? back : v);
      });
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
    reserve();
    if ("ResizeObserver" in window) {   // shown later, or a new width (next frame: never inside the observer's loop)
      new ResizeObserver(function () { requestAnimationFrame(reserve); }).observe(fig);
    } else window.addEventListener("resize", reserve);
    // the marks draw once, when they scroll into view; a figure already on screen when this runs was painted
    // whole before the script: it stays as it is (never collapsed and drawn again)
    var r0 = fig.getBoundingClientRect(), seen = r0.height > 0 && r0.bottom > 0 && r0.top < window.innerHeight;
    fig.classList.add("pf-viz-on");
    if (motion && !seen && "IntersectionObserver" in window) {
      var io = new IntersectionObserver(function (es) {
        es.forEach(function (en) { if (en.isIntersecting) { fig.classList.add("pf-viz-in"); io.disconnect(); } });
      }, { threshold: 0 });
      io.observe(fig);
    } else fig.classList.add("pf-viz-in", "pf-viz-still");
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
