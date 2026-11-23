// Coaching (beta, every account): the athlete's page (the calendar: the debrief first, two weeks, the day opened under
// them, the plan behind a fold; a session held and dropped on another day, a swipe between the weeks), the follower's pages and the onboarding (a plan already proposed), in a browser — at
// 320 and 393 px (the web site; inside the phone app that has the native Coaching tab; inside an installed app older
// than that tab), at 1280 px, light and dark. Local
// synthetic preview only.
// Start three fixtures (tests/browser/coaching_preview.py), each its own temporary database:
//   PF_COACHING_AS=athlete uvicorn tests.browser.coaching_preview:app --host 127.0.0.1 --port 8861
//   PF_COACHING_AS=coach   uvicorn tests.browser.coaching_preview:app --host 127.0.0.1 --port 8862
//   PF_COACHING_AS=new     uvicorn tests.browser.coaching_preview:app --host 127.0.0.1 --port 8863
// The third one also holds an account with a running habit and no plan, signed in on request (POST
// /__preview/coaching/as/habit): what the onboarding proposes from a history.
// Run with Playwright installed: node tests/browser/coaching-visuals.cjs
// Screenshots (deviceScaleFactor 2) go to PF_COACHING_SCREENSHOTS.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const athlete = process.env.PF_COACHING_ATHLETE || 'http://127.0.0.1:8861';
const coach = process.env.PF_COACHING_COACH || 'http://127.0.0.1:8862';
const fresh = process.env.PF_COACHING_NEW || 'http://127.0.0.1:8863';
const out = process.env.PF_COACHING_SCREENSHOTS || '/tmp/paceforge-coaching';

async function synthetic(url, as) {
  const fixture = await (await fetch(url + '/__preview/coaching')).json();
  assert.equal(fixture.synthetic, true, 'Never run against a real account');
  assert.equal(fixture.as, as);
  return fixture;
}

