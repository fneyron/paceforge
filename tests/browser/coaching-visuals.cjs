// Coaching (beta, every account): the athlete's calendar, the coach's pages and the onboarding, in a browser — at 320
// and 393 px (the web site; inside the phone app that has the native Coaching tab; inside an installed app older than
// that tab), at 1280 px, light and dark. Local synthetic preview only.
// Start three fixtures (tests/browser/coaching_preview.py), each its own temporary database:
//   PF_COACHING_AS=athlete uvicorn tests.browser.coaching_preview:app --host 127.0.0.1 --port 8861
//   PF_COACHING_AS=coach   uvicorn tests.browser.coaching_preview:app --host 127.0.0.1 --port 8862
//   PF_COACHING_AS=new     uvicorn tests.browser.coaching_preview:app --host 127.0.0.1 --port 8863
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
  const bind = (text, origin) => text.split('${JSON.stringify(ORIGIN)}').join(JSON.stringify(origin));
  return { new: origin => bind(script, origin), old: origin => bind(script.replace(marker, ''), origin) };
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

async function noOverflow(page, label) {
  const sizes = await page.evaluate(() => ({ viewport: innerWidth, page: document.documentElement.scrollWidth }));
  assert.ok(sizes.page <= sizes.viewport + 1, label + ': horizontal overflow ' + JSON.stringify(sizes));
  const clipped = await page.evaluate(() => [...document.querySelectorAll('.pf-coach-card, .pf-coach-entry')].flatMap(card => {
    const box = card.getBoundingClientRect();
    return [...card.querySelectorAll('h2, h3, p, a, button, select, input, textarea, label, svg.pf-effort, figure')].flatMap(el => {
      if (!el.getClientRects().length || el.closest('.sr-only')) return [];
      const r = el.getBoundingClientRect();
      return r.left < box.left - 1 || r.right > box.right + 1 ? [(el.id || el.className || el.tagName) + ' ' + Math.round(r.right - box.right)] : [];
    });
  }));
  assert.deepEqual(clipped, [], label + ': content outside its card');
}

