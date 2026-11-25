// Local synthetic preview only. Start activities_visual on 8881 (mixed), 8882 (empty).
// Optional PF_ACTIVITIES_BEFORE=http://127.0.0.1:8880 captures an archived baseline.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { coachingTab } = require('./app-shell.cjs');
const base = process.env.PF_ACTIVITIES_PREVIEW || 'http://127.0.0.1:8881';
const empty = process.env.PF_ACTIVITIES_EMPTY || 'http://127.0.0.1:8882';
const before = process.env.PF_ACTIVITIES_BEFORE;
const output = process.env.PF_ACTIVITIES_SCREENSHOTS || '/tmp/paceforge-activities';

async function synthetic(url, scenario) {
  const fixture = await (await fetch(url + '/__preview/activities')).json();
  assert.equal(fixture.synthetic, true, 'Never run against a real account');
  assert.equal(fixture.scenario, scenario);
  return fixture;
}

async function prepare(page, url, scale = 1, dark = false) {
  await page.goto(url + '/activities', { waitUntil: 'networkidle' });
  await page.evaluate(({ scale, dark }) => {
    document.documentElement.style.fontSize = 16 * scale + 'px';
    document.documentElement.classList.toggle('pf-large-text', scale >= 1.4);
    document.documentElement.classList.toggle('dark', dark);
    scrollTo({ top: 0, behavior: 'instant' });
  }, { scale, dark });
}

const lapsOutput = process.env.PF_LAPS_SCREENSHOTS || output;

