/* PaceForge — dependency-free SVG elevation profile with a fluid crosshair.
   Static parts (grade bands, outline, axes, checkpoints) are drawn once; hovering
   only moves a line, a dot and a label. Usage:
     var prof = PFElevationProfile(el, { elevData, totalDistKm, isDark, onHover(km), onLeave(), onClick(km) });
     prof.setCheckpoints([{distance_km, label}]); prof.showCross(km); prof.hideCross(); prof.destroy();
*/
window.PFElevationProfile = function (el, opts) {
  var NS = 'http://www.w3.org/2000/svg';
  var elev = opts.elevData || [], total = opts.totalDistKm || 1, isDark = !!opts.isDark;
  var gridC = isDark ? '#333' : '#f0f0f0', mutedC = isDark ? '#6b7280' : '#9ca3af';
  var P = { w: 0, h: 0, padL: 52, padR: 10, padT: 18, padB: 20, minE: 0, maxE: 1 };
  var svg = null, cross, dot, lbl, lblBox, lblText, cpLayer, cps = [], ro = null, raf = null, pendingKm = 0;
  function mk(t, a) { var e = document.createElementNS(NS, t); for (var k in a) e.setAttribute(k, a[k]); return e; }
  function xOf(km) { return P.padL + (km / total) * (P.w - P.padL - P.padR); }
  function yOf(e) { return P.padT + (1 - (e - P.minE) / ((P.maxE - P.minE) || 1)) * (P.h - P.padT - P.padB); }
  function kmOfX(px) { return Math.max(0, Math.min(total, (px - P.padL) / (P.w - P.padL - P.padR) * total)); }
  function elevAt(km) {
    var lo = 0, hi = elev.length - 1; if (hi < 0) return 0;
    if (km <= elev[0].distance_km) return elev[0].elevation; if (km >= elev[hi].distance_km) return elev[hi].elevation;
    while (hi - lo > 1) { var m = (lo + hi) >> 1; if (elev[m].distance_km <= km) lo = m; else hi = m; }
    var a = elev[lo], b = elev[hi], t = (km - a.distance_km) / ((b.distance_km - a.distance_km) || 1);
    return a.elevation + t * (b.elevation - a.elevation);
  }
  function gradeAt(km) { var d = 0.25, k0 = Math.max(0, km - d), k1 = Math.min(total, km + d); return k1 > k0 ? (elevAt(k1) - elevAt(k0)) / ((k1 - k0) * 1000) * 100 : 0; }
  function fmtKm(km) { return km.toFixed(1).replace('.', ','); }
  function fmtM(m) { return Math.round(m).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ' '); }
  function band(pct) { if (pct > 12) return '#b91c1c'; if (pct > 6) return '#ef4444'; if (pct > 2) return '#fb923c'; if (pct > -2) return '#d1d5db'; if (pct > -6) return '#93c5fd'; if (pct > -12) return '#3b82f6'; return '#1d4ed8'; }

  function draw() {
    if (!el || !elev.length) return;
    el.innerHTML = '';
    var w = el.clientWidth || 800, h = el.clientHeight || 170; P.w = w; P.h = h;
    var minE = Infinity, maxE = -Infinity;
    for (var i = 0; i < elev.length; i++) { var e = elev[i].elevation; if (e < minE) minE = e; if (e > maxE) maxE = e; }
    var pad = Math.max(40, (maxE - minE) * 0.08);
    P.minE = Math.floor((minE - pad) / 50) * 50; P.maxE = Math.ceil((maxE + pad) / 50) * 50;
    svg = mk('svg', { width: w, height: h, viewBox: '0 0 ' + w + ' ' + h }); svg.style.display = 'block'; svg.style.cursor = 'crosshair'; svg.style.touchAction = 'pan-y';
    var grid = mk('g', {});
    [P.minE, (P.minE + P.maxE) / 2, P.maxE].forEach(function (lvl) {
      var y = yOf(lvl);
      grid.appendChild(mk('line', { x1: P.padL, x2: w - P.padR, y1: y, y2: y, stroke: gridC, 'stroke-dasharray': '3 3' }));
      var t = mk('text', { x: P.padL - 6, y: y + 3, 'text-anchor': 'end', 'font-size': '10', fill: mutedC }); t.textContent = fmtM(lvl) + ' m'; grid.appendChild(t);
    });
    var step = total > 120 ? 20 : total > 60 ? 10 : total > 25 ? 5 : total > 10 ? 2 : 1;
    for (var k = step; k < total; k += step) { var tx = mk('text', { x: xOf(k), y: h - 6, 'text-anchor': 'middle', 'font-size': '10', fill: mutedC }); tx.textContent = k + ' km'; grid.appendChild(tx); }
    svg.appendChild(grid);
    var ds = Math.max(1, Math.floor(elev.length / 400)), pts = [];
    for (i = 0; i < elev.length; i += ds) pts.push(elev[i]);
    if (pts[pts.length - 1] !== elev[elev.length - 1]) pts.push(elev[elev.length - 1]);
    var strips = mk('g', { 'fill-opacity': isDark ? '0.55' : '0.45' }), base = h - P.padB, d = '';
    var win = Math.max(0.6, total / 60);
    for (i = 1; i < pts.length; i++) {
      var a = pts[i - 1], b = pts[i], mid = (a.distance_km + b.distance_km) / 2;
      var k0 = Math.max(0, mid - win / 2), k1 = Math.min(total, mid + win / 2);
      var pct = k1 > k0 ? (elevAt(k1) - elevAt(k0)) / ((k1 - k0) * 1000) * 100 : 0;
      var xa = xOf(a.distance_km).toFixed(1), xb = xOf(b.distance_km).toFixed(1);
      strips.appendChild(mk('polygon', { points: xa + ',' + yOf(a.elevation).toFixed(1) + ' ' + xb + ',' + yOf(b.elevation).toFixed(1) + ' ' + xb + ',' + base + ' ' + xa + ',' + base, fill: band(pct) }));
    }
    pts.forEach(function (p, i) { d += (i ? 'L' : 'M') + xOf(p.distance_km).toFixed(1) + ' ' + yOf(p.elevation).toFixed(1) + ' '; });
    svg.appendChild(strips);
    svg.appendChild(mk('path', { d: d, fill: 'none', stroke: isDark ? '#e5e7eb' : '#111827', 'stroke-width': '1.5', 'stroke-linejoin': 'round' }));
    cpLayer = mk('g', {}); svg.appendChild(cpLayer);
    cross = mk('line', { x1: 0, x2: 0, y1: P.padT, y2: base, stroke: '#f97316', 'stroke-width': '1.5', visibility: 'hidden' });
    dot = mk('circle', { r: 4, fill: '#f97316', stroke: '#fff', 'stroke-width': '1.5', visibility: 'hidden' });
    lbl = mk('g', { visibility: 'hidden' });
    lblBox = mk('rect', { rx: 4, ry: 4, height: 18, fill: isDark ? '#f3f4f6' : '#111827' });
    lblText = mk('text', { 'font-size': '10', 'font-weight': '600', fill: isDark ? '#111827' : '#ffffff', y: 12.5, x: 6 });
    lbl.appendChild(lblBox); lbl.appendChild(lblText);
    svg.appendChild(cross); svg.appendChild(dot); svg.appendChild(lbl);
    function kmFrom(ev) { var r = svg.getBoundingClientRect(); var cx = (ev.touches ? ev.touches[0].clientX : ev.clientX) - r.left; return kmOfX(cx * (w / r.width)); }
    svg.addEventListener('mousemove', function (ev) { hover(kmFrom(ev), true); });
    svg.addEventListener('mouseleave', function () { hideCross(); if (opts.onLeave) opts.onLeave(); });
    svg.addEventListener('touchstart', function (ev) { hover(kmFrom(ev), true); }, { passive: true });
    svg.addEventListener('touchmove', function (ev) { hover(kmFrom(ev), true); }, { passive: true });
    svg.addEventListener('click', function (ev) { if (opts.onClick) opts.onClick(kmFrom(ev)); });
    el.appendChild(svg); drawCps();
    if (ro) { try { ro.disconnect(); } catch (e) {} }
    if (window.ResizeObserver) { var rt = null; ro = new ResizeObserver(function () { if (rt) clearTimeout(rt); rt = setTimeout(function () { if (el.clientWidth && Math.abs(el.clientWidth - P.w) > 4) draw(); }, 120); }); ro.observe(el); }
  }
  function showCross(km) {
    if (!svg) return;
    var x = xOf(km), y = yOf(elevAt(km));
    cross.setAttribute('x1', x); cross.setAttribute('x2', x); cross.setAttribute('visibility', 'visible');
    dot.setAttribute('cx', x); dot.setAttribute('cy', y); dot.setAttribute('visibility', 'visible');
    var g = gradeAt(km);
    lblText.textContent = 'km ' + fmtKm(km) + ' · ' + fmtM(elevAt(km)) + ' m · ' + (g > 0 ? '+' : '') + Math.round(g) + ' %';
    var tw = lblText.getComputedTextLength() + 12, lx = x + 8; if (lx + tw > P.w - P.padR) lx = x - 8 - tw;
    lblBox.setAttribute('width', tw); lbl.setAttribute('transform', 'translate(' + lx.toFixed(1) + ',' + P.padT + ')'); lbl.setAttribute('visibility', 'visible');
  }
  function hideCross() { if (!svg) return; cross.setAttribute('visibility', 'hidden'); dot.setAttribute('visibility', 'hidden'); lbl.setAttribute('visibility', 'hidden'); }
  function hover(km, fromChart) { pendingKm = km; if (raf) return; raf = requestAnimationFrame(function () { raf = null; showCross(pendingKm); if (fromChart && opts.onHover) opts.onHover(pendingKm); }); }
  function drawCps() {
    if (!cpLayer) return; while (cpLayer.firstChild) cpLayer.removeChild(cpLayer.firstChild);
    var base = P.h - P.padB;
    cps.forEach(function (cp, i) {
      var x = xOf(cp.distance_km);
      cpLayer.appendChild(mk('line', { x1: x, x2: x, y1: P.padT - 2, y2: base, stroke: '#f59e0b', 'stroke-width': '1', 'stroke-dasharray': '2 2', opacity: '0.9' }));
      var g = mk('g', { transform: 'translate(' + (x - 11).toFixed(1) + ',' + (P.padT - 16) + ')' });
      g.appendChild(mk('rect', { width: 22, height: 13, rx: 3, ry: 3, fill: '#f59e0b' }));
      var t = mk('text', { x: 11, y: 9.5, 'text-anchor': 'middle', 'font-size': '8', 'font-weight': '700', fill: '#fff' }); t.textContent = cp.label || ('CP' + (i + 1)); g.appendChild(t);
      cpLayer.appendChild(g);
    });
  }
  draw();
  return {
    showCross: showCross, hideCross: hideCross,
    setCheckpoints: function (list) { cps = list || []; drawCps(); },
    elevAt: elevAt,
    destroy: function () { if (ro) { try { ro.disconnect(); } catch (e) {} } ro = null; svg = null; if (el) el.innerHTML = ''; }
  };
};
