/* Coaching › the options in a bottom sheet. Without this file they are a section at the end of the page (#coach-options:
   « Adapter à ma forme », the tiles, every tool after them, « Refaire le plan », « Arrêter le coaching »), which the
   round button and every ?outil=…#… address reach. With it the section moves into a modal <dialog>:
   - the round button opens it on its tiles; a tile opens its tool inside it, « ‹ Options » comes back to the tiles;
   - the page behind is inert (the dialog's own focus trap) and does not scroll; Escape and a tap on the scrim close
     it, the focus goes back to the round button; the sheet scrolls inside when a tool is long;
   - a page that comes back from one of its forms (?outil=…, #coach-tool-…: the server's `open`) opens it on that
     tool, with its message; a tool's htmx answer (a new code, an access removed, a new link) keeps it open there. */
(function () {
  'use strict';
  var section = document.getElementById('coach-options'), root = document.querySelector('.pf-coach');
  if (!section || !root || typeof HTMLDialogElement !== 'function' || root.dataset.sheet) return;
  root.dataset.sheet = '1';
  var OPEN = { fixed: 'coach-tool-fixed', feed: 'coach-tool-feed', agenda: 'coach-tool-agenda', share: 'coach-tool-share', how: 'coach-tool-how' };
  var dialog = document.createElement('dialog');
  dialog.className = 'pf-coach-dialog';
  dialog.setAttribute('aria-labelledby', 'coach-options-h');
  section.parentNode.insertBefore(dialog, section);
  dialog.appendChild(section);
  root.classList.add('has-sheet');
  var button = document.getElementById('coach-options-btn'), back = document.getElementById('coach-sheet-back');
  var close = document.getElementById('coach-sheet-close'), title = document.getElementById('coach-options-h');
  title.tabIndex = -1;
  close.hidden = false;
  var current = null, opener = null, known = null;

  function tools() { return document.getElementById('coach-tools'); }
  function show(id, focus) {
    var t = tools(), panel = id && document.getElementById(id);
    if (!t) return;
    known = t;
    current = panel ? id : null;
    t.querySelectorAll('.pf-coach-panel').forEach(function (p) { p.classList.toggle('is-open', p === panel); });
    t.classList.toggle('is-tool', !!panel);
    section.classList.toggle('is-tool', !!panel);  // « ‹ Options » takes the title's place
    back.hidden = !panel;
    if (focus === false) return;
    if (panel) (panel.querySelector('.pf-coach-panel-h') || panel).focus({ preventScroll: true });
    else if (opener && opener.isConnected) opener.focus({ preventScroll: true });
    else title.focus({ preventScroll: true });
    if (!panel) opener = null;
    dialog.scrollTop = 0;
  }
  function open(id, target) {
    if (!dialog.open) {
      dialog.showModal();
      document.documentElement.classList.add('pf-coach-locked');
    }
    show(id);
    if (target && target.scrollIntoView && target !== document.getElementById(id)) target.scrollIntoView({ block: 'nearest' });
  }
  dialog.addEventListener('close', function () {
    document.documentElement.classList.remove('pf-coach-locked');
    show(null, false);
    if (button) button.focus({ preventScroll: true });
  });
  // Tab and Shift+Tab go round inside the sheet (the modal dialog alone would let the focus out to the browser)
  dialog.addEventListener('keydown', function (e) {
    if (e.key !== 'Tab') return;
    var all = Array.prototype.filter.call(dialog.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]), select, textarea, summary, [tabindex="0"]'),
      function (el) { return el.getClientRects().length && !el.closest('[hidden]') && !el.closest('details:not([open]) > :not(summary)'); });
    if (!all.length) return;
    var first = all[0], last = all[all.length - 1], at = document.activeElement;
    if (e.shiftKey && (at === first || !dialog.contains(at) || at === title)) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && at === last) { e.preventDefault(); first.focus(); }
  });
  // a tap on the scrim: the click lands on the dialog itself (its content fills it)
  dialog.addEventListener('click', function (e) { if (e.target === dialog) dialog.close(); });
  close.addEventListener('click', function () { dialog.close(); });
  back.addEventListener('click', function () { show(null); });
  if (button) button.addEventListener('click', function (e) { e.preventDefault(); opener = null; open(null); });
  section.addEventListener('click', function (e) {
    var tile = e.target.closest && e.target.closest('[data-tool]');
    if (!tile || !document.getElementById(tile.dataset.tool)) return;
    e.preventDefault();
    opener = tile;
    show(tile.dataset.tool);
  });
  // a tool's htmx answer replaces #coach-tools: the sheet stays on that tool (watched on the sheet itself: an
  // outerHTML swap's own events start from the element it removed)
  new MutationObserver(function () {
    var t = tools();
    if (!dialog.open || !t || t === known) return;  // (htmx copies the old element's attributes while it settles)
    known = t;
    var asked = OPEN[t.dataset.open] || current, focused = dialog.contains(document.activeElement);
    show(asked, false);
    if (!focused) { var p = asked && document.getElementById(asked); (p ? p.querySelector('.pf-coach-panel-h') : title).focus({ preventScroll: true }); }
  }).observe(section, { childList: true });
  // … and once htmx has settled it (it puts the new elements' own classes back then)
  document.addEventListener('htmx:afterSettle', function () {
    var t = tools();
    if (dialog.open && t && dialog.contains(t)) show(current, false);
  });
  // where the page lands: the tool its form came back to (the server's `open`), or the fragment asked for
  function landing(event) {
    var hash = location.hash.length > 1 && document.getElementById(decodeURIComponent(location.hash.slice(1)));
    var t = tools(), asked = t && t.dataset.open;
    if (hash && section.contains(hash)) {
      var panel = hash.closest('.pf-coach-panel');
      open(panel ? panel.id : null, hash);
    } else if (asked && !event) {
      open(OPEN[asked] || null);
    }
  }
  // a form's answer: the page behind starts at its top (the browser went to the fragment before the sheet took it)
  var hashed = location.hash.length > 1 && document.getElementById(decodeURIComponent(location.hash.slice(1)));
  if ((hashed && section.contains(hashed)) || (tools() && tools().dataset.open)) scrollTo(0, 0);
  landing();
  addEventListener('hashchange', landing);
})();