// An activity's « Intervalles » (activity_laps.py): the chart above its table in the same card, a bar per lap, a
// tap and the arrow keys read a lap and mark its row, the plot never moves, no horizontal scroll (240 laps too)
async function lapsPage(browser, fixture, width, scale, dark) {
  const key = `${width}-${scale}-${dark ? 'dark' : 'light'}`;
  const page = await browser.newPage({ viewport: { width, height: 800 }, isMobile: true, hasTouch: true,
    reducedMotion: 'reduce' });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => { window.ReactNativeWebView = { postMessage() {} }; });
  await page.addInitScript(coachingTab);
  const open = async id => {
    await page.goto(`${base}/activity/${id}`, { waitUntil: 'networkidle' });
    await page.evaluate(({ scale, dark }) => {
      document.documentElement.style.fontSize = 16 * scale + 'px';
      document.documentElement.classList.toggle('pf-large-text', scale >= 1.4);
      document.documentElement.classList.toggle('dark', dark);
    }, { scale, dark });
    await page.waitForTimeout(150);
    const fig = page.locator('.pf-laps-chart figure[data-viz]');
    await fig.scrollIntoViewIfNeeded();
    return fig;
  };
  const overflow = async label => {
    const sizes = await page.evaluate(() => ({ viewport: innerWidth, page: document.documentElement.scrollWidth }));
    assert.ok(sizes.page <= sizes.viewport + 1, `${label}: horizontal overflow ${JSON.stringify(sizes)}`);
  };
  const tap = (fig, i) => fig.evaluate((fig, i) => {
    const svg = fig.querySelector('svg.pf-viz-svg'), bar = svg.querySelector(`[data-i="${i}"] rect`).getBoundingClientRect();
    const point = { clientX: bar.x + bar.width / 2, clientY: bar.y + bar.height - 2 };
    for (const type of ['pointerdown', 'pointerup']) {
      svg.dispatchEvent(new PointerEvent(type, { bubbles: true, pointerType: 'touch', pointerId: 1, ...point }));
    }
  }, i);
  // the club track session: warm-up, 2 × (8 × 300 m), cool-down
  let fig = await open(fixture.laps.track);
  const card = await page.locator('#laps-rows').evaluate(rows => rows.closest('.rounded-xl').querySelector('.pf-laps-chart') !== null);
  assert.ok(card, key + ': the chart sits in the laps card');
  const figBox = await fig.boundingBox(), tableBox = await page.locator('#laps-rows').boundingBox();
  assert.ok(figBox.y + figBox.height <= tableBox.y, key + ': the chart is above the table');
  const data = await fig.evaluate(fig => JSON.parse(fig.querySelector('.pf-viz-data').textContent));
  assert.equal(data.r.length, await page.locator('#laps-rows tr').count(), key + ': one readout per lap');
  assert.equal(await fig.locator('.pf-lap').count(), data.r.length, key + ': one bar per lap');
  assert.equal(await fig.locator('.pf-lap.is-effort').count(), 16, key + ': the 16 repetitions in the accent');
  assert.match(await fig.locator('figcaption').innerText(), /33 tours en 1 heure 02\. Le plus rapide : le numéro \d+/);
  const plotY = async () => fig.evaluate(fig => fig.querySelector('svg').getBoundingClientRect().y - fig.getBoundingClientRect().y);
  const readH = async () => fig.evaluate(fig => fig.querySelector('.pf-viz-read').getBoundingClientRect().height);
  const y0 = await plotY(), h0 = await readH();
  await tap(fig, 1);
  const input = fig.locator('.pf-viz-range');
  assert.equal(await input.inputValue(), '1', key + ': a tap selects the bar under the finger');
  assert.equal(await input.getAttribute('aria-valuetext'), data.a[1]);
  const read = await fig.locator('.pf-viz-read').innerText();
  assert.match(read, /Intervalle 2\s·\s1'0\d"\s·\s300\sm/, key + ': ' + read);
  assert.match(read, /3:\d\d\/km/);
  assert.match(read, /1\d\d\sbpm/);
  assert.ok(await page.locator('#laps-rows [data-i="1"]').evaluate(row => row.classList.contains('is-sel')), key + ': its row is marked');
  assert.ok(await fig.locator('[data-i="1"]').evaluate(g => g.classList.contains('is-sel')), key + ': its bar is marked');
  await page.waitForTimeout(50);  // reduced motion still runs a 0.01 ms transition (interface.css): read after it
  assert.equal(await fig.locator('.pf-viz-cross').evaluate(g => getComputedStyle(g).visibility), 'visible', key + ': the caret over it');
  if (width === 393 || scale === 2) await page.screenshot({ path: `${lapsOutput}/laps-track-${key}.png` });
  await input.focus();
  await page.keyboard.press('ArrowRight');
  assert.equal(await input.inputValue(), '2', key + ': the right arrow walks to the next lap');
  assert.ok(await page.locator('#laps-rows [data-i="2"]').evaluate(row => row.classList.contains('is-sel')));
  assert.equal(await page.locator('#laps-rows .is-sel').count(), 1, key + ': one row marked');
  await page.keyboard.press('ArrowLeft');
  await page.keyboard.press('ArrowLeft');
  assert.equal(await input.inputValue(), '0', key + ': the left arrow walks back');
  assert.match(await fig.locator('.pf-viz-read').innerText(), /Intervalle 1\s·\s16'30"\s·\s3,00\skm/);
  let moved = 0, heights = new Set();
  for (let i = 0; i <= data.r.length; i++) {
    await input.evaluate((el, i) => { el.value = i; el.dispatchEvent(new Event('input')); }, i);
    moved = Math.max(moved, Math.abs(await plotY() - y0));
    heights.add(Math.round(await readH()));
  }
  assert.ok(moved <= 1, `${key}: the plot moved ${moved}px while selecting`);
  assert.ok(Math.max(...heights) - Math.min(...heights) <= 1 && Math.abs(Math.max(...heights) - h0) <= 1, `${key}: readout height changed ${[...heights]}`);
  assert.match(await fig.locator('.pf-viz-read').innerText(), /33 intervalles\s·\s1h02/, key + ': past the last lap, the summary');
  await overflow(key + ' track');
  // 240 laps: thin bars, still no horizontal scroll, still a tap
  fig = await open(fixture.laps.many);
  assert.equal(await fig.locator('.pf-lap').count(), 240, key + ': 240 bars');
  await overflow(key + ' 240 laps');
  await tap(fig, 120);
  assert.equal(await fig.locator('.pf-viz-range').inputValue(), '120', key + ': a thin bar is still tapped');
  if (width === 320) await page.screenshot({ path: `${lapsOutput}/laps-many-${key}.png` });
  // the ride: its power
  fig = await open(fixture.laps.ride);
  assert.equal(await fig.locator('svg text').last().textContent(), 'W');
  await tap(fig, 1);
  assert.match(await fig.locator('.pf-viz-read').innerText(), /Intervalle 2\s·\s10'00"\s·\s6,00\skm\s+290\sW\s+36,0\skm\/h\s·\s158\sbpm/);
  await overflow(key + ' ride');
  if (width === 393 && scale === 1) await page.screenshot({ path: `${lapsOutput}/laps-ride-${key}.png` });
  assert.deepEqual(errors, [], key + ': browser errors on the activity pages');
  await page.close();
}

async function noOverflow(page, label) {
  const sizes = await page.evaluate(() => ({ viewport: innerWidth, page: document.documentElement.scrollWidth }));
  assert.ok(sizes.page <= sizes.viewport + 1, label + ': horizontal overflow ' + JSON.stringify(sizes));
  const clipped = await page.locator('.pf-activity-row').evaluateAll(rows => rows.flatMap(row => {
    const box = row.getBoundingClientRect();
    return [...row.querySelectorAll('.pf-item-title, .pf-activity-mobile-stats')].flatMap(el => {
      if (!el.getClientRects().length) return [];
      const r = el.getBoundingClientRect();
      return r.left < box.left - 1 || r.right > box.right + 1 ? [el.className] : [];
    });
  }));
  assert.deepEqual(clipped, [], label + ': activity content exceeds its card');
}

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const fixture = await synthetic(base, 'mixed');
  await synthetic(empty, 'empty');
  if (before) await synthetic(before, 'mixed');
  const browser = await chromium.launch({ headless: true });
  const measurements = [];
  const placementFailures = [];
  try {
    for (const width of [320, 393]) for (const scale of [1, 2]) for (const dark of [false, true]) {
      // 700px represents the content viewport below the native header and above tabs.
      const page = await browser.newPage({ viewport: { width, height: 700 }, isMobile: true,
        hasTouch: true, reducedMotion: 'reduce' });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.addInitScript(() => {
        window.ReactNativeWebView = { postMessage() {} }; window.paceforgeNativeRefresh = 2;
      });
      await page.addInitScript(coachingTab);
      const key = `${width}-${scale}-${dark ? 'dark' : 'light'}`;
      if (before && scale === 1) {
        await prepare(page, before, scale, dark);
        await page.screenshot({ path: `${output}/before-${key}.png` });
      }
      await prepare(page, base, scale, dark);
      assert.ok(await page.locator('#semaines svg.pf-viz-svg').filter({ visible: true }).isVisible(),
        key + ': weekly histogram is visible immediately');
      assert.equal(await page.locator('#semaines').evaluate(el => el.tagName), 'SECTION');
      assert.doesNotMatch(await page.locator('#main-content').innerText(), /COROS|Garmin|Strava|hors connexion/);
      assert.equal(await page.locator('#main-content a[href*="simulator"]').count(), 0,
        key + ': activities do not lead to planner');
      const first = await page.locator('[id^="activity-row-"]').first().boundingBox();
      measurements.push({ key, first_activity_y: Math.round(first.y), first_activity_bottom: Math.round(first.y + first.height) });
      if (scale === 1 && first.y + first.height > 700) placementFailures.push(key);
      const strength = page.locator('.pf-activity-row').filter({ hasText: 'Renforcement · jambes et gainage' });
      const mobility = page.locator('.pf-activity-row').filter({ hasText: 'Mobilité après la sortie' });
      for (const row of [strength, mobility]) {
        assert.doesNotMatch(await row.locator('.pf-activity-mobile-stats').innerText(), /\b0(?:[,.]0)?\s*km/);
        assert.match(await row.locator('.pf-activity-primary').innerText(), /min|\d+'\d+"/,
          key + ': non-distance sport emphasizes duration');
      }
      await noOverflow(page, key);
      await page.screenshot({ path: `${output}/after-${key}.png` });
      if (scale === 2) await page.locator('#activity-list > section').first().screenshot({ path: `${output}/after-${key}-week.png` });
      if (width === 393 && scale === 1 && !dark) {
        await page.locator('#activity-list > section').first().screenshot({ path: `${output}/after-current-week.png` });
      }
      await page.locator('#semaines button[data-range="distance"]').click();
      assert.equal(await page.locator('#semaines button[data-range="distance"]').getAttribute('aria-pressed'), 'true');
      const chart = page.locator('#semaines [data-range-panel="distance"] figure');
      assert.ok(await chart.isVisible());
      const selected = await chart.evaluate(fig => {
        const data = JSON.parse(fig.querySelector('.pf-viz-data').textContent);
        const input = fig.querySelector('.pf-viz-range');
        input.value = data.d.length - 2; input.dispatchEvent(new Event('input'));
        return { spoken: data.a[data.d.length - 2], read: data.r[data.d.length - 2], link: data.h[data.d.length - 2].href };
      });
      assert.equal(await chart.locator('.pf-viz-range').getAttribute('aria-valuetext'), selected.spoken);
      assert.ok((await chart.locator('.pf-viz-read').innerText()).includes(selected.read[1]));
      assert.equal(await chart.locator('[data-r-href]').getAttribute('href'), selected.link);
      await chart.getByRole('button', { name: 'Semaine précédente' }).click();
      assert.notEqual(await chart.locator('.pf-viz-range').getAttribute('aria-valuetext'), selected.spoken);
      const tapped = await chart.evaluate(fig => {
        const data = JSON.parse(fig.querySelector('.pf-viz-data').textContent);
        const svg = fig.querySelector('svg.pf-viz-svg');
        const rect = svg.getBoundingClientRect();
        const matrix = svg.getScreenCTM();
        const at = index => ({ x: matrix.a * data.x[index] + matrix.e, y: rect.y + rect.height / 2 });
        const start = at(8), end = at(9);
        for (const [type, point] of [['pointerdown', start], ['pointermove', end], ['pointerup', end]]) {
          svg.dispatchEvent(new PointerEvent(type, { bubbles: true, pointerType: 'touch', pointerId: 1,
            clientX: point.x, clientY: point.y }));
        }
        return { index: 9, spoken: data.a[9] };
      });
      assert.equal(await chart.locator('.pf-viz-range').inputValue(), String(tapped.index), key + ': horizontal finger scrub selects a week');
      assert.equal(await chart.locator('.pf-viz-range').getAttribute('aria-valuetext'), tapped.spoken);
      await chart.locator('[data-r-href]').click();
      assert.ok(page.url().includes('#week-'), key + ': chart leads to its actual activities');
      assert.deepEqual(errors, [], key + ': browser errors');
      await page.close();
    }
    fs.mkdirSync(lapsOutput, { recursive: true });
    for (const width of [320, 393]) for (const scale of [1, 2]) for (const dark of [false, true]) {
      await lapsPage(browser, fixture, width, scale, dark);
    }
    const page = await browser.newPage({ viewport: { width: 393, height: 700 }, isMobile: true, hasTouch: true });
    await page.addInitScript(() => { window.ReactNativeWebView = { postMessage() {} }; });
    await page.addInitScript(coachingTab);
    await prepare(page, base);
    // Filters affect both the histogram and actual records, retaining the selected measure.
    await page.locator('#semaines button[data-range="distance"]').click();
    const bike = page.getByRole('navigation', { name: 'Filtrer les activités par sport' }).getByRole('link', { name: 'Vélo', exact: true });
    assert.match(await bike.getAttribute('href'), /m=distance/);
    await bike.click();
    // the measure toggle already wrote ?m= into the URL: wait for the filter's own
    await page.waitForURL(url => new URL(url).searchParams.get('sport') === 'bike');
    assert.match(page.url(), /m=distance/);
    assert.ok(await page.locator('.pf-activity-row').filter({ hasText: 'Boucle des villages' }).isVisible());
    assert.equal(await page.locator('.pf-activity-row').filter({ hasText: 'Footing au bord du lac' }).count(), 0);
    assert.ok(await page.locator('#semaines [data-range-panel="distance"] figure').isVisible());
    await page.screenshot({ path: `${output}/after-filter-bike.png` });
    await prepare(page, base);
    const initialRows = await page.locator('[id^="activity-row-"]').count();
    await page.getByRole('button', { name: /Semaines précédentes/ }).click();
    await page.waitForFunction(n => document.querySelectorAll('[id^="activity-row-"]').length > n, initialRows);
    const rows = await page.locator('[id^="activity-row-"]').count();
    await page.evaluate(() => window.paceforgeRefresh.refresh(false));
    assert.equal(await page.locator('[id^="activity-row-"]').count(), rows, 'Refresh retains loaded history');
    await prepare(page, empty);
    assert.equal(await page.locator('#semaines').count(), 0, 'Empty history does not fabricate a graph');
    assert.match(await page.locator('#main-content').innerText(), /Aucune activité/);
    assert.ok(await page.getByRole('link', { name: 'Choisir une connexion' }).isVisible());
    await noOverflow(page, 'empty');
    await page.screenshot({ path: `${output}/after-empty.png` });
    await page.close();
    const desktop = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    await prepare(desktop, base);
    await noOverflow(desktop, 'desktop');
    assert.ok(await desktop.locator('#semaines svg.pf-viz-svg').filter({ visible: true }).isVisible());
    await desktop.screenshot({ path: `${output}/after-desktop.png` });
    await desktop.close();
    assert.deepEqual(placementFailures, [], 'First activity must be fully visible in the native content viewport');
    fs.writeFileSync(`${output}/verification.json`, JSON.stringify({ synthetic: true, day: fixture.day,
      configurations: 8, widths: [320, 393], font_scales: [1, 2], themes: ['light', 'dark'],
      native_content_height: 700, desktop: [1280, 900], measurements,
      checked: ['visible histogram', 'first activity visible without scrolling', 'duration for mobility and strength',
        'chart selection and week navigation', 'sport filter and measure retention', 'pagination after refresh',
        'no provider or planner clutter', 'empty history without fabricated graph', 'no horizontal overflow',
        'laps chart above its table: tap, arrow keys, row marked, stable plot, 240 laps, ride power'] }, null, 2));
    console.log('Activities: 8 mobile layouts, desktop, filters, charts, pagination, empty state and the laps chart (8 layouts) passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
