/* Coaching › the board by hand, and the road scrubbed.
   - A session's tile is held, then dropped on another day (of the board, or of the next week's row): the move the
     « Déplacer » forms post, to the same address, with the same fields. A horizontal swipe on the weeks is ‹ or ›.
   - The road: a finger (or a mouse) scrubs the bars — the bar under it is highlighted and the road says its week
     (dates, time, phase: data-label, the server's words) —, the week is loaded once, when it is let go. The arrow
     keys move along the bars (one of them in the tab order), Enter opens the week: the bars are its links.
   Nothing else: the keyboard, a screen reader and a browser without JavaScript keep « Déplacer » and the links.
   - The server says everything: which days take a session and which would put two hard days side by side
     (data-to, data-warn on its tile, coaching_view.drops), what the tile is called while it is held (data-what).
     This file only reads them; after the drop the server decides, and its answer redraws the board.
   - A finger holds HOLD ms without moving (a move before that is the page's scroll or a swipe); a mouse moves MOUSE px.
   - Bound once on the page's container: htmx replaces the swapped part after every action. */
(function () {
  'use strict';
  var root = document.querySelector('.pf-coach');
  if (!root || root.dataset.byHand) return;
  root.dataset.byHand = '1';
  var HOLD = 250, SLOP = 8, MOUSE = 5, SWIPE = 48, EDGE = 24;
  var press = null, drag = null, pending = null, swipe = null, quietUntil = 0, slide = null, scrub = null, roadQuiet = 0, popped = null;
  var HINTED = 'pf-coach-by-hand';

  function words(s) { return s ? s.split(' ') : []; }
  function dayOf(el) { return el && el.closest ? el.closest('.pf-coach-grid[data-w] a.pf-coach-day[data-day]') : null; }
  // the tile under the finger when it can be picked up, else the day's first session that can, else its grey run
  function blockOf(day, target) {
    var hit = target.closest('[data-move], [data-run]');
    if (hit && day.contains(hit)) return hit;
    return day.querySelector('[data-move]') || day.querySelector('[data-run]');
  }
  // where it can go: the days the server named for a session; for a run outside the plan, the days of its week
  // whose session may move to the run's day (« C’est ma séance de … »: that session goes there)
  function dropsOf(block, home) {
    var drops = new Map(), week = home.closest('.pf-coach-week');
    home.closest('.pf-coach-grid').querySelectorAll('a.pf-coach-day[data-day]').forEach(function (day) {
      var iso = day.dataset.day, session = block;
      if (block.dataset.run) {
        iso = block.dataset.run;
        session = day === home || !week.contains(day) ? null : Array.prototype.find.call(day.querySelectorAll('[data-move]'), function (b) { return words(b.dataset.to).indexOf(iso) >= 0; });
        if (!session) return;
      } else if (words(block.dataset.to).indexOf(iso) < 0) return;
      drops.set(day, { url: session.dataset.move, to: iso, block: session, into: block.dataset.run ? home : day,
        warn: words(session.dataset.warn).indexOf(iso) >= 0 });
    });
    return drops;
  }

  // the tile itself, lifted: its copy at its size, a little larger and tilted, with a shadow
  function ghostOf(block) {
    var r = block.getBoundingClientRect(), ghost = block.cloneNode(true);
    ['data-move', 'data-to', 'data-warn', 'data-run', 'style'].forEach(function (a) { ghost.removeAttribute(a); });
    ghost.classList.add('pf-coach-ghost');
    ghost.setAttribute('aria-hidden', 'true');
    ghost.style.width = r.width + 'px';
    ghost.style.height = r.height + 'px';
    ghost.title = block.dataset.what || '';
    root.appendChild(ghost);  // inside the page: the kinds' colours are set there
    return ghost;
  }

  function lift() {
    var p = press;
    press = null;
    clearTimeout(p.timer);
    var drops = dropsOf(p.block, p.day);
    if (!drops.size) return false;
    var main = p.day.closest('.pf-coach-main');
    drag = { id: p.id, type: p.type, day: p.day, block: p.block, drops: drops, main: main, over: null,
      moved: p.type !== 'touch', x0: p.x0, y0: p.y0, ghost: ghostOf(p.block) };
    swipe = null;  // a hold is not a swipe
    main.classList.add('is-dragging');
    p.day.classList.add('is-home');
    p.block.classList.add('is-lifted');
    drops.forEach(function (d, day) { day.classList.add('is-drop'); if (d.warn) day.classList.add('is-drop-warn'); });
    try { p.day.setPointerCapture(p.id); } catch (_) { /* a pointer already gone: the next event ends it */ }
    if (p.type === 'touch' && navigator.vibrate) { try { navigator.vibrate(8); } catch (_) { /* no haptics */ } }
    follow(p.x, p.y);
    return true;
  }

  // the day under the finger is marked; the lifted tile floats above the finger, and above that day's slot (the
  // finger never hides where it will land)
  function follow(x, y) {
    var under = document.elementFromPoint(x, y), day = under && under.closest('a.pf-coach-day');
    var over = day && drag.drops.has(day) ? day : null;
    if (over !== drag.over) {
      if (drag.over) drag.over.classList.remove('is-over');
      if (over) over.classList.add('is-over');
      drag.over = over;
    }
    var g = drag.ghost, w = g.offsetWidth, h = g.offsetHeight;
    // over the next week's small row: above the row, its tiles stay seen
    var limit = day && day.classList.contains('is-mini') ? day.getBoundingClientRect().top - 6 : Infinity;
    var bottom = Math.min(y - (drag.type === 'touch' ? 34 : 16), limit);
    var left = Math.max(6, Math.min(innerWidth - w - 6, x - w / 2));
    g.style.transform = 'translate(' + Math.round(left) + 'px,' + Math.round(Math.max(6, bottom - h)) + 'px) scale(1.08) rotate(-3deg)';
  }

  function finish(cancel) {
    var d = drag;
    drag = null;
    if (!d) return;
    d.ghost.remove();
    d.main.classList.remove('is-dragging');
    d.block.classList.remove('is-lifted');
    d.day.classList.remove('is-home');
    d.drops.forEach(function (_, day) { day.classList.remove('is-drop', 'is-drop-warn', 'is-over'); });
    if (d.moved) quietUntil = Date.now() + 500;  // the click that ends a drag opens no day
    if (cancel || !d.over || !d.day.isConnected || !window.htmx) return;
    var t = d.drops.get(d.over), grid = d.over.closest('.pf-coach-grid');
    // shown in its new day at once; the server's answer redraws the board, and an answer that never comes puts
    // the tile back (the app's toast says the request failed)
    pending = { block: t.block, home: t.block.parentNode, next: t.block.nextSibling };
    t.into.querySelector('.pf-coach-blocks').appendChild(t.block);
    t.block.classList.add('is-pending');
    popped = t.url;
    try { localStorage.setItem(HINTED, '1'); } catch (_) { /* private mode: the hint comes back, nothing else */ }
    window.htmx.ajax('POST', t.url, { source: grid, target: '#coach-main', swap: 'outerHTML',
      values: { to: t.to, w: grid.dataset.w } });
  }

  function release() {
    if (!press) return;
    clearTimeout(press.timer);
    press = null;
  }

  // ── the road: scrubbed, the bar under the finger highlighted and said; loaded once, when let go ──
  function barsOf() { return document.getElementById('coach-road'); }
  function tipLine() { return document.getElementById('coach-road-tip'); }
  function barAt(bars, x) {
    var list = bars.querySelectorAll('.pf-coach-wbar'), r = bars.getBoundingClientRect();
    if (!list.length || !r.width) return null;
    return list[Math.max(0, Math.min(list.length - 1, Math.floor((x - r.left) / r.width * list.length)))];
  }
  function mark(bars, bar) {
    bars.querySelectorAll('.pf-coach-wbar.is-scrub').forEach(function (b) { if (b !== bar) b.classList.remove('is-scrub'); });
    if (bar) bar.classList.add('is-scrub');
    var tip = tipLine();
    if (tip) tip.textContent = bar ? bar.dataset.label : bars.dataset.tip;
  }
  function load(bar) {
    var shown = barsOf().querySelector('.pf-coach-wbar.is-shown'), list = Array.prototype.slice.call(barsOf().children);
    if (shown) leave(list.indexOf(bar) > list.indexOf(shown) ? 'next' : 'prev');
    if (window.htmx) window.htmx.ajax('GET', bar.getAttribute('hx-get'), { source: bar, target: '#coach-main', swap: 'outerHTML' });
    else location.href = bar.href;
  }
  function endScrub(cancel) {
    var s = scrub;
    scrub = null;
    if (!s) return;
    s.bars.classList.remove('is-scrubbing');
    roadQuiet = Date.now() + 600;  // the click that ends a scrub is the scrub's
    if (cancel || !s.bar || s.bar.classList.contains('is-shown')) { mark(s.bars, null); return; }
    load(s.bar);
  }

  root.addEventListener('pointerdown', function (e) {
    if (drag || press || scrub || !e.isPrimary || (e.pointerType === 'mouse' && e.button !== 0)) return;
    var bars = e.target.closest && e.target.closest('#coach-road');
    if (bars) {
      e.preventDefault();  // no selection, no focus ring on a tap
      scrub = { id: e.pointerId, bars: bars, bar: null };
      bars.classList.add('is-scrubbing');
      try { bars.setPointerCapture(e.pointerId); } catch (_) { /* gone: pointerup ends it */ }
      scrub.bar = barAt(bars, e.clientX);
      mark(bars, scrub.bar);
      return;
    }
    var day = dayOf(e.target), block = day && blockOf(day, e.target);
    if (!block) return;
    press = { id: e.pointerId, type: e.pointerType, day: day, block: block, x0: e.clientX, y0: e.clientY,
      x: e.clientX, y: e.clientY, timer: 0 };
    if (e.pointerType === 'touch') press.timer = setTimeout(function () { if (press) lift(); }, HOLD);
  });
  document.addEventListener('pointermove', function (e) {
    if (scrub && e.pointerId === scrub.id) {
      var bar = barAt(scrub.bars, e.clientX);
      if (bar !== scrub.bar) { scrub.bar = bar; mark(scrub.bars, bar); }
      return;
    }
    if (press && e.pointerId === press.id) {
      press.x = e.clientX; press.y = e.clientY;
      var far = Math.hypot(e.clientX - press.x0, e.clientY - press.y0);
      if (press.type === 'touch') { if (far > SLOP) release(); }  // the page's scroll, or a swipe
      else if (far > MOUSE) lift();
      return;
    }
    if (!drag || e.pointerId !== drag.id) return;
    if (Math.hypot(e.clientX - drag.x0, e.clientY - drag.y0) > SLOP) drag.moved = true;
    follow(e.clientX, e.clientY);
  });
  document.addEventListener('pointerup', function (e) {
    if (scrub && e.pointerId === scrub.id) endScrub(false);
    else if (press && e.pointerId === press.id) release();
    // a hold let go where it started is a tap: the day opens, nothing moves
    else if (drag && e.pointerId === drag.id) { if (drag.moved) follow(e.clientX, e.clientY); finish(!drag.moved); }
  });
  document.addEventListener('pointercancel', function (e) {
    if (scrub && e.pointerId === scrub.id) endScrub(true);
    else if (press && e.pointerId === press.id) release();
    else if (drag && e.pointerId === drag.id) finish(true);
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && drag) { e.preventDefault(); finish(true); return; }
    // the road by the keyboard: the arrows move along the weeks, Home and End to its ends, Enter opens the week
    var bar = e.target.closest && e.target.closest('.pf-coach-wbar');
    if (!bar || ['ArrowLeft', 'ArrowRight', 'Home', 'End'].indexOf(e.key) < 0) return;
    e.preventDefault();
    var list = Array.prototype.slice.call(bar.parentNode.children), i = list.indexOf(bar);
    var to = list[e.key === 'Home' ? 0 : e.key === 'End' ? list.length - 1 : Math.max(0, Math.min(list.length - 1, i + (e.key === 'ArrowRight' ? 1 : -1)))];
    list.forEach(function (b) { b.tabIndex = b === to ? 0 : -1; });
    to.focus();
    mark(bar.parentNode, to.classList.contains('is-shown') ? null : to);
  });
  // while a tile is held the finger moves it, not the page (a touch-action set now would come too late for this
  // touch); everything else scrolls as usual
  root.addEventListener('touchmove', function (e) { if (drag) e.preventDefault(); }, { passive: false });
  root.addEventListener('contextmenu', function (e) { if (press || drag || scrub) e.preventDefault(); });
  root.addEventListener('dragstart', function (e) { if (dayOf(e.target) || (e.target.closest && e.target.closest('#coach-road'))) e.preventDefault(); });
  root.addEventListener('click', function (e) {
    // the click a pointer sends after a scrub or a drag is theirs (a key's click, detail 0, is always the key's)
    var pointer = e.detail !== 0;
    if (pointer && Date.now() < roadQuiet && e.target.closest && e.target.closest('#coach-road')) { e.preventDefault(); e.stopPropagation(); return; }
    if (pointer && Date.now() < quietUntil && dayOf(e.target)) { e.preventDefault(); e.stopPropagation(); }
    quietUntil = 0;
    var step = e.target.closest && e.target.closest('#coach-prev, #coach-next');
    if (step) leave(step.id === 'coach-next' ? 'next' : 'prev');
  }, true);

  // ── the weeks: a horizontal swipe is ‹ or ›, the same links (and the same addresses) ──
  function leave(direction) {
    var grid = document.querySelector('#coach-main .pf-coach-grid');
    slide = direction;
    if (grid) grid.classList.add('is-leave-' + direction);
  }
  root.addEventListener('touchstart', function (e) {
    var t = e.touches[0];
    swipe = null;
    if (e.touches.length !== 1 || drag || !e.target.closest || !e.target.closest('#coach-main .pf-coach-grid')) return;
    if (t.clientX < EDGE || t.clientX > innerWidth - EDGE) return;  // the system's own edge gestures
    swipe = { x: t.clientX, y: t.clientY, at: Date.now() };
  }, { passive: true });
  root.addEventListener('touchend', function (e) {
    var s = swipe, t = e.changedTouches[0];
    swipe = null;
    if (!s || drag || e.touches.length || !t) return;
    var dx = t.clientX - s.x, dy = t.clientY - s.y;
    if (Date.now() - s.at > 800 || Math.abs(dx) < SWIPE || Math.abs(dx) < 2 * Math.abs(dy)) return;
    var link = document.getElementById(dx < 0 ? 'coach-next' : 'coach-prev');
    if (link) link.click();
  }, { passive: true });
  root.addEventListener('touchcancel', function () { swipe = null; }, { passive: true });

  function hint() {
    var line = document.getElementById('coach-drag-hint');
    if (!line) return;
    var seen = false;
    try { seen = localStorage.getItem(HINTED) === '1'; } catch (_) { /* shown */ }
    line.hidden = seen;
  }
  document.addEventListener('htmx:afterSwap', function () {
    pending = null;
    var grid = document.querySelector('#coach-main .pf-coach-grid');
    if (slide && grid) {
      grid.classList.add('is-enter-' + slide);
      grid.addEventListener('animationend', function () { grid.classList.remove('is-enter-next', 'is-enter-prev'); }, { once: true });
    }
    slide = null;
    // the session dropped, in its new day: a short pop
    var moved = popped && document.querySelector('#coach-main .pf-coach-tile[data-move="' + popped + '"]');
    if (moved) moved.classList.add('is-pop');
    popped = null;
    hint();
  });
  document.addEventListener('htmx:afterRequest', function (e) {
    if (e.detail.successful) return;
    var grid = document.querySelector('#coach-main .pf-coach-grid');
    if (grid) grid.classList.remove('is-leave-next', 'is-leave-prev');
    slide = null;
    popped = null;
    var bars = barsOf();
    if (bars) mark(bars, null);
    var p = pending;
    pending = null;
    if (!p || !p.home.isConnected) return;
    p.block.classList.remove('is-pending');
    p.home.insertBefore(p.block, p.next && p.next.parentNode === p.home ? p.next : null);
  });
  hint();
})();