// What the phone app injects into every page (mobile/src/navigation.ts, appShellScript), read from the source and
// bound to the fixture's origin instead of paceforge.fr. `old`: the same script without the two statements that
// announce the native Coaching tab, as the versions installed before it run it.
const SHELL = (() => {
  const source = fs.readFileSync(path.resolve(__dirname, '../../mobile/src/navigation.ts'), 'utf8');
  const script = /export const appShellScript = `([^`]+)`;/.exec(source)[1];
  const marker = /^\s*window\.paceforgeNativeCoaching = 1;\n\s*document\.documentElement\.classList\.add\('pf-native-coaching'\);\n/m;
  assert.ok(marker.test(script), 'the shell announces its Coaching tab');
  // « Mon agenda »'s Google Agenda and Outlook in the system's sign-in sheet: announced by the shells since (`noagenda`:
  // one with the Coaching tab but from before that; `old` predates both)
  const agenda = /^\s*window\.paceforgeNativeAgenda = 1;\n\s*document\.documentElement\.classList\.add\('pf-native-agenda'\);\n/m;
  assert.ok(agenda.test(script), 'the shell announces its agenda sign-in sheet');
  const bind = (text, origin) => text.split('${JSON.stringify(ORIGIN)}').join(JSON.stringify(origin));
  return { new: origin => bind(script, origin), old: origin => bind(script.replace(marker, '').replace(agenda, ''), origin),
    noagenda: origin => bind(script.replace(agenda, ''), origin) };
})();

// A page as a phone or a desktop sees it. `app`: inside the phone app's WebView — 'new', the app with the native
// Coaching tab; 'old', an installed version from before it. Its shell hides the web header and tab bar and says what
// it can do on <html>; base.html adds .pf-native when ReactNativeWebView exists.
async function open(browser, { width, dark = false, app = false, height = 852 }) {
  const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 2, isMobile: width < 700,
    hasTouch: width < 700, colorScheme: dark ? 'dark' : 'light', reducedMotion: 'reduce' });
  page.errors = [];
  page.on('pageerror', error => page.errors.push(error.message));
  if (app) await page.addInitScript(([shell, origins]) => {
    window.ReactNativeWebView = { postMessage() {} };
    // as the WebView does: once at the document's start (its root element exists), once when it has loaded
    const run = () => { if (shell[location.origin]) new Function(shell[location.origin])(); };
    if (document.documentElement) run();
    else new MutationObserver((_, o) => { if (document.documentElement) { o.disconnect(); run(); } }).observe(document, { childList: true });
    addEventListener('load', run);
  }, [Object.fromEntries([athlete, coach, fresh].map(url => [new URL(url).origin, SHELL[app](new URL(url).origin)]))]);
  return page;
}

// The three ways a page is seen at phone width, and what each one shows of the ways in and out.
const SHELLS = [false, 'new', 'old'];
const suffix = app => (app ? `-app${app === 'old' ? '-old' : ''}` : '');

// One htmx tap: the request it sends has answered and the calendar part is swapped.
async function swap(page, action) {
  const answered = page.waitForResponse(r => r.request().headers()['hx-request'] === 'true' && r.url().includes('/coaching'));
  await action();
  assert.ok((await answered).ok(), 'the partial answered');
  await page.waitForFunction(() => !document.querySelector('.htmx-request, .htmx-swapping, .htmx-settling'));
}

// The onboarding's week, drawn again after a chip changed: its request has answered and the parts are swapped.
async function redrawn(page, action) {
  const answered = page.waitForResponse(r => r.url().includes('/coaching/apercu'));
  await action();
  const response = await answered;
  assert.equal(response.status(), 200, 'the preview answers 200');
  assert.equal(response.request().method(), 'GET', 'the preview only reads');
  await page.waitForFunction(() => !document.querySelector('.htmx-request, .htmx-swapping, .htmx-settling'));
}

// The bars of the onboarding's week: [column, kind, duration] in reading order.
const previewBars = page => page.locator('#coach-preview .pf-coach-pv-col').evaluateAll(cols => cols.flatMap((col, i) =>
  [...col.querySelectorAll('.pf-coach-bar-s')].map(bar => [i, bar.className.replace(/.*is-/, ''), bar.previousElementSibling.textContent])));

// What is seen of a kind of control in the swapped part (a closed fold's content does not count).
const seen = (page, selector) => page.locator(selector).evaluateAll(all => all.filter(el => {
  if (!el.getClientRects().length) return false;
  for (let fold = el.closest('details:not([open])'); fold; fold = fold.parentElement && fold.parentElement.closest('details:not([open])')) {
    if (!(el.matches('summary') && el.parentElement === fold)) return false;
  }
  return true;
}).map(el => el.innerText.replace(/\s+/g, ' ').trim()));

async function noOverflow(page, label) {
  const sizes = await page.evaluate(() => ({ viewport: innerWidth, page: document.documentElement.scrollWidth }));
  assert.ok(sizes.page <= sizes.viewport + 1, label + ': horizontal overflow ' + JSON.stringify(sizes));
  const clipped = await page.evaluate(() => [...document.querySelectorAll('.pf-coach-card, .pf-coach-entry')].flatMap(card => {
    const box = card.getBoundingClientRect();
    return [...card.querySelectorAll('h2, h3, p, a, button, select, input, textarea, label, summary, svg.pf-effort, figure, .pf-coach-pv, .pf-coach-gauge')].flatMap(el => {
      if (!el.getClientRects().length || el.closest('.sr-only')) return [];
      const r = el.getBoundingClientRect();
      return r.left < box.left - 1 || r.right > box.right + 1 ? [(el.id || el.className || el.tagName) + ' ' + Math.round(r.right - box.right)] : [];
    });
  }));
  assert.deepEqual(clipped, [], label + ': content outside its card');
}

// Every visible control of the swapped part is at least 40 px tall (the house's 44 px targets, 40 for the round steps).
async function targets(page, label) {
  const small = await page.evaluate(() => [...document.querySelectorAll('#coach-main a, #coach-main button, #coach-main select, #coach-main summary')].flatMap(el => {
    if (!el.getClientRects().length) return [];
    // the fields of a closed fold are not shown (the browser keeps their boxes: content-visibility)
    const fold = el.closest('details:not([open])');
    if (fold && !(el.matches('summary') && el.parentElement === fold)) return [];
    const r = el.getBoundingClientRect();
    return r.height < 40 || r.width < 36 ? [(el.id || el.className) + ' ' + Math.round(r.width) + '×' + Math.round(r.height)] : [];
  }));
  assert.deepEqual(small, [], label + ': touch targets under 40 px');
}

async function shot(page, name) {
  const height = await page.evaluate(() => document.documentElement.scrollHeight);
  const size = page.viewportSize();
  await page.setViewportSize({ width: size.width, height: Math.max(size.height, height) });
  await page.evaluate(() => scrollTo({ top: 0, behavior: 'instant' }));
  await page.screenshot({ path: `${out}/${name}.png` });
  await page.setViewportSize(size);
}

async function theme(page, dark) {
  await page.evaluate(d => document.documentElement.classList.toggle('dark', d), dark);
}

// A finger, as the phone sends it (Chromium's own touch events, through the compositor: a stroke really scrolls the
// page, a hold really waits). `stroke`: a quick move, the page's scroll or a swipe; `hold`: still for 350 ms (the
// calendar lifts a session after 250), then moved — the finger stays down when `end` is false.
async function fingerOf(page) {
  const cdp = await page.context().newCDPSession(page);
  const send = (type, points) => cdp.send('Input.dispatchTouchEvent', { type, touchPoints: points });
  const path = async (from, to, steps) => {
    for (let i = 1; i <= steps; i++) {
      await send('touchMove', [{ x: from.x + (to.x - from.x) * i / steps, y: from.y + (to.y - from.y) * i / steps }]);
      await page.waitForTimeout(16);
    }
  };
  return {
    stroke: async (from, to) => { await send('touchStart', [from]); await path(from, to, 6); await send('touchEnd', []); },
    hold: async (from, to, end = true) => {
      await send('touchStart', [from]); await page.waitForTimeout(350); await path(from, to, 12);
      if (end) await send('touchEnd', []);
    },
    move: (from, to) => path(from, to, 8),
    up: () => send('touchEnd', []),
  };
}

async function centre(page, selector) {
  const b = await page.locator(selector).boundingBox();
  return { x: b.x + b.width / 2, y: b.y + b.height / 2 };
}

// What the calendar shows while a session is held: the classes of the days, the ghost's box, the page's width.
async function held(page) {
  return page.evaluate(() => {
    const ghost = document.querySelector('.pf-coach-ghost');
    const days = [...document.querySelectorAll('#coach-main .pf-coach-day')];
    return { dragging: document.querySelector('#coach-main.is-dragging') !== null, ghost: ghost && ghost.getBoundingClientRect().toJSON(),
      label: ghost && ghost.textContent, drops: days.filter(d => d.classList.contains('is-drop')).map(d => d.id),
      warn: days.filter(d => d.classList.contains('is-drop-warn')).map(d => d.id),
      over: days.filter(d => d.classList.contains('is-over')).map(d => d.id),
      faded: days.filter(d => !d.matches('.is-drop, .is-home')).map(d => getComputedStyle(d).opacity),
      wide: document.documentElement.scrollWidth > innerWidth + 1 };
  });
}

(async () => {
  fs.mkdirSync(out, { recursive: true });
  const fixture = await synthetic(athlete, 'athlete');
  await synthetic(coach, 'coach');
  await synthetic(fresh, 'new');
  const browser = await chromium.launch({ headless: true });
  try {
    // ── the athlete's page: 320 and 393 px, light and dark, on the site and inside the app ──
    for (const width of [320, 393]) for (const dark of [false, true]) for (const app of SHELLS) {
      const key = `${width}-${dark ? 'dark' : 'light'}${suffix(app)}`;
      const page = await open(browser, { width, dark, app });
      await page.goto(athlete + '/coaching', { waitUntil: 'networkidle' });
      assert.match(await page.locator('h1').innerText(), /^Coaching\s+bêta$/i, key);
      assert.match(await page.locator('.pf-coach-goal').innerText(), /J‑54 · Trail des Crêtes/);
      // the time the plan runs on, said once, with the way to the race page
      assert.match(await page.locator('a.pf-coach-goal-time').innerText(), /^Objectif\s5h00$/, key);
      assert.match(await page.locator('a.pf-coach-goal-time').getAttribute('href'), /^\/simulator\/routes\/\d+$/, key);
      assert.ok((await page.locator('a.pf-coach-goal-time').boundingBox()).height >= 40, key + ': a 40 px target');
      // the form: this morning's index is low — today's quality session is a footing, one short reason, the way to
      // Santé, never the number; the option itself is a switch, on
      assert.match(await page.locator('.pf-coach-s-title').innerText(), /^Footing\s+\d+ min$/, key);
      assert.equal(await page.locator('.pf-coach-formline a[href="/sante"]').innerText(), 'Séance allégée\u00a0: récupération basse ce matin', key);
      assert.equal(await page.locator('.pf-coach-formline span').innerText(), 'La séance prévue passe à vendredi.', key);
      assert.doesNotMatch(await page.locator('.pf-coach-daycard').innerText(), /\d\s?%/, key + ': the index is Santé’s to print');
      // the agenda: today's session has its time, in a free slot of her work calendar
      assert.match(await page.locator('.pf-coach-slot-time').innerText(), /^12\sh\s00 – 12\sh\s30$/, key + ': the slot');
      // the page IS the calendar: the debrief asked first, then the weeks, the day opened under them, the plan's
      // chart behind a fold
      assert.match(await page.locator('.pf-coach-date').innerText(), /^Aujourd’hui · jeudi \d+ \S+$/, key);
      const boxes = {};
      for (const part of ['debrief', 'calendar', 'grid', 'daycard', 'planfold']) boxes[part] = await page.locator('.pf-coach-' + part).boundingBox();
      assert.ok(boxes.debrief.y + boxes.debrief.height <= boxes.calendar.y && boxes.grid.y + boxes.grid.height <= boxes.daycard.y
        && boxes.calendar.y + boxes.calendar.height <= boxes.planfold.y, key + ': debrief, weeks, day, plan');
      // this week and the next, whole, within the first phone screen (clear of the tab bar on the site)
      const weekBottoms = await page.locator('.pf-coach-week').evaluateAll(all => all.map(w => w.getBoundingClientRect().bottom));
      const screenEnd = app ? 852 : (await page.locator('.pf-tabbar').boundingBox()).y;
      assert.equal(weekBottoms.length, 2, key + ': two weeks');
      assert.ok(weekBottoms[1] <= screenEnd, key + ': both weeks within the first screen ' + JSON.stringify([weekBottoms, screenEnd]));
      // how hard, at a glance: a steady session shows a gauge of four (the words say it too), never a flat block
      const gauge = page.locator('.pf-coach-session .pf-coach-gauge');
      assert.equal(await gauge.getAttribute('aria-label'), 'Effort facile : niveau 2 sur 4', key);
      assert.deepEqual(await gauge.locator('i').evaluateAll(all => all.map(i => getComputedStyle(i).backgroundColor)).then(c => [c[0] === c[1], c[1] === c[2], c[2] === c[3]]), [true, false, true], key + ': two steps of four are filled');
      assert.equal(await page.locator('.pf-coach-session svg.pf-effort').count(), 0, key + ': no profile for a steady session');
      assert.match(await page.locator('.pf-coach-s-guide').innerText(), /^Facile\s: tu peux tenir une conversation\. 112–148\sbpm$/, key);
      // ONE main action; « Déplacer » beside it, secondary; the others quiet (smaller, no fill)
      assert.deepEqual(await seen(page, '#coach-main .pf-btn-primary'), ['C’est fait'], key + ': one main action');
      const main = await page.locator('[id^="coach-done-"]').boundingBox();
      const move = await page.locator('[id^="coach-move-"]').boundingBox();
      assert.ok(main.height >= 48 && main.width > boxes.daycard.width * 0.5, key + ': « C’est fait » takes most of the row');
      assert.ok(move.height >= 44 && Math.abs(move.y - main.y) < 2 && move.x > main.x, key + ': « Déplacer » beside it');
      assert.equal(await page.locator('[id^="coach-move-"]').innerText(), 'Déplacer', key);
      assert.deepEqual(await seen(page, '.pf-coach-daycard .pf-coach-quietbtn'), ['Autre créneau', 'Pas faite', 'Modifier'], key + ': the quiet actions');
      assert.deepEqual(await page.locator('.pf-coach-daycard .pf-coach-quietbtn').evaluateAll(all => [...new Set(all.map(el => getComputedStyle(el).backgroundColor))]), ['rgba(0, 0, 0, 0)'], key + ': no fill');
      // the days: fourteen, each its sessions as blocks (kind, status, height), a word, the time given — no legend
      assert.equal(await page.locator('.pf-coach-day').count(), 14, key + ': fourteen days');
      assert.equal(await page.locator('.pf-coach-day.is-today').count(), 1, key + ': today is highlighted');
      assert.equal(await page.locator(`#coach-day-${fixture.day}.is-selected`).count(), 1, key + ': today by default');
      const names = await page.locator('.pf-coach-day').evaluateAll(all => all.map(a => a.getAttribute('aria-label')));
      for (const name of names) assert.match(name, /^(lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche) \d+ \S+ : (repos|une sortie hors plan|.+ \d.*, (prévue|faite|pas faite))/, key + ': a full name, ' + name);
      assert.match(names[3], /^jeudi \d+ \S+ : Footing 30 minutes, prévue à 12\sh\s00 \(aujourd’hui\)$/, key);
      const week = page.locator('.pf-coach-week.is-current');
      const bars = await week.locator('.pf-coach-bar-s').evaluateAll(all => all.map(b => ({
        cls: b.className.replace('pf-coach-bar-s ', ''), h: b.getBoundingClientRect().height, fill: getComputedStyle(b).backgroundColor })));
      assert.deepEqual(bars.map(b => b.cls), ['is-easy is-done', 'is-extra is-done', 'is-easy is-planned', 'is-hard is-planned', 'is-long is-planned'], key + ': the week’s sessions, and the run outside the plan');
      assert.ok(bars[2].h < bars[3].h && bars[3].h < bars[4].h, key + ': taller with the duration ' + JSON.stringify(bars.map(b => b.h)));
      assert.notEqual(bars[0].fill, bars[2].fill, key + ': done is full, planned is outlined');
      assert.deepEqual(await week.locator('.pf-coach-word').allInnerTexts(), ['Footing', 'Sortie', 'Footing', 'Côtes', 'Longue'], key + ': a word under each');
      assert.match(await page.locator(`#coach-day-${fixture.day} .pf-coach-when`).innerText(), /^12\sh\s00$/, key + ': the time given');
      assert.equal(await page.locator('#coach-main .pf-coach-legend').count(), 0, key + ': no legend');
      assert.match(await week.locator('.pf-coach-week-h').innerText(), /^Cette semaine\n\d\S* \/ \d\S*\n\d.* faites sur .* prévues\nSpécifique$/, key + ': the week’s line, fait / prévu, its phase');
      // a run outside the plan: a grey block on its day, « Sortie »
      const wednesday = page.locator('.pf-coach-week.is-current .pf-coach-day').nth(2);
      assert.match(await wednesday.getAttribute('aria-label'), /^mercredi \d+ \S+ : une sortie hors plan$/, key);
      assert.equal(await wednesday.locator('.pf-coach-bar-s.is-extra').count(), 1, key + ': a grey block');
      // the debrief: Tuesday's run arrived, its ressenti not said — one question at the top, five levels, a mark on its day
      assert.equal(await page.locator('#debrief.pf-coach-debrief h2').innerText(), 'Ta sortie de mardi est arrivée\u00a0— c’était comment\u00a0?', key);
      assert.deepEqual(await page.locator('#debrief [data-feel]').allInnerTexts(), ['Très facile', 'Facile', 'Bien', 'Dur', 'Très dur'], key);
      for (const h of await page.locator('#debrief [data-feel]').evaluateAll(all => all.map(b => b.getBoundingClientRect().height))) assert.ok(h >= 44, key + ': 44 px levels');
      const tuesday = page.locator('.pf-coach-week.is-current .pf-coach-day').nth(1);
      assert.equal(await tuesday.locator('.pf-coach-todo').count(), 1, key + ': a mark on Tuesday');
      assert.match(await tuesday.getAttribute('aria-label'), /, faite, à débriefer$/, key);
      assert.equal(await page.locator('.pf-coach-todo').count(), 1, key + ': one mark');
      // « Le plan »: behind its fold, closed; opened, the chart and its phases
      assert.equal(await page.locator('#coach-plan').evaluate(el => el.open), false, key + ': the plan’s chart waits');
      assert.equal(await page.locator('#coach-main svg.pf-viz-svg').isVisible(), false, key);
      assert.ok((await page.locator('#coach-plan-toggle').boundingBox()).height >= 44, key + ': a 44 px row');
      // the tools: one row, closed — the page ends two rows after the plan
      assert.equal(await page.locator('#coach-options').evaluate(el => el.open), false, key + ': the options are closed');
      assert.equal(await page.locator('#coach-tools').isVisible(), false, key);
      assert.deepEqual(await seen(page, '.pf-coach-more summary'), ['Comment ça marche', 'Options du plan Calendrier, agenda, partage, refaire le plan'], key + ': two rows');
      if (!app) assert.ok(await page.evaluate(() => document.documentElement.scrollHeight) < 1900, key + ': the page stays short');
      // opened, the plan's chart (it stays open across the taps that redraw the calendar)
      if (!dark && !app) {
        await page.locator('#coach-plan-toggle').click();
        assert.ok(await page.locator('#coach-main svg.pf-viz-svg').isVisible(), key + ': the plan chart');
        assert.equal(await page.locator('.pf-coach-plan .pf-plan-phase').count(), 3, key + ': the phases are marked');
        await swap(page, () => page.locator(`#coach-day-${fixture.day}`).click());
        assert.equal(await page.locator('#coach-plan').evaluate(el => el.open), true, key + ': still open after a tap');
        await targets(page, key + ' plan');
        if (width === 393) await shot(page, 'athlete-393-plan');
        await page.locator('#coach-plan-toggle').click();
      }
      // inside the app the shell owns the header and the tabs; only an app older than its Coaching tab needs the
      // page's own way back to Activités
      assert.equal(await page.locator('.pf-top').isVisible(), !app, key);
      assert.equal(await page.locator('.pf-tabbar').isVisible(), !app, key);
      assert.equal(await page.evaluate(() => document.documentElement.classList.contains('pf-native-coaching')), app === 'new', key);
      assert.equal(await page.locator('a.pf-back[href="/activities"]').isVisible(), app === 'old', key + ': « ‹ Activités » in an older app only');
      if (!app) assert.deepEqual(await page.locator('.pf-tabbar a').allInnerTexts(), ['Courses', 'Activités', 'Santé', 'Coaching'], key + ': a 4th tab');
      if (!app) assert.equal(await page.locator('.pf-tabbar a[aria-current="page"]').innerText(), 'Coaching', key);
      await noOverflow(page, key);
      await targets(page, key);
      if (width === 393) await shot(page, `athlete-393-calendar${dark ? '-dark' : ''}${suffix(app)}`);
      if (width === 320 && !app) await shot(page, `athlete-320-calendar${dark ? '-dark' : ''}`);
      if (width === 393 && !app) await page.screenshot({ path: `${out}/athlete-393-first-screen${dark ? '-dark' : ''}.png` });
      // « Options du plan »: the form option is a switch, on, with its wording; then the calendar
      await page.locator('#coach-options-toggle').click();
      const toggle = page.locator('#coach-form-switch');
      assert.equal(await toggle.getAttribute('aria-checked'), 'true', key);
      assert.ok((await toggle.boundingBox()).height >= 44, key + ': the switch is a 44 px target');
      assert.match(await page.locator('#coach-form-note').innerText(), /^Activé le \S+ \d+ \S+\. Un matin où ta récupération de Santé est basse, ta séance dure devient un footing\. La personne qui suit ton plan ne voit jamais pourquoi\.$/, key);
      assert.deepEqual((await page.locator('#coach-tools .pf-tool').allInnerTexts()).map(t => t.trim().split('\n')[0].trim()), ['Adapter mes séances à ma forme', 'Ma séance fixe', 'Ajouter à mon calendrier', 'Mon agenda', 'Partager mon plan', 'Suivre le plan de quelqu’un', 'Refaire le plan', 'Arrêter le coaching'], key + ': every option is still there');
      for (const box of await page.locator('#coach-tools .pf-tool').evaluateAll(all => all.map(el => el.getBoundingClientRect().height))) assert.ok(box >= 44, key + ': 44 px rows');
      // the plan is automatic: nothing the athlete reads says « coach » (the feature keeps its name)
      assert.doesNotMatch(await page.locator('#main-content').innerText(), /\bcoachs?\b/i, key + ': never « coach »');
      await noOverflow(page, key + ' options');
      if (width === 393 && !app) await shot(page, `athlete-393-options${dark ? '-dark' : ''}`);
      // « Ma séance fixe »: a row of the options — its weekday (one choice), its duration, its name, « Enregistrer »
      assert.match(await page.locator('#coach-tool-fixed > summary').innerText(), /^Ma séance fixe\s+Une séance de ton club, chaque semaine$/, key);
      await page.locator('#coach-tool-fixed > summary').click();
      assert.equal(await page.locator('#coach-tool-fixed input[name="fixed_day"]').count(), 7, key + ': seven weekdays');
      assert.equal(await page.locator('#coach-tool-fixed input[name="fixed_day"]:checked').count(), 0, key + ': no weekday guessed');
      assert.equal(await page.locator('#coach-fixed-min').inputValue(), '75', key + ': 1 h 15 until a weekday is chosen');
      for (const id of ['#coach-fixed-save', '#coach-fixed-name']) assert.ok((await page.locator(id).boundingBox()).height >= 44, key + ': 44 px ' + id);
      assert.equal(await page.locator('#coach-fixed-remove').count(), 0, key + ': nothing to remove yet');
      await page.locator('#coach-fixed-day-2').check({ force: true });
      assert.match(await page.locator('#coach-tool-fixed [data-fixed-why]').innerText(), /^(Lu sur tes mercredis\.|Sans sortie ce jour-là : 1h15 par défaut\.)$/, key + ': the duration proposed, and why');
      await noOverflow(page, key + ' fixed');
      const card = await page.locator('#coach-options').boundingBox();
      const beyond = await page.locator('#coach-tool-fixed :is(.pf-coach-chip, select, input.pf-input, .pf-btn)').evaluateAll((all, right) =>
        all.filter(el => el.getClientRects().length && el.getBoundingClientRect().right > right + 0.5).map(el => el.outerHTML.slice(0, 60)), card.x + card.width);
      assert.deepEqual(beyond, [], key + ': « Ma séance fixe » stays inside its card');
      if (!app) await shot(page, `athlete-${width}-fixed${dark ? '-dark' : ''}`);
      await page.locator('#coach-tool-fixed > summary').click();
      // the calendar: a browser and the updated app get the subscription button (the app hands webcal:// to the
      // system); an older app drops that link, so it only shows the address to copy
      await page.locator('#coach-tool-feed > summary').click();
      assert.equal(await page.locator('#coach-tool-feed a[href^="webcal://"]').isVisible(), app !== 'old', key + ': « S’abonner au calendrier »');
      assert.ok(await page.locator('#coach-feed-url').isVisible() && await page.locator('[data-copy="coach-feed-url"]').isVisible(), key + ': the address to copy');
      await noOverflow(page, key + ' calendar');
      if (width === 393 && !dark && app) await shot(page, `athlete-393-feed${suffix(app)}`);
      assert.deepEqual(page.errors, [], key + ': browser errors');
      await page.close();
    }

    // ── the race and its plan: a long run's climb target, the race page's line ──
    for (const dark of [false, true]) {
      const page = await open(browser, { width: 393, dark });
      const sunday = new Date(fixture.day + 'T00:00:00Z');
      sunday.setUTCDate(sunday.getUTCDate() + (7 - sunday.getUTCDay()) % 7 + 7);  // next week's long run
      await page.goto(`${athlete}/coaching?d=${sunday.toISOString().slice(0, 10)}`, { waitUntil: 'networkidle' });
      assert.match(await page.locator('.pf-coach-s-title').innerText(), /^Sortie longue/);
      assert.match(await page.locator('.pf-coach-s-text').innerText(), /^Vise \+\d[\d\s]*m$/, 'a trail race: the climb to aim at');
      await noOverflow(page, 'climb target');
      await shot(page, `athlete-393-climb${dark ? '-dark' : ''}`);
      if (!dark) {
        await page.locator('a.pf-coach-goal-time').click();
        await page.waitForURL('**/simulator/routes/*');
        const line = page.locator('a.pf-rp-coaching');
        assert.match(await line.innerText(), /^Plan de coaching · semaine \d+ sur \d+ ›$/);
        assert.ok((await line.boundingBox()).height >= 44);
        await line.scrollIntoViewIfNeeded();
        await page.screenshot({ path: `${out}/race-393-coaching-line.png` });
        await line.click();
        await page.waitForURL('**/coaching');
      }
      assert.deepEqual(page.errors.filter(e => !/maplibre|WebGL/i.test(e)), [], 'race and plan: browser errors');
      await page.close();
    }

    // ── the agenda: next week's times, Tuesday's session sent to Friday, another slot, « Mon agenda » ──
    for (const dark of [true, false]) {  // the light pass ends on a tap that changes the time
      const page = await open(browser, { width: 393, dark });
      const friday = new Date(fixture.day + 'T00:00:00Z');
      friday.setUTCDate(friday.getUTCDate() + 8);  // the preview's day is a Thursday
      const iso = friday.toISOString().slice(0, 10);
      await page.goto(`${athlete}/coaching?d=${iso}&outil=agenda`, { waitUntil: 'networkidle' });
      assert.equal(await page.locator('.pf-coach-moves').innerText(), 'Mardi est plein\u00a0: séance déplacée à vendredi.');
      assert.equal(await page.locator('.pf-coach-week').nth(1).locator('.pf-coach-bar-s').count(), 3, 'Tuesday no longer holds a session');
      assert.equal(await page.locator(`#coach-day-${iso} .pf-coach-bar-s`).count(), 1, 'Friday does');
      assert.equal(await page.locator('#coach-today').innerText(), 'Aujourd’hui', 'another day has the way back to today');
      const slot = page.locator('.pf-coach-slot-time');
      assert.match(await slot.innerText(), /^6\sh\s00 – \d+\sh\s\d\d$/, 'the first free time of the day');
      // « Mon agenda »: the address masked, how its reading went, the three moments, the way to find the address
      const agenda = page.locator('#coach-tool-agenda');
      assert.equal(await page.locator('#coach-options').getAttribute('open'), '', 'the options open on the fold asked');
      assert.equal(await agenda.getAttribute('open'), '');
      assert.equal(await agenda.locator('.pf-coach-calrow b').innerText(), 'calendar.google.com/….ics');
      assert.match(await agenda.locator('.pf-coach-calrow small').innerText(), /^Lu aujourd’hui à 8\sh\s30$/);
      assert.doesNotMatch(await page.content(), /private-preview|stella%40example/, 'never the address itself');
      assert.equal(await agenda.locator('.pf-coach-moments input:checked').count(), 3);
      assert.ok(await agenda.locator('#coach-cal-url').isVisible() && await agenda.locator('#coach-cal-refresh').isVisible());
      for (const id of ['#coach-cal-add', '#coach-cal-refresh', `#coach-tool-agenda [id^="coach-cal-remove-"]`]) {
        assert.ok((await page.locator(id).boundingBox()).height >= 44, id + ': a 44 px target');
      }
      await noOverflow(page, 'agenda');
      await targets(page, 'agenda');
      await shot(page, `athlete-393-agenda${dark ? '-dark' : ''}`);
      if (!dark) {
        // another slot: the next free time, in place; it is then hers
        const before = await slot.innerText();
        await swap(page, () => page.locator('[id^="coach-slot-"]').click());
        assert.match(await page.locator('.pf-coach-slot-time').innerText(), /^6\sh\s30 – /);
        assert.notEqual(await page.locator('.pf-coach-slot-time').innerText(), before);
        await agenda.locator('.pf-coach-calhelp > summary').click();
        assert.match(await agenda.locator('.pf-coach-calhelp').innerText(), /Adresse secrète au format iCal/);
        await noOverflow(page, 'agenda help');
        await shot(page, 'athlete-393-agenda-help');
        // a refused address says why, in the fold, and nothing is fetched
        await page.locator('#coach-cal-url').fill('https://127.0.0.1/calendar.ics');
        const posted = page.waitForResponse(r => r.url().endsWith('/coaching/agenda') && r.request().method() === 'POST');
        await page.locator('#coach-cal-add').click();
        assert.equal((await posted).status(), 200);
        await page.locator('#coach-tool-agenda .pf-coach-error').waitFor();
        assert.match(await page.locator('#coach-tool-agenda .pf-coach-error').innerText(), /n’est pas acceptée/);
        assert.equal(await page.locator('.pf-coach-calrow').count(), 1);
      }
      assert.deepEqual(page.errors, [], 'agenda: browser errors');
      await page.close();
    }
    {
      const wide = await open(browser, { width: 1280, height: 900 });
      const friday = new Date(fixture.day + 'T00:00:00Z');
      friday.setUTCDate(friday.getUTCDate() + 8);
      await wide.goto(`${athlete}/coaching?d=${friday.toISOString().slice(0, 10)}&outil=agenda`, { waitUntil: 'networkidle' });
      await noOverflow(wide, 'agenda desktop');
      await shot(wide, 'athlete-1280-agenda');
      await wide.close();
    }

    // ── « Mon agenda » with Google Agenda and Outlook configured: the two buttons above the address, the sentence,
    //    a source to reconnect; at 320 px, light and dark, on the site and in the app (an app from before the sign-in
    //    sheet keeps the screen of before). Off again afterwards: the checks above saw the screen without them. ──
    {
      const turn = async state => assert.equal((await fetch(`${athlete}/__preview/coaching/agenda-accounts/${state}`, { method: 'POST' })).status, 200);
      try {
        await turn('on');
        for (const [app, dark] of [[false, false], [false, true], ['new', false], ['new', true], ['noagenda', false]]) {
          const key = `agenda accounts${app ? ' ' + app : ''}${dark ? ' dark' : ''}`;
          const page = await open(browser, { width: 320, dark, app });
          await page.goto(`${athlete}/coaching?outil=agenda`, { waitUntil: 'networkidle' });
          const agenda = page.locator('#coach-tool-agenda');
          const buttons = agenda.locator('[data-agenda-connect]');
          if (app === 'noagenda') {
            assert.equal(await buttons.evaluateAll(all => all.filter(el => el.getClientRects().length).length), 0, key + ': hidden');
            assert.equal(await agenda.getByText('Jamais les titres').isVisible(), false, key);
            assert.ok(await agenda.locator('#coach-cal-url').isVisible(), key + ': the address to paste stays');
          } else {
            assert.deepEqual((await buttons.allInnerTexts()).map(t => t.trim()), ['Connecter Google Agenda', 'Connecter Outlook'], key);
            assert.equal((await agenda.locator('.pf-coach-connect-note').innerText()).trim(), 'PaceForge lit seulement quand tu es occupé. Jamais les titres.');
            for (const b of await buttons.all()) assert.ok((await b.boundingBox()).height >= 44, key + ': a 44 px target');
            const above = await page.evaluate(() => document.querySelector('[data-agenda-connect]').getBoundingClientRect().bottom <=
              document.querySelector('#coach-cal-url').getBoundingClientRect().top);
            assert.ok(above, key + ': above the address');
            assert.equal(await page.locator('script[src*="coaching-agenda.js"]').count(), 1);
          }
          await noOverflow(page, key);
          await shot(page, `athlete-320-agenda-accounts${app ? '-app' + (app === 'noagenda' ? '-before-sheet' : '') : ''}${dark ? '-dark' : ''}`);
          assert.deepEqual(page.errors, [], key + ': browser errors');
          await page.close();
        }
        // a connected Google Agenda whose access expired: listed like an address, « Reconnecter », « Retirer »;
        // only « Connecter Outlook » is left to offer
        await turn('reauth');
        for (const dark of [false, true]) {
          const page = await open(browser, { width: 320, dark });
          await page.goto(`${athlete}/coaching?outil=agenda`, { waitUntil: 'networkidle' });
          const agenda = page.locator('#coach-tool-agenda');
          const row = agenda.locator('.pf-coach-calrow').first();
          assert.equal(await row.locator('b').innerText(), 'Google Agenda');
          assert.match(await row.locator('small').innerText(), /^À reconnecter\s:\sl’accès a expiré ou a été retiré\.$/);
          assert.ok(await row.getByText('Reconnecter', { exact: true }).isVisible() && await row.getByText('Retirer').isVisible());
          assert.deepEqual((await agenda.locator('.pf-coach-connect [data-agenda-connect]').allInnerTexts()).map(t => t.trim()), ['Connecter Outlook']);
          await noOverflow(page, 'agenda reauth');
          await shot(page, `athlete-320-agenda-reconnect${dark ? '-dark' : ''}`);
          // back from the provider: the outcome in one line
          await page.goto(`${athlete}/coaching?outil=agenda&connexion=microsoft&resultat=organisation`, { waitUntil: 'networkidle' });
          assert.match(await agenda.locator('.pf-coach-error').innerText(), /^Ton organisation n’autorise pas cette connexion/);
          await noOverflow(page, 'agenda refusal');
          if (!dark) await shot(page, 'athlete-320-agenda-refusal');
          assert.deepEqual(page.errors, [], 'agenda reauth: browser errors');
          await page.close();
        }
      } finally {
        await turn('off');
      }
    }

    // ── desktop (before the taps below answer the debrief) ──
    for (const dark of [false, true]) {
      const page = await open(browser, { width: 1280, height: 900, dark });
      await page.goto(athlete + '/coaching', { waitUntil: 'networkidle' });
      assert.deepEqual(await page.locator('.pf-nav a').allInnerTexts(), ['Courses', 'Activités', 'Santé', 'Coaching']);
      assert.equal(await page.locator('.pf-nav a[aria-current="page"]').innerText(), 'Coaching');
      // the debrief above; the weeks, and the day opened beside them
      const [ask, cal, day] = [await page.locator('.pf-coach-debrief').boundingBox(), await page.locator('.pf-coach-cal-main').boundingBox(), await page.locator('.pf-coach-daycard').boundingBox()];
      assert.ok(ask.y + ask.height <= cal.y, 'the debrief, then the calendar');
      assert.ok(day.x > cal.x + cal.width - 1 && Math.abs(day.y - cal.y) < 8, 'the calendar, the day beside it ' + JSON.stringify([cal, day]));
      assert.equal(await page.locator('.pf-coach-day').count(), 14);
      await noOverflow(page, 'desktop');
      await shot(page, `athlete-1280${dark ? '-dark' : ''}`);
      assert.deepEqual(page.errors, []);
      await page.close();
    }

    // ── taps: no full reload, instant feedback, keyboard; the debrief, a run outside the plan, « Déplacer » ──
    {
      const page = await open(browser, { width: 393 });
      const at = days => { const d = new Date(fixture.day + 'T00:00:00Z'); d.setUTCDate(d.getUTCDate() + days); return d.toISOString().slice(0, 10); };
      // the preview's day is a Thursday
      const [lastThu, lastSun, tue, wed, fri, sat] = [at(-7), at(-4), at(-2), at(-1), at(1), at(2)];
      await page.goto(athlete + '/coaching', { waitUntil: 'networkidle' });
      await page.evaluate(() => { window.__same_document = true; });
      const period = await page.locator('.pf-coach-period').innerText();
      await swap(page, () => page.locator('#coach-prev').click());
      assert.notEqual(await page.locator('.pf-coach-period').innerText(), period, 'the weeks slid');
      assert.equal(await page.evaluate(() => window.__same_document), true, 'in place: no full reload');
      assert.equal(await page.locator('.pf-coach-week-h b').first().innerText(), 'Semaine dernière', 'past weeks are one tap away');
      // last Thursday's session was skipped: hollow and dashed
      const missed = await page.locator(`#coach-day-${lastThu} .pf-coach-bar-s`).evaluate(b => [b.className, getComputedStyle(b).borderTopStyle, getComputedStyle(b).backgroundColor]);
      assert.deepEqual(missed, ['pf-coach-bar-s is-hard is-missed', 'dashed', 'rgba(0, 0, 0, 0)'], 'a missed session is dashed and hollow');
      // a done session of last week: the activity's summary, the ressenti, the note
      const doneDay = page.locator(`#coach-day-${lastSun}`);
      await swap(page, () => doneDay.click());
      const full = await doneDay.locator('.pf-coach-bar-s').evaluate(b => getComputedStyle(b).backgroundColor);
      assert.notEqual(full, 'rgba(0, 0, 0, 0)', 'a done session is a full block');
      assert.deepEqual(await seen(page, '#coach-main .pf-btn-primary'), [], 'done: no main action left');
      assert.ok(await page.locator('.pf-coach-activity').first().isVisible());
      assert.match(await page.locator('.pf-coach-activity small').first().innerText(), /km · .*(min|h\d\d) · \+/);
      assert.match(await page.locator('.pf-coach-activity').first().getAttribute('href'), /^\/activity\/\d+$/);
      // the ressenti: a scale of five steps, each a 44 px target with its word, growing with the effort
      const feel = page.locator('.pf-coach-daycard [data-feel]');
      assert.equal(await feel.count(), 5);
      assert.deepEqual(await feel.allInnerTexts(), ['Très facile', 'Facile', 'Bien', 'Dur', 'Très dur']);
      const steps = await feel.evaluateAll(all => all.map(b => [b.getBoundingClientRect().height, b.querySelector('i').getBoundingClientRect().height]));
      assert.ok(steps.every(([h]) => h >= 44) && steps.every(([, bar], i) => i === 0 || bar > steps[i - 1][1]), 'five growing steps ' + JSON.stringify(steps));
      assert.doesNotMatch(await page.locator('.pf-coach-feel-block').innerText(), /\p{Extended_Pictographic}/u, 'no emoji');
      const before = await feel.evaluateAll(all => all.findIndex(b => b.getAttribute('aria-pressed') === 'true'));
      const pick = before === 3 ? 1 : 3;
      // pressed at once, before the server answers
      await page.route('**/ressenti', async route => { await new Promise(r => setTimeout(r, 300)); await route.continue(); });
      const answered = page.waitForResponse(r => r.url().endsWith('/ressenti'));
      await feel.nth(pick).click();
      assert.equal(await feel.nth(pick).getAttribute('aria-pressed'), 'true', 'instant feedback');
      await answered;
      await page.waitForFunction(() => !document.querySelector('.htmx-request, .htmx-swapping, .htmx-settling'));
      await page.unroute('**/ressenti');
      assert.equal(await feel.nth(pick).getAttribute('aria-pressed'), 'true', 'saved on the tap');
      assert.equal(await page.locator('.pf-coach-daycard [data-feel][aria-pressed="true"]').count(), 1);
      // the steps up to the one chosen are filled, the others are not
      const filled = await page.locator('.pf-coach-daycard [data-feel] i').evaluateAll(all => all.map(i => getComputedStyle(i).backgroundColor));
      assert.equal(new Set(filled.slice(0, pick + 1)).size, 1);
      assert.ok(pick === 4 || (filled[pick + 1] !== filled[pick] && new Set(filled.slice(pick + 1)).size === 1), 'filled up to the level ' + JSON.stringify(filled));
      // the note: behind « Ajouter une note » — no field, no « Enregistrer » until asked
      assert.equal(await page.locator('.pf-coach-note textarea').isVisible(), false, 'the note’s field waits behind its fold');
      // done with a run: the scale, and the quiet way to take the run back out of the plan
      assert.deepEqual(await seen(page, '.pf-coach-daycard button:not([data-feel])'), ['Ce n’est pas cette séance'], 'no button but the scale and the unlink');
      await page.locator('.pf-coach-notefold > summary').click();
      assert.match(await page.locator('.pf-coach-notefold > summary').innerText(), /^(Ajouter une note|Modifier ma note)$/);
      await page.locator('.pf-coach-note textarea').fill('Bonnes sensations en côte.');
      await swap(page, () => page.locator('.pf-coach-note button').click());
      assert.equal(await page.locator('.pf-coach-said.is-note').innerText(), 'Ta note\nBonnes sensations en côte.');
      assert.match(await page.locator('.pf-coach-said:not(.is-note)').innerText(), /^Florent\nBien géré\./, 'the comment is signed with a first name');
      assert.equal(await page.locator('.pf-coach-notefold > summary').innerText(), 'Modifier ma note');
      assert.equal(await page.locator('.pf-coach-note textarea').isVisible(), false, 'closed again once saved');
      await noOverflow(page, 'done session');
      await targets(page, 'done session');
      await shot(page, 'athlete-393-done-ressenti');
      await theme(page, true); await shot(page, 'athlete-393-done-ressenti-dark'); await theme(page, false);
      // the month: one tap; the same days, more weeks
      await swap(page, () => page.locator('#coach-view').click());
      assert.equal(await page.locator('#coach-view').getAttribute('aria-pressed'), 'true');
      assert.equal(await page.locator('.pf-coach-calendar.is-month').count(), 1);
      assert.ok(await page.locator('.pf-coach-day').count() >= 28, 'a month of days');
      assert.ok(await page.locator('.pf-coach-day .pf-coach-bar-s.is-done').count() >= 3);
      assert.ok(await page.locator('.pf-coach-day .pf-coach-bar-s.is-missed').count() >= 1, 'a missed session is marked');
      assert.equal(await page.locator('.pf-coach-day.is-selected').count(), 1);
      await noOverflow(page, 'month');
      await targets(page, 'month');
      await shot(page, 'athlete-393-month');
      await theme(page, true); await shot(page, 'athlete-393-month-dark'); await theme(page, false);
      await swap(page, () => page.locator('#coach-view').click());
      assert.equal(await page.locator('.pf-coach-day').count(), 14, 'back to the two weeks');

      // the debrief, by its link (what a notification will open): the calendar on Tuesday, the question at the top
      await page.goto(`${athlete}/coaching?d=${tue}#debrief`, { waitUntil: 'networkidle' });
      const ask = page.locator('#debrief');
      assert.equal(await ask.getAttribute('class').then(c => c.includes('pf-coach-debrief')), true, 'the link lands on the question');
      assert.ok(await ask.evaluate(el => { const r = el.getBoundingClientRect(); return r.top >= 0 && r.top < innerHeight; }), 'in view');
      assert.match(await ask.locator('h2').innerText(), /^Ta sortie de mardi est arrivée/);
      assert.match(await ask.locator('.pf-coach-debrief-line').innerText(), /^Footing \d+ min · [\d,]+\skm · /, 'what it was, the run’s own figures');
      assert.equal(await page.locator(`#coach-day-${tue}.is-selected`).count(), 1, 'its day is open under the weeks');
      assert.equal(await page.locator('.pf-coach-daycard .pf-coach-feel-block').count(), 0, 'asked once, at the top');
      await noOverflow(page, 'debrief');
      await targets(page, 'debrief');
      await shot(page, 'athlete-393-debrief');
      await theme(page, true); await shot(page, 'athlete-393-debrief-dark'); await theme(page, false);
      // one tap answers it, in place: the question goes, the mark on its day too, the day shows what was said
      await swap(page, () => page.locator('#coach-ask-3').click());
      assert.equal(await page.locator('.pf-coach-debrief').count(), 0, 'answered: the question is gone');
      assert.equal(await page.locator(`#coach-day-${tue} .pf-coach-todo`).count(), 0, 'its mark too');
      assert.doesNotMatch(await page.locator(`#coach-day-${tue}`).getAttribute('aria-label'), /débriefer/);
      assert.equal(await page.locator(`#coach-day-${tue}.is-selected`).count(), 1, 'the calendar stays on its day');
      assert.equal(await page.locator('.pf-coach-daycard [data-feel][aria-pressed="true"]').innerText(), 'Bien', 'the day says what was answered');
      await shot(page, 'athlete-393-debrief-answered');
      // the same link once answered: the day itself
      await page.goto(`${athlete}/coaching?d=${tue}#debrief`, { waitUntil: 'networkidle' });
      assert.equal(await page.locator('#debrief').getAttribute('class'), 'pf-coach-daycard', 'nothing waits: the link lands on the day');
      await page.waitForFunction(() => { const r = document.getElementById('debrief').getBoundingClientRect(); return r.top >= 0 && r.top < innerHeight; });

      // a run outside the plan: on its day, « Hors plan », nothing to answer
      await swap(page, () => page.locator(`#coach-day-${wed}`).click());
      assert.match(await page.locator('.pf-coach-activity.is-extra').innerText(), /^Hors plan · Footing avec Léa\n[\d,]+\skm/);
      assert.equal(await page.locator('.pf-coach-rest').innerText(), 'Rien de prévu');
      assert.equal(await page.locator('#coach-main [data-feel]').count(), 0, 'nothing to answer');
      assert.deepEqual(await seen(page, '#coach-main .pf-btn-primary'), [], 'no main action');
      await noOverflow(page, 'off plan');
      await targets(page, 'off plan');
      await shot(page, 'athlete-393-offplan');

      // a structured session keeps its profile, in its kind's colour; a day without a session says so and names the
      // next one, a tap away
      await page.goto(`${athlete}/coaching?d=${fri}`, { waitUntil: 'networkidle' });
      assert.match(await page.locator('.pf-coach-s-title').innerText(), /^Côtes\s+45 min$/);
      assert.ok(await page.locator('.pf-coach-session svg.pf-effort').isVisible(), 'the intensity profile is drawn');
      assert.equal(await page.locator('.pf-coach-gauge').count(), 0, 'no gauge beside a profile');
      assert.deepEqual(await seen(page, '#coach-main .pf-btn-primary'), [], 'a session to come has no main action');
      assert.deepEqual(await seen(page, '.pf-coach-do .pf-btn'), ['Déplacer'], '« Déplacer », alone on its row');
      await shot(page, 'athlete-393-structured');
      await swap(page, () => page.locator(`#coach-day-${sat}`).click());
      assert.equal(await page.locator('.pf-coach-rest').innerText(), 'Repos');
      const next = page.locator('#coach-next-session');
      assert.match(await next.innerText(), /^Prochaine séance · dimanche \d+ \S+\nSortie longue (\d+ min|\dh\d\d)$/);
      assert.ok((await next.boundingBox()).height >= 44);
      await noOverflow(page, 'rest day');
      await shot(page, 'athlete-393-rest');
      await theme(page, true); await shot(page, 'athlete-393-rest-dark'); await theme(page, false);
      await swap(page, () => next.click());
      assert.match(await page.locator('.pf-coach-s-title').innerText(), /^Sortie longue\s+(\d+ min|\dh\d\d)$/);
      assert.match(await page.locator('.pf-coach-date').innerText(), /^Dimanche \d+ \S+$/);

      // « Déplacer », by the keyboard: Friday's hill session to Saturday — not one of her days, and next to Sunday's
      // long run: the day says so, and takes it
      await page.goto(athlete + '/coaching', { waitUntil: 'networkidle' });
      await page.locator(`#coach-day-${fri}`).focus();
      await swap(page, () => page.keyboard.press('Enter'));
      assert.equal(await page.evaluate(() => document.activeElement.id), `coach-day-${fri}`, 'the focus stays on the day chosen');
      assert.ok(await page.locator('.pf-coach-s-guide').isVisible(), 'the guidance line');
      await page.locator('[id^="coach-move-"]:not(#coach-move-cancel)').focus();
      await swap(page, () => page.keyboard.press('Enter'));
      assert.match(await page.locator('.pf-coach-moving').innerText(), /^Où placer «\s?Côtes 45 min\s?»\s?\? Touche un jour\.\s+Annuler$/);
      assert.equal(await page.evaluate(() => document.activeElement.id), 'coach-moving', 'the instruction takes the focus');
      // the days are the targets now: the calendar is in view, the day keeps no action
      const [calBox, tabs] = [await page.locator('.pf-coach-cal-main').boundingBox(), await page.locator('.pf-tabbar').boundingBox()];
      assert.ok(calBox.y >= 0 && calBox.y + calBox.height <= tabs.y, 'the calendar came into view, clear of the tab bar ' + JSON.stringify([calBox, tabs]));
      assert.deepEqual(await seen(page, '.pf-coach-daycard :is(.pf-btn, .pf-coach-quietbtn):not(#coach-today)'), [], 'while moving: no other action');
      const target = page.locator(`button#coach-day-${sat}`);
      assert.equal(await target.isDisabled(), false, 'any day: Saturday was never one of hers');
      assert.match(await target.getAttribute('class'), /\bis-warn\b/, 'next to another hard day: marked');
      assert.match(await target.getAttribute('aria-label'), /^Déplacer au samedi \d+ \S+ : repos — deux jours durs de suite$/);
      assert.match(await page.locator('.pf-coach-move-hint').innerText(), /tu peux le choisir quand même\.$/, 'a warning, never a refusal');
      assert.equal(await page.locator(`button#coach-day-${fri}`).isDisabled(), true, 'its own day');
      assert.equal(await page.locator(`button#coach-day-${tue}`).isDisabled(), true, 'a day behind');
      assert.equal(await page.locator(`button#coach-day-${wed}`).isDisabled(), false, 'a day behind that holds a run no session took');
      assert.equal(await page.locator('button.pf-coach-day[disabled]').count(), 3, 'Monday, Tuesday and its own day only: every other day takes it, next week too');
      await targets(page, 'moving');
      await noOverflow(page, 'moving');
      await shot(page, 'athlete-393-moving');
      await theme(page, true); await shot(page, 'athlete-393-moving-dark'); await theme(page, false);
      await target.focus();
      await swap(page, () => page.keyboard.press('Enter'));
      assert.equal(await page.locator('.pf-coach-moving').count(), 0);
      assert.equal(await page.locator('#coach-main .pf-coach-error').count(), 0, 'accepted');
      assert.equal(await page.evaluate(() => document.activeElement.id), `coach-day-${sat}`, 'the focus on its new day');
      assert.equal(await page.locator(`#coach-day-${fri} .pf-coach-bar-s`).count(), 0, 'its old day is free');
      assert.equal(await page.locator(`#coach-day-${sat}.is-selected .pf-coach-bar-s.is-hard`).count(), 1, 'it is on the day chosen');
      assert.match(await page.locator('.pf-coach-hardpair').innerText(), /^Samedi et dimanche\s:\sdeux jours durs de suite\. Tu peux courir la séance en footing\.$/, 'said on its week, with the way out');
      await shot(page, 'athlete-393-moved');
      // pinned: read again, it stays where it was put
      await page.goto(`${athlete}/coaching?d=${sat}`, { waitUntil: 'networkidle' });
      assert.equal(await page.locator(`#coach-day-${sat} .pf-coach-bar-s.is-hard`).count(), 1, 'a moved session stays');
      await page.locator('[id^="coach-edit-"] > summary').first().click();
      const duration = page.locator('.pf-coach-form select[name="minutes"]').first();
      const minutes = (await duration.inputValue()) === '40' ? '50' : '40';
      await duration.selectOption(minutes);
      await noOverflow(page, 'edit form');
      await swap(page, () => page.locator('[id^="coach-save-"]').first().click());
      assert.match(await page.locator('.pf-coach-s-dur').first().innerText(), new RegExp('^' + minutes + ' min$'));
      assert.match(await page.locator('.pf-coach-edited').innerText(), /Modifiée par toi/);
      // the chart: a week chosen says its planned and done time
      await page.locator('#coach-plan-toggle').click();
      const chart = page.locator('.pf-coach-plan figure');
      const read = await chart.evaluate(fig => {
        const data = JSON.parse(fig.querySelector('.pf-viz-data').textContent);
        const input = fig.querySelector('.pf-viz-range');
        input.value = 1; input.dispatchEvent(new Event('input'));
        return data.r[1];
      });
      assert.ok((await chart.locator('.pf-viz-read').innerText()).includes(read[1]));
      // the calendar feed: a webcal link, and the feed itself
      await page.locator('#coach-options-toggle').click();
      await page.locator('#coach-tool-feed > summary').click();
      const webcal = await page.locator('#coach-tool-feed a[href^="webcal://"]').getAttribute('href');
      assert.match(webcal, /^webcal:\/\/[^/]+\/coaching\/cal\/[\w-]{40,}\.ics$/);
      const feed = await page.evaluate(async path => { const r = await fetch(path); return [r.status, r.headers.get('content-type'), await r.text()]; }, new URL(webcal.replace('webcal://', 'https://')).pathname);
      assert.equal(feed[0], 200);
      assert.match(feed[1], /^text\/calendar/);
      assert.match(feed[2], /^BEGIN:VCALENDAR\r\n/);
      assert.ok(feed[2].includes('DTSTART;VALUE=DATE:') && feed[2].includes('SUMMARY:Footing'));
      await noOverflow(page, 'tools');
      // a link to an option lands on it, opened (the address a form of the options comes back to, and the older one)
      for (const address of ['/coaching?outil=forme#coach-form', '/coaching#coach-form', '/coaching#coach-tool-agenda']) {
        await page.goto(athlete + address, { waitUntil: 'networkidle' });
        const target = page.locator(new URL(athlete + address).hash);
        assert.equal(await page.locator('#coach-options').evaluate(el => el.open), true, address + ': the options are open');
        await page.waitForFunction(id => { const r = document.querySelector(id).getBoundingClientRect(); return r.top >= 0 && r.top < innerHeight; }, new URL(athlete + address).hash);
        assert.ok(await target.isVisible(), address + ': on the option');
        if (address.endsWith('agenda')) assert.equal(await target.evaluate(el => el.open), true, address + ': its fold too');
      }
      assert.deepEqual(page.errors, [], 'taps: browser errors');
      await page.close();
    }

    // ── by hand (coaching-calendar.js): a session held, then dropped on another day — the move « Déplacer » posts;
    //    a swipe on the weeks is ‹ ›; the page's scroll stays the page's ──
    {
      const at = days => { const d = new Date(fixture.day + 'T00:00:00Z'); d.setUTCDate(d.getUTCDate() + days); return d.toISOString().slice(0, 10); };
      const [wed, thu, sat, sun, nextTue] = [at(-1), fixture.day, at(2), at(3), at(5)];
      const moves = [], watch = p => p.on('request', r => { if (r.method() === 'POST' && r.url().endsWith('/move')) moves.push(r.postData()); });
      const page = await open(browser, { width: 393 });
      watch(page);
      const finger = await fingerOf(page);
      await page.goto(athlete + '/coaching', { waitUntil: 'networkidle' });
      // whether anything was ever lifted (the ghost is added to the page while a session is held)
      await page.evaluate(() => {
        window.__lifted = false;
        new MutationObserver(() => { if (document.querySelector('.pf-coach-ghost')) window.__lifted = true; })
          .observe(document.querySelector('.pf-coach'), { childList: true, subtree: true });
      });
      assert.equal(await page.locator('#coach-drag-hint').innerText(), 'Maintiens une séance pour la déplacer.', 'one line says it, the first time');
      const hills = await page.locator(`#coach-day-${sat} [data-move]`).getAttribute('data-what');
      assert.match(hills, /^Côtes \d+ min$/, 'Saturday holds the hill session moved there');
      // a vertical stroke that starts on a session scrolls the page: nothing is lifted, nothing is sent
      const satAt = await centre(page, `#coach-day-${sat}`);
      await finger.stroke(satAt, { x: satAt.x, y: satAt.y - 260 });
      await page.waitForTimeout(400);
      assert.ok(await page.evaluate(() => scrollY) > 40, 'the page scrolled');
      assert.equal(await page.evaluate(() => window.__lifted), false, 'a scroll lifts nothing');
      await page.evaluate(() => scrollTo({ top: 0, behavior: 'instant' }));
      // a horizontal swipe on the weeks: the next two, then back — the same addresses as › and ‹
      const period = await page.locator('.pf-coach-period').innerText();
      const row = await centre(page, `#coach-day-${thu}`);
      await swap(page, () => finger.stroke({ x: row.x + 90, y: row.y }, { x: row.x - 90, y: row.y + 6 }));
      assert.equal(await page.locator('.pf-coach-week-h b').first().innerText(), 'Semaine prochaine', 'a swipe to the left: the next weeks');
      assert.notEqual(await page.locator('.pf-coach-period').innerText(), period);
      await swap(page, () => finger.stroke({ x: row.x - 90, y: row.y }, { x: row.x + 90, y: row.y - 6 }));
      assert.equal(await page.locator('.pf-coach-period').innerText(), period, 'to the right: back');
      assert.equal(await page.evaluate(() => window.__lifted), false, 'a swipe lifts nothing');
      // held, then let go outside any day, or Escape: back in place, no request
      await finger.hold(satAt, await centre(page, '.pf-coach-period'));
      await finger.hold(satAt, await centre(page, `#coach-day-${nextTue}`), false);
      assert.equal((await held(page)).dragging, true, 'held');
      await page.keyboard.press('Escape');
      await finger.up();
      await page.waitForTimeout(300);
      assert.deepEqual(moves, [], 'let go outside, or Escape: nothing sent');
      assert.equal(await page.locator(`#coach-day-${sat} .pf-coach-bar-s.is-hard`).count(), 1, 'the session is back on its day');
      assert.deepEqual(await held(page).then(h => [h.dragging, h.ghost]), [false, null]);
      // held over a day next to another hard session: the days that take it are marked, the others fade, that one
      // has the hard-days warning's colour; the block floats above the finger, never under it
      await finger.hold(satAt, { x: satAt.x, y: satAt.y + 40 }, false);
      let state = await held(page);
      assert.ok(state.drops.length >= 9 && !state.drops.includes(`coach-day-${sat}`), 'every day still to come takes it ' + state.drops);
      assert.ok(state.faded.every(o => o === '0.38'), 'the days that cannot take it fade');
      assert.equal(state.label, hills);
      const warnDay = state.warn.find(id => id !== `coach-day-${sun}`);
      assert.ok(warnDay, 'a day next to another hard session is marked ' + state.warn);
      const warnAt = await centre(page, '#' + warnDay);
      await finger.move({ x: satAt.x, y: satAt.y + 40 }, warnAt);
      state = await held(page);
      assert.deepEqual(state.over, [warnDay], 'the day under the finger');
      assert.ok(state.ghost.bottom < warnAt.y - 20 && state.ghost.top >= 0, 'above the finger ' + JSON.stringify(state.ghost));
      assert.equal(await page.locator('#' + warnDay).evaluate(d => getComputedStyle(d).boxShadow.includes('rgb(199, 106, 0)')), true, 'the warning’s colour');
      await page.screenshot({ path: `${out}/byhand-393-held.png` });
      await theme(page, true); await page.screenshot({ path: `${out}/byhand-393-held-dark.png` }); await theme(page, false);
      await finger.move(warnAt, await centre(page, '.pf-coach-period'));
      await finger.up();
      await page.waitForTimeout(300);
      assert.deepEqual(moves, [], 'let go outside: nothing sent');
      // held, then let go on next Tuesday: shown there at once, sent as « Déplacer » sends it, the calendar redrawn
      // on the server's answer — the day opened follows the session
      await page.route('**/move', async route => { await new Promise(r => setTimeout(r, 400)); await route.continue(); });
      await swap(page, async () => {
        await finger.hold(satAt, await centre(page, `#coach-day-${nextTue}`));
        assert.equal(await page.locator(`#coach-day-${nextTue} .pf-coach-bar-s.is-pending`).count(), 1, 'in its new day before the answer');
      });
      await page.unroute('**/move');
      assert.equal(moves.length, 1);
      assert.match(moves[0], new RegExp(`^to=${nextTue}&v=semaine&w=\\d{4}-\\d\\d-\\d\\d$`), 'the same fields as the form');
      assert.equal(await page.locator('#coach-main .pf-coach-error').count(), 0, 'accepted');
      assert.equal(await page.locator(`#coach-day-${sat} .pf-coach-bar-s`).count(), 0, 'its old day is free');
      assert.equal(await page.locator(`#coach-day-${nextTue}.is-selected .pf-coach-bar-s.is-hard`).count(), 1, 'on the day chosen, opened');
      assert.equal((await page.locator('.pf-coach-s-title').innerText()).replace(/\s+/g, ' '), hills);
      assert.equal(await page.locator('#coach-drag-hint').isVisible(), false, 'the hint, once it is done');
      // the run outside the plan, dropped on Thursday's session: « C’est ma séance de jeudi », by hand
      const wedAt = await centre(page, `#coach-day-${wed}`);
      await finger.hold(wedAt, { x: wedAt.x + 20, y: wedAt.y }, false);
      state = await held(page);
      assert.equal(state.label, 'Sortie');
      assert.ok(state.drops.includes(`coach-day-${thu}`) && state.drops.every(id => id <= `coach-day-${sun}`), 'the sessions of its week ' + state.drops);
      await swap(page, async () => { await finger.move({ x: wedAt.x + 20, y: wedAt.y }, await centre(page, `#coach-day-${thu}`)); await finger.up(); });
      assert.match(moves[1], new RegExp(`^to=${wed}&`), 'Thursday’s session moves to the run’s day');
      assert.equal(await page.locator(`#coach-day-${wed} .pf-coach-bar-s.is-extra`).count(), 0, 'the run is the session now');
      assert.equal(await page.locator(`#coach-day-${wed} .pf-coach-bar-s.is-done`).count(), 1);
      assert.equal(await page.locator(`#coach-day-${thu} .pf-coach-bar-s`).count(), 0);
      await shot(page, 'byhand-393-run-taken');
      assert.deepEqual(page.errors, [], 'by hand: browser errors');
      await page.close();

      // 320 px: the same, held over a day next to a hard session (Sunday's long run, the next days)
      const small = await open(browser, { width: 320 });
      watch(small);
      const smallFinger = await fingerOf(small);
      await small.goto(athlete + '/coaching', { waitUntil: 'networkidle' });
      const sunAt = await centre(small, `#coach-day-${sun}`);
      await smallFinger.hold(sunAt, { x: sunAt.x, y: sunAt.y + 30 }, false);
      state = await held(small);
      assert.ok(state.warn.length >= 1, '320: a warning day');
      const smallWarn = await centre(small, '#' + state.warn[state.warn.length - 1]);
      await smallFinger.move({ x: sunAt.x, y: sunAt.y + 30 }, smallWarn);
      state = await held(small);
      assert.equal(state.wide, false, '320: no horizontal overflow while held');
      assert.ok(state.ghost.left >= 0 && state.ghost.right <= 320 && state.ghost.bottom < smallWarn.y, '320: the ghost in the screen, above the finger');
      await small.screenshot({ path: `${out}/byhand-320-held.png` });
      await smallFinger.move(smallWarn, await centre(small, '.pf-coach-period'));
      await smallFinger.up();
      await small.waitForTimeout(300);
      assert.equal(moves.length, 2, '320: let go outside, nothing sent');
      assert.deepEqual(small.errors, [], '320 by hand: browser errors');
      await small.close();

      // a mouse: a few pixels and the session follows; let go on Saturday, next to Sunday's long run — the server
      // says it on the week, in its own words; Escape puts a session back
      const wide = await open(browser, { width: 1280, height: 900 });
      watch(wide);
      await wide.goto(`${athlete}/coaching?d=${nextTue}`, { waitUntil: 'networkidle' });
      const from = await centre(wide, `#coach-day-${nextTue}`), to = await centre(wide, `#coach-day-${sat}`);
      await wide.mouse.move(from.x, from.y);
      await wide.mouse.down();
      await wide.mouse.move(from.x + 4, from.y - 4, { steps: 2 });
      await wide.mouse.move(to.x, to.y, { steps: 12 });
      state = await held(wide);
      assert.deepEqual(state.over, [`coach-day-${sat}`]);
      assert.ok(state.warn.includes(`coach-day-${sat}`), 'Saturday is next to Sunday’s long run');
      await wide.screenshot({ path: `${out}/byhand-1280-held.png` });
      await swap(wide, () => wide.mouse.up());
      assert.match(moves[moves.length - 1], new RegExp(`^to=${sat}&`));
      assert.equal(await wide.locator(`#coach-day-${sat}.is-selected .pf-coach-bar-s.is-hard`).count(), 1, 'moved, and opened (the click that ends a drag opens no other day)');
      assert.match(await wide.locator('.pf-coach-hardpair').innerText(), /^Samedi et dimanche\s:\sdeux jours durs de suite\./, 'the server says the warning');
      const count = moves.length, back = await centre(wide, `#coach-day-${sat}`);
      await wide.mouse.move(back.x, back.y);
      await wide.mouse.down();
      await wide.mouse.move(back.x + 60, back.y + 80, { steps: 6 });
      assert.equal((await held(wide)).dragging, true);
      await wide.keyboard.press('Escape');
      await wide.mouse.up();
      await wide.waitForTimeout(300);
      assert.equal(moves.length, count, 'Escape: nothing sent');
      assert.equal((await held(wide)).ghost, null);
      assert.deepEqual(wide.errors, [], 'mouse by hand: browser errors');
      await wide.close();
    }

    // ── the onboarding: the athlete's own (a race, a computed volume), then a new account end to end ──
    for (const dark of [false, true]) {
      const page = await open(browser, { width: 393, dark });
      await page.goto(athlete + '/coaching?refaire=1', { waitUntil: 'networkidle' });
      assert.ok(await page.getByText('Trail des Crêtes').first().isVisible());
      assert.match(await page.locator('#coach-base option:checked').innerText(), /^\dh\d\d$/, 'the computed volume');
      // her plan's own answers, and the week they make, drawn: a bar over each of her four days
      assert.deepEqual(await page.locator('[data-days] input:checked').evaluateAll(all => all.map(i => i.value)), ['1', '3', '4', '6']);
      assert.equal(await page.locator('#coach-long').inputValue(), '6');
      assert.deepEqual((await previewBars(page)).map(([day]) => day), [1, 3, 4, 6], 'a bar over each day chosen');
      assert.equal(await page.getByRole('button', { name: 'Refaire mon plan' }).isDisabled(), false);
      assert.equal(await page.getByText('Quand peux-tu courir').count(), 0, 'asked in « Mon agenda », not here');
      await noOverflow(page, 'onboarding');
      await shot(page, `athlete-393-onboarding${dark ? '-dark' : ''}`);
      await page.close();
    }
    // ── any account, with no plan, no activity and no race: Activités, then the questions ──
    for (const app of SHELLS) {
      const page = await open(browser, { width: 393, app });
      await page.goto(fresh + '/activities', { waitUntil: 'networkidle' });
      // the entry card: only where nothing else leads to Coaching, an app from before its native tab
      assert.equal(await page.locator('a.pf-coach-entry').count(), 1);
      assert.equal(await page.locator('a.pf-coach-entry').isVisible(), app === 'old', `activities${suffix(app)}: the entry card`);
      if (!app) assert.deepEqual(await page.locator('.pf-tabbar a').allInnerTexts(), ['Courses', 'Activités', 'Santé', 'Coaching']);
      await noOverflow(page, 'activities' + suffix(app));
      await shot(page, `activities-393${suffix(app) || '-site'}`);
      assert.deepEqual(page.errors, [], 'activities: browser errors');
      await page.close();
    }
    for (const width of [320, 393]) for (const dark of [false, true]) for (const app of SHELLS) {
      const key = `new account ${width}${dark ? ' dark' : ''}${suffix(app)}`;
      const page = await open(browser, { width, dark, app });
      await page.goto(fresh + '/coaching', { waitUntil: 'networkidle' });
      assert.match(await page.locator('h1').innerText(), /^Coaching\s+bêta$/i, key);
      assert.equal(await page.locator('a.pf-back[href="/activities"]').isVisible(), app === 'old', key + ': « ‹ Activités »');
      // out of the box: every answer is filled in and the plan is drawn — no race: « Progresser » chosen among the
      // three intents, its line under them, the races folded away; no history: five coarse volumes, 2 h chosen; the
      // default week (Tuesday, Thursday, Sunday), the long run on Sunday
      assert.deepEqual((await page.locator('.pf-coach-intent b').allInnerTexts()).map(t => t.trim()), ['Garder la forme', 'Progresser', 'Préparer une course'], key);
      assert.equal(await page.locator('input[name="intent"]:checked').getAttribute('value'), 'progresser', key);
      assert.match(await page.locator('#coach-intent-line').innerText(), /^Ton volume monte peu à peu/, key);
      assert.equal(await page.locator('#coach-races').isVisible(), false, key + ': no race to choose for « Progresser »');
      assert.equal(await page.locator('input[name="goal"]:checked').count(), 0, key);
      assert.match(await page.locator('.pf-coach-pv-more .pf-coach-level').innerText(), /^Tu cours régulièrement\s: aucune sortie enregistrée/, key + ': the level, read');
      assert.deepEqual((await page.locator('.pf-coach-chip.is-wide').allInnerTexts()).map(t => t.replace(/\s+/g, ' ').trim()),
        ['1 h', '2 h', '3 h', '4 h', '5 h et +'], key);
      assert.equal(await page.locator('input[name="base"]:checked').getAttribute('value'), '120', key);
      assert.equal(await page.locator('#coach-base').count(), 0, key + ': no computed volume without a history');
      assert.deepEqual(await page.locator('[data-days] input:checked').evaluateAll(all => all.map(i => i.value)), ['1', '3', '6'], key);
      assert.deepEqual(await page.locator('[data-days] input').evaluateAll(all => all.map(i => i.getAttribute('aria-label'))), ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche'], key);
      assert.equal(await page.locator('#coach-long').inputValue(), '6', key);
      assert.deepEqual(await page.locator('#coach-long option').allInnerTexts(), ['mardi', 'jeudi', 'dimanche'], key + ': the long run among the days chosen');
      assert.equal(await page.locator('.pf-coach-error').count(), 0, key);
      assert.equal(await page.getByText('Quand peux-tu courir').count(), 0, key + ': asked in « Mon agenda », not here');
      // the week these answers make is drawn at once: a bar over each day chosen, its duration above it
      const drawn = await previewBars(page);
      assert.deepEqual(drawn.map(([day, kind]) => [day, kind]), [[1, 'easy'], [3, 'hard'], [6, 'long']], key + ': the first week');
      for (const [, , duration] of drawn) assert.match(duration, /^(\d+\smin|\dh\d\d)$/, key);
      assert.match(await page.locator('#coach-preview').getAttribute('aria-label'), /^Semaine du .+, 3 séances, \d heures?( \d\d)? : mardi : footing \d+ minutes ; jeudi : (séance|côtes) \d+ minutes ; dimanche : sortie longue \d+ minutes\.$/, key);
      const columns = await page.locator('.pf-coach-pv-col').evaluateAll(all => all.map(el => { const r = el.getBoundingClientRect(); return r.left + r.width / 2; }));
      const under = await page.locator('[data-days] .pf-coach-chip').evaluateAll(all => all.map(el => { const r = el.getBoundingClientRect(); return r.left + r.width / 2; }));
      assert.ok(columns.every((x, i) => Math.abs(x - under[i]) < 1.5), key + ': each day sits under its bar');
      assert.deepEqual((await page.locator('.pf-coach-pv-more .pf-coach-legend span').allInnerTexts()).map(t => t.trim()), ['Footing', 'Sortie longue', 'Séance'], key);
      assert.match(await page.locator('#coach-preview-first').innerText(), /^Première séance aujourd’hui\s: Séance \d+ min$/, key);
      const chips = await page.locator('.pf-coach-chip, .pf-coach-option, .pf-coach-intent').evaluateAll(all => all.filter(el => el.getClientRects().length).map(el => {
        const r = el.getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height), Math.round(r.top)]; }));
      // 44 px tall; seven days side by side leave 33 px each at 320 px, 43 px at 393 px
      assert.deepEqual(chips.filter(([w, h]) => w < (width < 393 ? 32 : 40) || h < 44), [], key + ': 44 px choices');
      // each row of choices on one line: seven days, five volumes
      for (const [group, count] of [['[data-days]', 7], ['.pf-coach-volume', 5]]) {
        const tops = await page.locator(`${group} .pf-coach-chip`).evaluateAll(all => all.map(el => Math.round(el.getBoundingClientRect().top)));
        assert.equal(tops.length, count, key + ' ' + group);
        if (width >= 393) assert.equal(new Set(tops).size, 1, key + ': ' + group + ' on one line');
      }
      const submit = await page.getByRole('button', { name: 'Créer mon plan' }).boundingBox();
      assert.ok(submit.height >= 44 && submit.width > width * 0.8, key + ': the button');
      await noOverflow(page, key);
      if (width === 393) await shot(page, `new-393-onboarding${dark ? '-dark' : ''}${suffix(app)}`);
      // « Créer mon plan » is ready at once, within the first screen and a half
      const go = page.getByRole('button', { name: 'Créer mon plan' });
      assert.equal(await go.isDisabled(), false, key + ': ready at once');
      if (!app) assert.ok(submit.y + submit.height < 852 * 1.5, key + ': the button is near ' + Math.round(submit.y));
      if (width === 320 && !app) await shot(page, `new-320-onboarding${dark ? '-dark' : ''}`);
      if (width === 393 && !dark && !app) {
        // the plan reacts: a chip changed, the week is drawn again by the generator (a GET that writes nothing),
        // in place — 3 h on four days, a fourth bar; the long run's day follows the days
        await page.evaluate(() => { window.__same_document = true; });
        await redrawn(page, () => page.locator('.pf-coach-chip.is-wide input[value="180"]').check({ force: true }));
        const longer = await previewBars(page);
        assert.deepEqual(longer.map(([day]) => day), [1, 3, 6]);
        assert.notDeepEqual(longer, drawn, 'the durations follow the volume');
        await redrawn(page, () => page.locator('[data-days] input[value="5"]').check({ force: true }));
        assert.deepEqual((await previewBars(page)).map(([day]) => day), [1, 3, 5, 6], 'a bar on the day added');
        assert.equal(await page.locator('#coach-preview-said').innerText(), await page.locator('#coach-preview').getAttribute('aria-label'), 'the new week is said to a screen reader');
        assert.match(await page.locator('#coach-preview-said').innerText(), /, 4 séances, .* ; samedi : /);
        assert.deepEqual(await page.locator('#coach-long option').allInnerTexts(), ['mardi', 'jeudi', 'samedi', 'dimanche']);
        await redrawn(page, () => page.locator('[data-days] input[value="6"]').uncheck({ force: true }));
        assert.equal(await page.locator('#coach-long').inputValue(), '5', 'Sunday removed: the last day chosen');
        assert.equal((await previewBars(page)).find(([day]) => day === 5)[1], 'long');
        await page.locator('#coach-long').focus();
        await redrawn(page, () => page.locator('#coach-long').selectOption('1'));
        assert.equal((await previewBars(page)).find(([day]) => day === 1)[1], 'long', 'the long run on the day chosen');
        assert.equal(await page.evaluate(() => document.activeElement.id), 'coach-long', 'the focus stays on the control');
        assert.equal(await page.evaluate(() => window.__same_document), true, 'in place: no full reload');
        await noOverflow(page, key + ' changed');
        await shot(page, 'new-393-onboarding-changed');
        // seven days: the button waits and the line under the days says why (the week's place stays empty); one
        // day: the same, in its own words
        for (const day of ['0', '2', '4', '6']) await redrawn(page, () => page.locator(`[data-days] input[value="${day}"]`).check({ force: true }));
        assert.equal(await go.isDisabled(), true, 'seven days: the button waits');
        assert.equal(await page.locator('#coach-days-hint').innerText(), 'Garde un jour de repos : 6 jours au plus.');
        assert.ok((await page.locator('#coach-days-hint').getAttribute('class')).includes('pf-coach-error'));
        assert.equal((await previewBars(page)).length, 0);
        assert.equal(await page.locator('#coach-preview .pf-coach-pv-msg').count(), 0, 'said once, under the days');
        await shot(page, 'new-393-onboarding-seven');
        for (const day of ['0', '1', '3', '4', '5', '6']) await redrawn(page, () => page.locator(`[data-days] input[value="${day}"]`).uncheck({ force: true }));
        assert.equal(await go.isDisabled(), true, 'one day is not enough');
        assert.equal(await page.locator('#coach-days-hint').innerText(), 'Choisis au moins 2 jours.');
        // nothing was written by all of this: the account still has no plan
        assert.equal((await (await fetch(fresh + '/coaching/calendar', { headers: { 'HX-Request': 'true' } })).status), 404, 'no plan yet');
        // a form posted all the same (no JavaScript) says what is missing, in place (answered 200: the app's shell
        // replaces a 4xx document), and keeps the answers
        const posted = page.waitForResponse(r => r.url().endsWith('/coaching/plan'));
        await page.evaluate(() => document.getElementById('coach-onb').submit());
        assert.equal((await posted).status(), 200);
        await page.locator('.pf-coach-error[role="alert"]').waitFor();
        assert.match(await page.locator('.pf-coach-error[role="alert"]').innerText(), /jour/i);
        assert.deepEqual(await page.locator('[data-days] input:checked').evaluateAll(all => all.map(i => i.value)), ['2'], 'the day is kept');
        assert.equal(await page.locator('input[name="base"]:checked').getAttribute('value'), '180', 'the answer is kept');
        await noOverflow(page, key + ' error');
        await shot(page, 'new-393-onboarding-error');
      }
      assert.deepEqual(page.errors, [], key + ': browser errors');
      await page.close();
    }
    for (const [width, dark] of [[393, false], [393, true], [320, false]]) {
      // « Ma séance fixe » on the start screen: one quiet line under the days, that opens its three answers; a weekday
      // chosen fills in its duration, is ticked among the days, and the week drawn follows (nothing is written)
      const key = `new account fixed ${width}${dark ? ' dark' : ''}`;
      const page = await open(browser, { width, dark });
      await page.goto(fresh + '/coaching', { waitUntil: 'networkidle' });
      const line = page.locator('label.pf-coach-fixed-toggle');
      assert.equal((await line.innerText()).trim(), 'J’ai déjà une séance chaque semaine (club)', key);
      assert.ok((await line.boundingBox()).height >= 44, key + ': a 44 px line');
      assert.equal(await page.locator('[data-fixed-body]').isVisible(), false, key + ': closed');
      await redrawn(page, () => page.locator('#coach-fixed-on').check());
      assert.equal(await page.locator('[data-fixed-body]').isVisible(), true, key + ': open');
      await redrawn(page, () => page.locator('#coach-fixed-day-2').check({ force: true }));
      await page.waitForFunction(() => document.querySelector('#coach-preview .pf-coach-pv-col:nth-child(3) .pf-coach-bar-s'));
      assert.equal(await page.locator('#coach-fixed-min').inputValue(), '75', key);
      assert.equal(await page.locator('[data-fixed-why]').innerText(), 'Sans sortie ce jour-là : 1h15 par défaut.', key);
      assert.equal(await page.locator('#coach-day-2').isChecked(), true, key + ': ticked among the days');
      assert.ok((await previewBars(page)).some(([day, kind, duration]) => day === 2 && kind === 'hard' && /1h15/.test(duration)), key + ': the club session drawn');
      assert.match(await page.locator('#coach-preview').getAttribute('aria-label'), /mercredi : Séance club 1 heure 15/, key);
      assert.match(await page.locator('.pf-coach-pv-lines').innerText(), /Séance club est ta séance de qualité de la semaine/, key);
      const fixedChips = await page.locator('[data-fixed-body] .pf-coach-chip').evaluateAll(all => all.map(el => Math.round(el.getBoundingClientRect().top)));
      assert.equal(fixedChips.length, 7, key);
      if (width >= 393) assert.equal(new Set(fixedChips).size, 1, key + ': the weekdays on one line');
      await page.locator('#coach-fixed').scrollIntoViewIfNeeded();
      await noOverflow(page, key);
      await shot(page, `new-${width}-onboarding-fixed${dark ? '-dark' : ''}`);
      assert.equal((await (await fetch(fresh + '/coaching/calendar', { headers: { 'HX-Request': 'true' } })).status), 404, key + ': nothing written');
      assert.deepEqual(page.errors, [], key + ': browser errors');
      await page.close();
    }
    {
      // an app from before the tab: by the card; then the plan is made
      const page = await open(browser, { width: 393, app: 'old' });
      await page.goto(fresh + '/activities', { waitUntil: 'networkidle' });
      const entry = page.locator('a.pf-coach-entry');
      const first = await page.locator('.pf-activities > *').nth(1).getAttribute('class');
      assert.match(first, /pf-coach-entry/, 'at the top');
      await entry.click();
      await page.waitForURL('**/coaching');
      // out of the box: one tap makes the plan proposed, and the page opens on today's session
      const announced = await page.locator('#coach-preview-first b').innerText();
      await noOverflow(page, 'new onboarding');
      await page.getByRole('button', { name: 'Créer mon plan' }).click();
      await page.waitForURL('**/coaching');
      assert.equal(await page.locator('.pf-coach-day').count(), 14);
      assert.match(await page.locator('.pf-coach-goal').innerText(), /Progresser, sans course/);
      assert.match(await page.locator('.pf-coach-date').innerText(), /^Aujourd’hui · /);
      assert.equal((await page.locator('.pf-coach-s-title').innerText()).replace(/\s+/g, ' '), announced, 'the first session announced');
      assert.deepEqual(await seen(page, '#coach-main .pf-btn-primary'), ['C’est fait']);
      assert.equal(await page.locator('.pf-coach-week.is-current .pf-coach-bar-s').count(), 2, 'this week: today and Sunday');
      assert.equal(await page.locator('.pf-coach-debrief').count(), 0, 'nothing to debrief yet');
      await noOverflow(page, 'new plan');
      await shot(page, 'new-393-plan-app-old');
      // « C’est fait »: one tap, in place; the ressenti is asked at once
      await swap(page, () => page.locator('[id^="coach-done-"]').click());
      assert.equal(await page.locator('[data-feel]').count(), 5);
      assert.match(await page.locator('#debrief.pf-coach-debrief h2').innerText(), /^Ta séance d’aujourd’hui est faite\u00a0— c’était comment/, 'asked at the top, once');
      assert.equal(await page.locator('.pf-coach-daycard [data-feel]').count(), 0);
      assert.equal(await page.locator('.pf-coach-s-head .pf-pill').innerText(), 'Fait');
      assert.deepEqual(await seen(page, '.pf-coach-daycard .pf-coach-quietbtn'), ['Annuler', 'Ajouter une note']);
      assert.equal(await page.locator('[id^="coach-undo-"]').getAttribute('aria-label'), 'Annuler « fait »');
      await targets(page, 'ticked by hand');
      await noOverflow(page, 'ticked by hand');
      assert.deepEqual(page.errors, [], 'onboarding: browser errors');
      await page.close();
    }
    {
      // an account with a running habit and no plan: its own days ticked, its usual long-run day, its next race,
      // its computed volume — and the week all of it makes
      const as = who => fetch(`${fresh}/__preview/coaching/as/${who}`, { method: 'POST' }).then(r => r.json());
      assert.deepEqual(await as('habit'), { synthetic: true, as: 'habit' });
      for (const dark of [false, true]) {
        const page = await open(browser, { width: 393, dark });
        await page.goto(fresh + '/coaching', { waitUntil: 'networkidle' });
        assert.deepEqual(await page.locator('[data-days] input:checked').evaluateAll(all => all.map(i => i.value)), ['0', '2', '5'], 'the days he runs');
        assert.equal(await page.locator('#coach-long').inputValue(), '5', 'his usual long-run day');
        assert.equal(await page.locator('input[name="goal"]:checked + span b').innerText(), 'Semi des Vignes', 'his next race');
        assert.match(await page.locator('#coach-base option:checked').innerText(), /^2h5\d|^3h0\d$/, 'his computed volume');
        assert.deepEqual((await previewBars(page)).map(([day, kind]) => [day, kind]), [[0, 'easy'], [2, 'hard'], [5, 'long']]);
        assert.equal(await page.getByRole('button', { name: 'Créer mon plan' }).isDisabled(), false);
        await noOverflow(page, 'habit onboarding');
        await shot(page, `habit-393-onboarding${dark ? '-dark' : ''}`);
        assert.deepEqual(page.errors, [], 'habit onboarding: browser errors');
        await page.close();
      }
      const wide = await open(browser, { width: 1280, height: 900 });
      await wide.goto(fresh + '/coaching', { waitUntil: 'networkidle' });
      await noOverflow(wide, 'habit onboarding desktop');
      await shot(wide, 'habit-1280-onboarding');
      await wide.close();
      await as('new');
    }
    {
      // the owner's story: a new account opens Coaching, adds « 10 km du lac d’Annecy » on 24 April 2027 — 10 km, no
      // GPX file —, keeps two days (Tuesday and Sunday) and gets a plan in the calendar
      const as = who => fetch(`${fresh}/__preview/coaching/as/${who}`, { method: 'POST' }).then(r => r.json());
      assert.deepEqual(await as('story'), { synthetic: true, as: 'story' });
      // (the plan is made at the last one: 393 px, light)
      for (const [width, dark] of [[320, false], [320, true], [393, true], [393, false]]) {
        const key = `story ${width}${dark ? ' dark' : ''}`;
        const page = await open(browser, { width, dark });
        await page.goto(fresh + '/coaching', { waitUntil: 'networkidle' });
        await redrawn(page, () => page.locator('input[name="intent"][value="course"]').check({ force: true }));
        assert.equal(await page.locator('#coach-races').isVisible(), true, key);
        assert.equal(await page.locator('#coach-goal-new').isChecked(), true, key + ': no race yet: « Ajouter une course », open');
        assert.equal(await page.locator('#coach-newrace').isVisible(), true, key);
        assert.equal(await page.getByRole('button', { name: 'Créer mon plan' }).isDisabled(), true, key + ': waits for the race');
        await page.locator('#race-name').fill('10 km du lac d’Annecy');
        await page.locator('#race-date').fill('2027-04-24');
        await redrawn(page, async () => { await page.locator('#race-km').fill('10'); await page.locator('#race-km').blur(); });
        await redrawn(page, () => page.locator('[data-days] input[value="3"]').uncheck({ force: true }));
        assert.deepEqual(await page.locator('[data-days] input:checked').evaluateAll(all => all.map(i => i.value)), ['1', '6'], key);
        assert.deepEqual((await previewBars(page)).map(([day, kind]) => [day, kind]), [[1, 'hard'], [6, 'long']], key + ': a session with rhythm and a long run');
        const lines = await page.locator('.pf-coach-pv-lines li').allInnerTexts();
        assert.ok(lines.some(l => /^Sans temps visé, une seule séance de qualité/.test(l)), key + ' ' + lines);
        assert.ok(lines.some(l => /^Deux séances par semaine suffisent/.test(l)), key + ' ' + lines);
        assert.ok(lines.some(l => /^Pour aller plus loin, c’est une troisième séance/.test(l)), key + ' ' + lines);
        assert.equal(await page.getByRole('button', { name: 'Créer mon plan' }).isDisabled(), false, key);
        const chips = await page.locator('.pf-coach-chip, .pf-coach-option, .pf-coach-intent, #coach-newrace .pf-input').evaluateAll(all => all.filter(el => el.getClientRects().length).map(el => {
          const r = el.getBoundingClientRect(); return [el.id || el.className, Math.round(r.width), Math.round(r.height)]; }));
        assert.deepEqual(chips.filter(([, w, h]) => w < (width < 393 ? 32 : 40) || h < 44), [], key + ': 44 px choices');
        await noOverflow(page, key);
        await shot(page, `story-${width}-onboarding${dark ? '-dark' : ''}`);
        if (width === 393 && !dark) {
          await page.getByRole('button', { name: 'Créer mon plan' }).click();
          await page.waitForURL('**/coaching');
          assert.match(await page.locator('.pf-coach-goal').innerText(), /10 km du lac d’Annecy/);
          assert.match(await page.locator('.pf-coach-goal-time').innerText(), /^Environ\s55 min$/);
          assert.equal(await page.locator('a.pf-coach-goal-time').count(), 0, 'no race page to link to');
          assert.match(await page.locator('.pf-coach-head .pf-coach-level').first().innerText(), /^Tu cours régulièrement/);
          await noOverflow(page, 'story plan');
          await shot(page, 'story-393-plan');
          await theme(page, true); await shot(page, 'story-393-plan-dark'); await theme(page, false);
          await page.goto(fresh + '/coaching?d=2027-04-24', { waitUntil: 'networkidle' });
          assert.match(await page.locator('.pf-coach-s-title').first().innerText(), /Course/);
          await shot(page, 'story-393-race-day');
          await page.goto(fresh + '/coaching', { waitUntil: 'networkidle' });
          await page.locator('details.pf-coach-how > summary').click();
          assert.match(await page.locator('details.pf-coach-how').innerText(), /Deux jours par semaine : une séance avec du rythme/);
          await shot(page, 'story-393-how');
          await page.goto(fresh + '/coaching?v=mois&w=2027-04-01', { waitUntil: 'networkidle' });
          await shot(page, 'story-393-april');
          await page.goto(fresh + '/coaching?refaire=1', { waitUntil: 'networkidle' });
          assert.equal(await page.locator('input[name="goal"][value="race"]').isChecked(), true, 'its race, chosen again');
          await shot(page, 'story-393-redo');
        }
        assert.deepEqual(page.errors, [], key + ': browser errors');
        await page.close();
      }
      await as('new');
    }

    // ── the coach ──
    {
      const page = await open(browser, { width: 393 });
      await page.goto(coach + '/coaching/athletes', { waitUntil: 'networkidle' });
      const card = page.locator('.pf-coach-athlete');
      assert.equal(await card.count(), 1);
      assert.match(await card.innerText(), /Stella/);
      assert.match(await page.locator('h1').innerText(), /^Plans suivis\s+bêta$/i);
      assert.equal(await page.locator('label[for="coach-code"]').innerText(), 'Suivre le plan de quelqu’un');
      assert.doesNotMatch(await page.locator('#main-content').innerText(), /\bcoachs?\b|athlète/i, 'the follower’s list names people, not roles');
      assert.ok(await card.locator('.pf-coach-bar b').isVisible(), 'her week at a glance');
      assert.ok(await card.locator('.pf-coach-recent li').count() >= 3, 'her last sessions');
      assert.ok(await card.locator('.pf-coach-recent-feel').first().isVisible(), 'with their ressenti');
      await noOverflow(page, 'coach list');
      await shot(page, 'coach-list-393');
      await theme(page, true); await shot(page, 'coach-list-393-dark'); await theme(page, false);
      // a wrong code says so, in place
      await page.locator('#coach-code').fill('ZZZZ-ZZZZ');
      await page.getByRole('button', { name: 'Suivre' }).click();
      await page.locator('#coach-code-error').waitFor();
      assert.match(await page.locator('#coach-code-error').innerText(), /Code invalide ou expiré/);
      await page.goto(coach + '/coaching/athletes', { waitUntil: 'networkidle' });
      await page.locator('.pf-coach-athlete-head').click();
      await page.waitForURL(`**/coaching/athletes/${fixture.ids.athlete}`);
      assert.match(await page.locator('h1').innerText(), /^Stella/);
      assert.equal(await page.locator('#main-content a[href^="/activity/"]').count(), 0, 'never the activity itself');
      assert.equal(await page.locator('#main-content a[href^="/sante"]').count(), 0, 'never Santé');
      // the same day as the athlete's: the session was adapted, never why
      assert.equal(await page.locator('.pf-coach-formline').innerText(), 'Séance adaptée ce matin');
      assert.doesNotMatch(await page.locator('#main-content').innerText(), /récupération basse|VFC|sommeil|forme|Santé/i, 'no health word on a coach’s page');
      assert.equal(await page.locator('#coach-form-switch').count(), 0);
      // the times of the sessions, nothing of the calendar behind them
      assert.match(await page.locator('.pf-coach-slot-time').innerText(), /^12\sh\s00 – 12\sh\s30$/);
      assert.equal(await page.locator('[id^="coach-slot-"]').count(), 0, 'no « Autre créneau » for a coach');
      assert.doesNotMatch(await page.content(), /calendar\.google|Mon agenda|Journée chargée|est plein/);
      assert.equal(await page.locator('[data-feel]').count(), 0, 'the ressenti is the athlete’s to say');
      assert.equal(await page.locator('#coach-tools').count(), 0);
      assert.equal(await page.locator('#coach-options').count(), 0, 'the options are the athlete’s');
      assert.equal(await page.locator('a.pf-back').innerText(), 'Plans suivis');
      assert.doesNotMatch(await page.locator('#main-content').innerText(), /\bcoachs?\b|athlète/i, 'never « coach » on a follower’s page');
      // the same layout, with what a follower may do: the calendar, the day opened under it, no tick, « Déplacer »
      const [grid, day] = [await page.locator('.pf-coach-grid').boundingBox(), await page.locator('.pf-coach-daycard').boundingBox()];
      assert.ok(grid.y + grid.height <= day.y, 'the weeks, then the day');
      assert.equal(await page.locator('.pf-coach-day').count(), 14, 'this week and the next');
      assert.equal(await page.locator(`#coach-day-${fixture.day}.is-today.is-selected`).count(), 1);
      assert.equal(await page.locator('[id^="coach-done-"], [id^="coach-missed-"]').count(), 0, 'a follower never ticks');
      assert.deepEqual(await seen(page, '#coach-main .pf-btn-primary'), []);
      assert.deepEqual(await seen(page, '.pf-coach-do .pf-btn'), ['Déplacer']);
      assert.deepEqual(await seen(page, '.pf-coach-daycard .pf-coach-quietbtn'), ['Modifier', 'Commenter', 'Ajouter une séance']);
      assert.equal(await page.locator('.pf-coach-gauge').getAttribute('aria-label'), 'Effort facile : niveau 2 sur 4');
      assert.deepEqual(await page.locator('.pf-coach-week.is-current .pf-coach-bar-s').evaluateAll(all => all.map(b => b.className.replace('pf-coach-bar-s ', ''))),
        ['is-easy is-done', 'is-extra is-done', 'is-easy is-planned', 'is-hard is-planned', 'is-long is-planned'], 'her week, the run outside the plan too');
      // the debrief is hers: no question, no mark
      assert.equal(await page.locator('.pf-coach-debrief, .pf-coach-todo').count(), 0, 'never asked for a ressenti');
      assert.doesNotMatch(await page.locator('#coach-main').innerText(), /débriefer|c’était comment/i);
      assert.equal(await page.locator('#debrief').count(), 0);
      await targets(page, 'coach athlete');
      // a comment on today's session (behind its fold), then a session added two days on
      await page.locator('.pf-coach-notefold > summary').click();
      await page.locator('.pf-coach-note textarea').first().fill('Bien géré. Garde cette allure jeudi.');
      await swap(page, () => page.locator('.pf-coach-note button').first().click());
      assert.equal(await page.locator('.pf-coach-said').innerText(), 'Ton commentaire\nBien géré. Garde cette allure jeudi.');
      assert.equal(await page.locator('.pf-coach-notefold > summary').innerText(), 'Commenter', 'one short word, the comment kept in its field');
      const row = await page.locator('.pf-coach-quiet > :is(a, details) , .pf-coach-quiet > details > summary').evaluateAll(all => [...new Set(all.map(el => Math.round(el.getBoundingClientRect().top)))]);
      assert.equal(row.length, 1, 'the session’s quiet actions sit on one line ' + JSON.stringify(row));
      await noOverflow(page, 'coach athlete');
      await shot(page, 'coach-athlete-393');
      await theme(page, true); await shot(page, 'coach-athlete-393-dark'); await theme(page, false);
      // her run outside the plan: on its day, its summary only — never the activity, nothing to answer or attach
      const wed = new Date(fixture.day + 'T00:00:00Z');
      wed.setUTCDate(wed.getUTCDate() - 1);
      await swap(page, () => page.locator(`#coach-day-${wed.toISOString().slice(0, 10)}`).click());
      assert.match(await page.locator('.pf-coach-activity.is-extra').innerText(), /^Hors plan · Footing avec Léa\n/);
      assert.equal(await page.locator('#main-content a[href^="/activity/"]').count(), 0, 'never the activity itself');
      assert.equal(await page.locator('.pf-coach-attach, #coach-main [data-feel]').count(), 0);
      await shot(page, 'coach-athlete-393-offplan');
      await swap(page, () => page.locator(`#coach-day-${fixture.day}`).click());
      await swap(page, () => page.locator('#coach-next').click());
      const empty = page.locator('a.pf-coach-day:not(:has(.pf-coach-bar-s))').first();
      await swap(page, () => empty.click());
      await page.locator('#coach-add > summary').click();
      await page.locator('#coach-add-kind').selectOption('seance');
      await page.locator('#coach-add-dur').selectOption('55');
      await swap(page, () => page.locator('#coach-add-ok').click());
      assert.match(await page.locator('.pf-coach-s-title').innerText(), /Séance\s+55 min/);
      assert.match(await page.locator('.pf-coach-s-text').innerText(), /3 × 8 min soutenu, récup 2 min/);
      assert.match(await page.locator('.pf-coach-edited').innerText(), /Modifiée par toi/);
      await noOverflow(page, 'coach added');
      // a done session as a coach reads it: the ressenti on the same scale (read only), her note, his comment
      const sunday = new Date(fixture.day + 'T00:00:00Z');
      sunday.setUTCDate(sunday.getUTCDate() - 4);
      await page.goto(`${coach}/coaching/athletes/${fixture.ids.athlete}?d=${sunday.toISOString().slice(0, 10)}`, { waitUntil: 'networkidle' });
      assert.match(await page.locator('.pf-coach-feel.is-readonly').getAttribute('aria-label'), /^Ressenti : (Très facile|Facile|Bien|Dur|Très dur)$/);
      assert.equal(await page.locator('.pf-coach-feel.is-readonly button').count(), 0);
      assert.match(await page.locator('.pf-coach-said.is-note').innerText(), /^Sa note\n/);
      assert.equal(await page.locator('#main-content a[href^="/activity/"]').count(), 0, 'never the activity itself');
      assert.doesNotMatch(await page.locator('#main-content').innerText(), /récupération basse|VFC|sommeil|forme|Santé/i, 'no health word on a coach’s page');
      await noOverflow(page, 'coach done');
      await shot(page, 'coach-athlete-393-done');
      // by hand, what « Déplacer » gives a follower: the sessions still to do, never the run outside the plan
      await page.goto(`${coach}/coaching/athletes/${fixture.ids.athlete}`, { waitUntil: 'networkidle' });
      assert.ok(await page.locator('#coach-main [data-move]').count() >= 4, 'the sessions still to do can be picked up');
      assert.equal(await page.locator('#coach-main [data-run]').count(), 0, 'the run outside the plan stays the athlete’s');
      assert.equal(await page.locator('#coach-main .is-done[data-move], #coach-main .is-race[data-move]').count(), 0);
      assert.deepEqual(page.errors, [], 'coach: browser errors');
      await page.close();
      const wide = await open(browser, { width: 1280, height: 900 });
      await wide.goto(`${coach}/coaching/athletes/${fixture.ids.athlete}`, { waitUntil: 'networkidle' });
      await noOverflow(wide, 'coach desktop');
      await shot(wide, 'coach-athlete-1280');
      await wide.goto(coach + '/coaching/athletes', { waitUntil: 'networkidle' });
      await shot(wide, 'coach-list-1280');
      await wide.close();
    }
    console.log('coaching visuals: ok, screenshots in ' + out);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