// Every visible control of the calendar part is at least 40 px tall (the house's 44 px targets, 40 for the round steps).
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
      const toggle = page.locator('#coach-form-switch');
      assert.equal(await toggle.getAttribute('aria-checked'), 'true', key);
      assert.ok((await toggle.boundingBox()).height >= 44, key + ': the switch is a 44 px target');
      assert.match(await page.locator('#coach-form-note').innerText(), /^Activé le \S+ \d+ \S+\. Un matin/, key);
      assert.equal(await page.locator('.pf-coach-day').count(), 7, key + ': seven days');
      assert.equal(await page.locator('.pf-coach-day.is-today').count(), 1, key + ': today is highlighted');
      assert.equal(await page.locator(`#coach-day-${fixture.day}.is-selected`).count(), 1, key + ': today by default');
      assert.ok(await page.locator('.pf-coach-bar b').isVisible(), key + ': the week bar says fait / prévu');
      assert.match(await page.locator('.pf-coach-bar b').innerText(), /^\d.* \/ \d/);
      assert.ok(await page.locator('#coach-main svg.pf-viz-svg').isVisible(), key + ': the plan chart');
      assert.ok(await page.locator('svg.pf-effort').first().isVisible(), key + ': the intensity profile is drawn');
      assert.equal(await page.locator('.pf-coach-plan .pf-plan-phase').count(), 3, key + ': the phases are marked');
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
      if (width === 393) await shot(page, `athlete-393-week${dark ? '-dark' : ''}${suffix(app)}`);
      // the calendar: a browser and the updated app get the subscription button (the app hands webcal:// to the
      // system); an older app drops that link, so it only shows the address to copy
      await page.locator('#coach-tool-feed > summary').click();
      assert.equal(await page.locator('#coach-tool-feed a[href^="webcal://"]').isVisible(), app !== 'old', key + ': « S’abonner au calendrier »');
      assert.ok(await page.locator('#coach-feed-url').isVisible() && await page.locator('[data-copy="coach-feed-url"]').isVisible(), key + ': the address to copy');
      await noOverflow(page, key + ' calendar');
      if (width === 393 && !dark && app) await shot(page, `athlete-393-calendar${suffix(app)}`);
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

    // ── taps: no full reload, instant feedback, keyboard ──
    {
      const page = await open(browser, { width: 393 });
      await page.goto(athlete + '/coaching', { waitUntil: 'networkidle' });
      await page.evaluate(() => { window.__same_document = true; });
      const week = await page.locator('.pf-coach-period').innerText();
      await swap(page, () => page.locator('#coach-prev').click());
      assert.notEqual(await page.locator('.pf-coach-period').innerText(), week, 'the week changed');
      assert.equal(await page.evaluate(() => window.__same_document), true, 'in place: no full reload');
      // a done session of last week: the activity's summary, the ressenti, the note
      const doneDay = page.locator('.pf-coach-day:has(.pf-coach-pill.is-done)').last();
      await swap(page, () => doneDay.click());
      assert.ok(await page.locator('.pf-coach-activity').first().isVisible());
      assert.match(await page.locator('.pf-coach-activity small').first().innerText(), /km · .*(min|h\d\d) · \+/);
      assert.match(await page.locator('.pf-coach-activity').first().getAttribute('href'), /^\/activity\/\d+$/);
      const feel = page.locator('[data-feel]');
      assert.equal(await feel.count(), 5);
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
      assert.equal(await page.locator('[data-feel]').nth(pick).getAttribute('aria-pressed'), 'true', 'saved on the tap');
      assert.equal(await page.locator('[data-feel][aria-pressed="true"]').count(), 1);
      await page.locator('.pf-coach-note textarea').fill('Bonnes sensations en côte.');
      await swap(page, () => page.locator('.pf-coach-note button').click());
      assert.equal(await page.locator('.pf-coach-note textarea').inputValue(), 'Bonnes sensations en côte.');
      await noOverflow(page, 'done session');
      await shot(page, 'athlete-393-done-ressenti');
      await theme(page, true); await shot(page, 'athlete-393-done-ressenti-dark'); await theme(page, false);
      // the month
      await swap(page, () => page.locator('#coach-view').click());
      assert.equal(await page.locator('#coach-view').getAttribute('aria-pressed'), 'true');
      assert.ok(await page.locator('.pf-coach-mday').count() >= 28, 'a month of days');
      assert.ok(await page.locator('.pf-coach-mday .pf-coach-pill.is-done').count() >= 3);
      assert.ok(await page.locator('.pf-coach-mday .pf-coach-pill.is-missed').count() >= 1, 'a missed session is marked');
      assert.equal(await page.locator('.pf-coach-mday.is-selected').count(), 1);
      await noOverflow(page, 'month');
      await targets(page, 'month');
      await shot(page, 'athlete-393-month');
      await theme(page, true); await shot(page, 'athlete-393-month-dark'); await theme(page, false);
      await swap(page, () => page.locator('#coach-view').click());
      assert.equal(await page.locator('.pf-coach-day').count(), 7, 'back to the week');
      // next week by the keyboard: a planned session, moved to a free day, then edited
      await page.goto(athlete + '/coaching', { waitUntil: 'networkidle' });
      await page.locator('#coach-next').focus();
      await swap(page, () => page.keyboard.press('Enter'));
      const planned = page.locator('.pf-coach-day:has(.pf-coach-pill.is-planned)').first();
      const from = await planned.getAttribute('id');
      await planned.focus();
      await swap(page, () => page.keyboard.press('Enter'));
      assert.equal(await page.evaluate(() => document.activeElement.id), from, 'the focus stays on the day chosen');
      assert.ok(await page.locator('.pf-coach-s-guide').isVisible(), 'the guidance line');
      await swap(page, () => page.locator('[id^="coach-move-"]:not(#coach-move-cancel)').first().click());
      assert.match(await page.locator('.pf-coach-moving').innerText(), /Choisis le nouveau jour/);
      const free = page.locator('button.pf-coach-day:not(:has(.pf-coach-pill)):not([disabled])').first();
      const to = await free.getAttribute('id');
      await free.focus();
      await swap(page, () => page.keyboard.press('Enter'));
      assert.equal(await page.locator('.pf-coach-moving').count(), 0);
      assert.equal(await page.locator(`#${from} .pf-coach-pill`).count(), 0, 'its old day is free');
      assert.equal(await page.locator(`#${to}.is-selected .pf-coach-pill`).count(), 1, 'it is on the day chosen');
      await page.locator('.pf-coach-edit > summary').first().click();
      const duration = page.locator('.pf-coach-form select[name="minutes"]').first();
      const minutes = (await duration.inputValue()) === '40' ? '50' : '40';
      await duration.selectOption(minutes);
      await noOverflow(page, 'edit form');
      await swap(page, () => page.locator('[id^="coach-save-"]').first().click());
      assert.match(await page.locator('.pf-coach-s-dur').first().innerText(), new RegExp('^' + minutes + ' min$'));
      assert.match(await page.locator('.pf-coach-edited').innerText(), /Modifiée par toi/);
      // the chart: a week chosen says its planned and done time
      const chart = page.locator('.pf-coach-plan figure');
      const read = await chart.evaluate(fig => {
        const data = JSON.parse(fig.querySelector('.pf-viz-data').textContent);
        const input = fig.querySelector('.pf-viz-range');
        input.value = 1; input.dispatchEvent(new Event('input'));
        return data.r[1];
      });
      assert.ok((await chart.locator('.pf-viz-read').innerText()).includes(read[1]));
      // the calendar feed: a webcal link, and the feed itself
      await page.locator('#coach-tool-feed > summary').click();
      const webcal = await page.locator('#coach-tool-feed a[href^="webcal://"]').getAttribute('href');
      assert.match(webcal, /^webcal:\/\/[^/]+\/coaching\/cal\/[\w-]{40,}\.ics$/);
      const feed = await page.evaluate(async path => { const r = await fetch(path); return [r.status, r.headers.get('content-type'), await r.text()]; }, new URL(webcal.replace('webcal://', 'https://')).pathname);
      assert.equal(feed[0], 200);
      assert.match(feed[1], /^text\/calendar/);
      assert.match(feed[2], /^BEGIN:VCALENDAR\r\n/);
      assert.ok(feed[2].includes('DTSTART;VALUE=DATE:') && feed[2].includes('SUMMARY:Footing'));
      await noOverflow(page, 'tools');
      assert.deepEqual(page.errors, [], 'taps: browser errors');
      await page.close();
    }

    // ── desktop ──
    for (const dark of [false, true]) {
      const page = await open(browser, { width: 1280, height: 900, dark });
      await page.goto(athlete + '/coaching', { waitUntil: 'networkidle' });
      assert.deepEqual(await page.locator('.pf-nav a').allInnerTexts(), ['Courses', 'Activités', 'Santé', 'Coaching']);
      assert.equal(await page.locator('.pf-nav a[aria-current="page"]').innerText(), 'Coaching');
      const [cal, day] = [await page.locator('.pf-coach-cal').boundingBox(), await page.locator('.pf-coach-daycard').boundingBox()];
      assert.ok(day.x > cal.x + cal.width - 1 && Math.abs(day.y - cal.y) < 2, 'the day beside the calendar');
      await noOverflow(page, 'desktop');
      await shot(page, `athlete-1280${dark ? '-dark' : ''}`);
      assert.deepEqual(page.errors, []);
      await page.close();
    }

    // ── the onboarding: the athlete's own (a race, a computed volume), then a new account end to end ──
    for (const dark of [false, true]) {
      const page = await open(browser, { width: 393, dark });
      await page.goto(athlete + '/coaching?refaire=1', { waitUntil: 'networkidle' });
      assert.ok(await page.getByText('Trail des Crêtes').first().isVisible());
      assert.match(await page.locator('#coach-base option:checked').innerText(), /^\dh\d\d$/, 'the computed volume');
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
      // no race: one goal, chosen; no history: five coarse volumes, 2 h chosen; no day yet
      assert.deepEqual(await page.locator('.pf-coach-option b').allInnerTexts(), ['Progresser, sans course'], key);
      assert.equal(await page.locator('input[name="goal"]:checked').getAttribute('value'), 'none', key);
      assert.deepEqual((await page.locator('.pf-coach-chip.is-wide').allInnerTexts()).map(t => t.replace(/\s+/g, ' ').trim()),
        ['1 h', '2 h', '3 h', '4 h', '5 h et +'], key);
      assert.equal(await page.locator('input[name="base"]:checked').getAttribute('value'), '120', key);
      assert.equal(await page.locator('#coach-base').count(), 0, key + ': no computed volume without a history');
      assert.equal(await page.locator('[data-days] input:checked').count(), 0, key);
      assert.equal(await page.locator('.pf-coach-error').count(), 0, key);
      const chips = await page.locator('.pf-coach-chip, .pf-coach-option').evaluateAll(all => all.map(el => {
        const r = el.getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height), Math.round(r.top)]; }));
      // 44 px tall; seven days side by side leave 33 px each at 320 px, 43 px at 393 px
      assert.deepEqual(chips.filter(([w, h]) => w < (width < 393 ? 32 : 40) || h < 44), [], key + ': 44 px choices');
      // each row of choices on one line: seven days, seven days, five volumes
      for (const [group, count] of [['[data-days]', 7], ['[data-long]', 7], ['.pf-coach-q-card:last-of-type .pf-coach-chips', 5]]) {
        const tops = await page.locator(`${group} .pf-coach-chip`).evaluateAll(all => all.map(el => Math.round(el.getBoundingClientRect().top)));
        assert.equal(tops.length, count, key + ' ' + group);
        if (width >= 393) assert.equal(new Set(tops).size, 1, key + ': ' + group + ' on one line');
      }
      const submit = await page.getByRole('button', { name: 'Créer mon plan' }).boundingBox();
      assert.ok(submit.height >= 44 && submit.width > width * 0.8, key + ': the button');
      await noOverflow(page, key);
      if (width === 393) await shot(page, `new-393-onboarding${dark ? '-dark' : ''}${suffix(app)}`);
      // the button waits for 2 to 6 days; a form posted without them all the same (no JavaScript) says what is
      // missing, in place (answered 200: the app's shell replaces a 4xx document), and keeps the answers
      assert.equal(await page.getByRole('button', { name: 'Créer mon plan' }).isDisabled(), true, key + ': no day yet');
      if (width === 393 && !dark && !app) {
        await page.locator('.pf-coach-chip.is-wide input[value="180"]').check({ force: true });
        await page.locator('[data-days] input[value="2"]').check({ force: true });
        assert.equal(await page.getByRole('button', { name: 'Créer mon plan' }).isDisabled(), true, 'one day is not enough');
        const posted = page.waitForResponse(r => r.url().endsWith('/coaching/plan'));
        await page.evaluate(() => document.getElementById('coach-onb').submit());
        assert.equal((await posted).status(), 200);
        await page.locator('.pf-coach-error').waitFor();
        assert.match(await page.locator('.pf-coach-error').innerText(), /jour/i);
        assert.equal(await page.locator('input[name="base"]:checked').getAttribute('value'), '180', 'the answer is kept');
        await noOverflow(page, key + ' error');
        await shot(page, 'new-393-onboarding-error');
      }
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
      assert.ok(await page.getByRole('button', { name: 'Créer mon plan' }).isVisible());
      assert.equal(await page.locator('[data-long] input:disabled').count(), 7, 'no long-run day before a day is chosen');
      for (const day of ['1', '3', '6']) await page.locator(`[data-days] input[value="${day}"]`).check({ force: true });
      assert.equal(await page.locator('[data-long] input:checked').getAttribute('value'), '6', 'Sunday by default');
      assert.equal(await page.locator('[data-long] input:disabled').count(), 4);
      await page.locator('[data-days] input[value="6"]').uncheck({ force: true });
      assert.equal(await page.locator('[data-long] input:checked').getAttribute('value'), '3', 'else the last day');
      await page.locator('[data-days] input[value="5"]').check({ force: true });
      await noOverflow(page, 'new onboarding');
      await page.getByRole('button', { name: 'Créer mon plan' }).click();
      await page.waitForURL('**/coaching');
      assert.equal(await page.locator('.pf-coach-day').count(), 7);
      assert.match(await page.locator('.pf-coach-goal').innerText(), /Progresser, sans course/);
      assert.ok(await page.locator('.pf-coach-day .pf-coach-pill').count() >= 1 || await page.locator('#coach-next').count() === 1);
      await noOverflow(page, 'new plan');
      assert.deepEqual(page.errors, [], 'onboarding: browser errors');
      await page.close();
    }

    // ── the coach ──
    {
      const page = await open(browser, { width: 393 });
      await page.goto(coach + '/coaching/athletes', { waitUntil: 'networkidle' });
      const card = page.locator('.pf-coach-athlete');
      assert.equal(await card.count(), 1);
      assert.match(await card.innerText(), /Stella/);
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
      assert.equal(await page.locator('[data-feel]').count(), 0, 'the ressenti is the athlete’s to say');
      assert.equal(await page.locator('#coach-tools').count(), 0);
      // a comment on today's session, then a session added two days on
      await page.locator('.pf-coach-note textarea').first().fill('Bien géré. Garde cette allure jeudi.');
      await swap(page, () => page.locator('.pf-coach-note button').first().click());
      assert.equal(await page.locator('.pf-coach-note textarea').first().inputValue(), 'Bien géré. Garde cette allure jeudi.');
      await noOverflow(page, 'coach athlete');
      await shot(page, 'coach-athlete-393');
      await theme(page, true); await shot(page, 'coach-athlete-393-dark'); await theme(page, false);
      await swap(page, () => page.locator('#coach-next').click());
      const empty = page.locator('a.pf-coach-day:not(:has(.pf-coach-pill))').first();
      await swap(page, () => empty.click());
      await page.locator('#coach-add > summary').click();
      await page.locator('#coach-add-kind').selectOption('seance');
      await page.locator('#coach-add-dur').selectOption('55');
      await swap(page, () => page.locator('#coach-add-ok').click());
      assert.match(await page.locator('.pf-coach-s-title').innerText(), /Séance\s+55 min/);
      assert.match(await page.locator('.pf-coach-s-text').innerText(), /3 × 8 min soutenu, récup 2 min/);
      assert.match(await page.locator('.pf-coach-edited').innerText(), /Modifiée par toi/);
      await noOverflow(page, 'coach added');
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
