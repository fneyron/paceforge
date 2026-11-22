/* Coaching › the calendar by hand. A session is held, then dropped on another day: the move the « Déplacer » forms
   post, to the same address, with the same fields. A horizontal swipe on the weeks is ‹ or ›. Nothing else: the
   keyboard, a screen reader and a browser without JavaScript keep « Déplacer » and the links.
   - The server says everything: which days take a session and which would put two hard days side by side
     (data-to, data-warn on its block, coaching_view.drops), what the block is called while it is held (data-what).
     This file only reads them; after the drop the server decides, and its answer redraws the calendar.
   - A finger holds HOLD ms without moving (a move before that is the page's scroll or a swipe); a mouse moves MOUSE px.
   - Bound once on the page's container: htmx replaces the calendar after every action. */
(function () {
  'use strict';
  var root = document.querySelector('.pf-coach');
  if (!root || root.dataset.byHand) return;
  root.dataset.byHand = '1';
  var HOLD = 250, SLOP = 8, MOUSE = 5, SWIPE = 48, EDGE = 24;
  var press = null, drag = null, pending = null, swipe = null, quietUntil = 0, slide = null;
  var HINTED = 'pf-coach-by-hand';

  function words(s) { return s ? s.split(' ') : []; }
  function dayOf(el) { return el && el.closest ? el.closest('.pf-coach-grid[data-v] a.pf-coach-day[data-day]') : null; }
  // the block under the finger when it can be picked up, else the day's first session that can, else its grey run
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

  function ghostOf(block) {
    var ghost = document.createElement('div');
    ghost.className = 'pf-coach-ghost';
    ghost.setAttribute('aria-hidden', 'true');
    var copy = block.cloneNode(false);
    copy.removeAttribute('style');
    var label = document.createElement('span');
    label.textContent = block.dataset.what || '';
    ghost.append(copy, label);
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
    follow(p.x, p.y);
    return true;
  }

  // the day under the finger is marked; the held block floats above the finger, and above that day's number (the
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
    var bottom = Math.min(y - (drag.type === 'touch' ? 34 : 16), day ? day.getBoundingClientRect().top - 6 : Infinity);
    var left = Math.max(4, Math.min(innerWidth - w - 4, x - w / 2));
    g.style.transform = 'translate(' + Math.round(left) + 'px,' + Math.round(Math.max(4, bottom - h)) + 'px)';
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
    // shown in its new day at once; the server's answer redraws the calendar, and an answer that never comes puts
    // the block back (the app's toast says the request failed)
    pending = { block: t.block, home: t.block.parentNode, next: t.block.nextSibling };
    t.into.querySelector('.pf-coach-blocks').appendChild(t.block);
    t.block.classList.add('is-pending');
    try { localStorage.setItem(HINTED, '1'); } catch (_) { /* private mode: the hint comes back, nothing else */ }
    window.htmx.ajax('POST', t.url, { source: grid, target: '#coach-main', swap: 'outerHTML',
      values: { to: t.to, v: grid.dataset.v, w: grid.dataset.w } });
  }

  function release() {
    if (!press) return;
    clearTimeout(press.timer);
    press = null;
  }

  root.addEventListener('pointerdown', function (e) {
    if (drag || press || !e.isPrimary || (e.pointerType === 'mouse' && e.button !== 0)) return;
    var day = dayOf(e.target), block = day && blockOf(day, e.target);
    if (!block) return;
    press = { id: e.pointerId, type: e.pointerType, day: day, block: block, x0: e.clientX, y0: e.clientY,
      x: e.clientX, y: e.clientY, timer: 0 };
    if (e.pointerType === 'touch') press.timer = setTimeout(function () { if (press) lift(); }, HOLD);
  });
  document.addEventListener('pointermove', function (e) {
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
    if (press && e.pointerId === press.id) release();
    // a hold let go where it started is a tap: the day opens, nothing moves
    else if (drag && e.pointerId === drag.id) { if (drag.moved) follow(e.clientX, e.clientY); finish(!drag.moved); }
  });
  document.addEventListener('pointercancel', function (e) {
    if (press && e.pointerId === press.id) release();
    else if (drag && e.pointerId === drag.id) finish(true);
  });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && drag) { e.preventDefault(); finish(true); } });
  // while a session is held the finger moves it, not the page (a touch-action set now would come too late for
  // this touch); everything else scrolls as usual
  root.addEventListener('touchmove', function (e) { if (drag) e.preventDefault(); }, { passive: false });
  root.addEventListener('contextmenu', function (e) { if (press || drag) e.preventDefault(); });
  root.addEventListener('dragstart', function (e) { if (dayOf(e.target)) e.preventDefault(); });
  root.addEventListener('click', function (e) {
    if (Date.now() < quietUntil && dayOf(e.target)) { e.preventDefault(); e.stopPropagation(); }
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
    hint();
  });
  document.addEventListener('htmx:afterRequest', function (e) {
    if (e.detail.successful) return;
    var grid = document.querySelector('#coach-main .pf-coach-grid');
    if (grid) grid.classList.remove('is-leave-next', 'is-leave-prev');
    slide = null;
    var p = pending;
    pending = null;
    if (!p || !p.home.isConnected) return;
    p.block.classList.remove('is-pending');
    p.home.insertBefore(p.block, p.next && p.next.parentNode === p.home ? p.next : null);
  });
  hint();
})();
