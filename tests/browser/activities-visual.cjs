// Local synthetic preview only. Start activities_visual on 8881 (mixed), 8882 (empty).
// Optional PF_ACTIVITIES_BEFORE=http://127.0.0.1:8880 captures an archived baseline.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
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
    const page = await browser.newPage({ viewport: { width: 393, height: 700 }, isMobile: true, hasTouch: true });
    await page.addInitScript(() => { window.ReactNativeWebView = { postMessage() {} }; });
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
        'no provider or planner clutter', 'empty history without fabricated graph', 'no horizontal overflow'] }, null, 2));
    console.log('Activities: 8 mobile layouts, desktop, filters, charts, pagination and empty state passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
