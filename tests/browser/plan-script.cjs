// The race plan page with its script in /static/js/gpx-result.js: every control (objective and slider, export sheet
// and its focus trap, rows, point and add sheets, map, GPX picker, the « … » menu and its Escape, settings forms, race
// sheet, save retry), no console error, no executable inline script in the plan partial, and a plan swapped in by
// htmx set up once.
// Start: python -m uvicorn tests.browser.preview_app:app --host 127.0.0.1 --port 8853
// Run with Playwright installed: node tests/browser/plan-script.cjs
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const base = process.env.PF_PREVIEW_URL || 'http://127.0.0.1:8853';

(async () => {
  const browser = await chromium.launch({ headless: true });
  const done = [];
  try {
    for (const viewport of [{ width: 1280, height: 900 }, { width: 393, height: 760 }]) {
      const ctx = await browser.newContext({ viewport, acceptDownloads: true });
      const page = await ctx.newPage();
      const errors = [];
      page.on('pageerror', e => errors.push('pageerror: ' + e.message));
      page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource|ERR_|tiles|basemaps|openfreemap|amazonaws|maplibre|AJAXError|NetworkError|Failed to fetch/i.test(m.text())) errors.push('console: ' + m.text()); });
      // the map's providers are out of reach here: answered empty, the map still mounts
      await page.route(/openfreemap|cartocdn|amazonaws/, r => r.fulfill({ status: 404, body: '' }));
      const recalcs = [];
      page.on('request', r => { if (r.url().includes('/partials/simulator/passage-times')) recalcs.push(r.postData() || ''); });
      await page.goto(base + '/simulator/routes/1', { waitUntil: 'networkidle' });
      await page.waitForFunction(() => window._sim && window._sim.setTarget && document.querySelector('#plan-table'));

      // no executable inline script and no inline handler left in the plan's partial
      const inline = await page.evaluate(() => {
        const root = document.getElementById('simulator-root');
        const scripts = [...root.querySelectorAll('script')].filter(s => !s.src && s.type !== 'application/json');
        const handlers = [...root.querySelectorAll('*')].flatMap(el => [...el.attributes].filter(a => /^on|^hx-on/.test(a.name)).map(a => el.tagName + '[' + a.name + ']'));
        const ext = [...document.querySelectorAll('script[src*="gpx-result.js"]')].map(s => s.src);
        return { scripts: scripts.length, handlers, ext };
      });
      assert.equal(inline.scripts, 0); assert.deepEqual(inline.handlers, []); assert.equal(inline.ext.length, 1);
      assert.match(inline.ext[0], /\/static\/js\/gpx-result\.js\?v=\w+/);
      const js = await page.request.get(inline.ext[0]);
      assert.equal(js.status(), 200); assert.match(js.headers()['cache-control'], /max-age=31536000, immutable/);

      // the objective: the number opens the editor, a typed time recalculates and saves, OK closes on the number
      await page.locator('#hero-time').click();
      const minutes = String(10 + Math.floor(Math.random() * 40));  // the preview keeps the last run's objective
      await page.locator('#target-h').fill('6'); await page.locator('#target-m').fill(minutes);
      const saved = page.waitForResponse(r => r.url().endsWith('/api/simulator/routes') && r.request().method() === 'POST');
      await page.locator('.pf-objok').click();
      assert.equal((await saved).status(), 200);
      assert.equal(await page.locator('#hero-num').textContent(), '6h' + minutes);
      assert.equal(await page.evaluate(() => document.activeElement.id), 'hero-time');
      // the slider: a move previews, a release commits
      await page.locator('#hero-time').click();
      await page.locator('#obj-slider').evaluate(el => { el.value = 60; el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true })); });
      assert.notEqual(await page.locator('#hero-num').textContent(), '6h' + minutes);
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('#hero-edit').isHidden(), true);
      await page.locator('#hero-time').click();
      await page.locator('#obj-reset button').click();  // « Revenir à l'estimation »
      await page.waitForFunction(() => document.getElementById('hero-label').textContent === 'Estimation');
      await page.keyboard.press('Escape');

      // the export sheet: opens with its focus inside, Tab stays in it, × closes back to the opener
      const exportBtn = page.locator('[data-primary-export]');
      await exportBtn.click();
      assert.equal(await page.locator('#export-sheet').isVisible(), true);
      assert.ok(await page.evaluate(() => document.getElementById('export-sheet').contains(document.activeElement)));
      for (let i = 0; i < 8; i++) await page.keyboard.press('Tab');
      assert.ok(await page.evaluate(() => document.getElementById('export-sheet').contains(document.activeElement)));
      const download = page.waitForEvent('download');
      await page.locator('#export-sheet .pf-tool').first().click();  // GPX horodaté: saved, then downloaded
      assert.match((await download).url(), /\/api\/simulator\/routes\/1\/pace-export\?format=gpx$/);  // (the preview's export quota may answer 429)
      assert.equal(await page.locator("#export-sheet").isVisible(), true);  // the sheet stays open after a download
      await page.locator('#export-sheet [aria-label="Fermer"]').click();
      assert.equal(await page.locator('#export-sheet').isHidden(), true);
      assert.equal(await page.evaluate(() => document.activeElement.hasAttribute('data-primary-export')), true);

      // a passage row opens in place, its « Modifier ce point » opens the point sheet, which edits and closes
      const head = page.locator('#plan-table [data-row] .pf-prow-head').first();
      await head.click();
      assert.equal(await head.getAttribute('aria-expanded'), 'true');
      const row = page.locator('#plan-table [data-row].is-open');
      await row.locator('[data-sim="openSheet"]').click();
      assert.equal(await page.locator('#cp-sheet').isVisible(), true);
      const before = recalcs.length;
      await page.locator('#cp-sheet [data-k="water"]').click();
      await page.waitForFunction(n => document.querySelectorAll('#cp-sheet [data-k="water"][aria-pressed="true"]').length === 1, before);
      const name = page.locator('#cp-sheet [data-cp-name]');
      await name.fill('Ravito test'); await name.press('Tab');
      await page.waitForFunction(() => [...document.querySelectorAll('#plan-table [data-name-text]')].some(e => e.textContent.trim() === 'Ravito test'));
      await page.waitForTimeout(300);
      assert.ok(recalcs.length > before);
      await page.locator('#cp-sheet [data-sim="closeSheet"]').last().click();
      assert.equal(await page.locator('#cp-sheet').isHidden(), true);
      // the add-point sheet and its own ×
      await page.locator('#add-pt, [data-empty-state] [data-sim="openAddSheet"]').first().click();
      assert.equal(await page.locator('#cp-sheet [data-add-name]').isVisible(), true);
      await page.locator('#cp-sheet [data-sheet-panel] [data-sim="closeSheet"]').click();
      assert.equal(await page.locator('#cp-sheet').isHidden(), true);

      // the map: the chip loads MapLibre from /static/vendor on demand, the overlay closes with ×
      await page.locator('[data-sim="showMap"]').click();
      await page.waitForFunction(() => !!window.maplibregl, null, { timeout: 15000 });
      assert.equal(await page.locator('#map-wrap').isVisible(), true);
      await page.waitForTimeout(500);
      await page.locator('#map-wrap [data-sim="closeMap"].pf-mapov-x').click();
      assert.equal(await page.locator('#map-wrap').isHidden(), true);
      // « Voir sur la carte » from an open row
      await head.click(); await head.click();
      const maplink = page.locator('#plan-table [data-row].is-open [data-sim="flyToCp"]');
      if (await maplink.count()) { await maplink.click(); assert.equal(await page.locator('#map-wrap').isVisible(), true); await page.keyboard.press('Escape'); }
      assert.equal(await page.locator('#map-wrap').isHidden(), true);

      // « Remplacer la trace GPX »: a picked file posts the form through htmx (answered here, the preview's trace kept)
      await page.route('**/api/simulator/routes/1/reimport', r => r.fulfill({ status: 200, contentType: 'text/html', body: '' }));
      const reimport = page.waitForRequest(r => r.url().endsWith('/reimport') && r.method() === 'POST');
      await page.locator('#plan-more input[type=file]').setInputFiles({ name: 't.gpx', mimeType: 'application/gpx+xml', buffer: Buffer.from('<gpx></gpx>') });
      assert.match((await reimport).headers()['content-type'], /multipart\/form-data/);
      await page.unroute('**/api/simulator/routes/1/reimport');
      await page.evaluate(() => document.querySelectorAll('#simulator-root details[open]').forEach(d => d.removeAttribute('open')));
      // the « … » menu: Escape from one of its items gives the focus back to its button, not to the page
      await page.locator('#plan-more summary').focus();
      await page.keyboard.press('Enter');
      assert.equal(await page.locator('#plan-more').getAttribute('open'), '');
      await page.locator('#plan-more .pf-menu button').first().focus();
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('#plan-more').getAttribute('open'), null);
      assert.equal(await page.evaluate(() => document.activeElement === document.querySelector('#plan-more summary')), true);

      // the plan's settings: the scenario form posts through htmx and the page recalculates once it answers
      await page.locator('#plan-more summary').click();
      await page.locator('#plan-more [data-sim="openTool"]').click();
      assert.equal(await page.locator('#settings-sheet').isVisible(), true);
      const n0 = recalcs.length;
      const posted = page.waitForResponse(r => r.url().endsWith('/scenarios'));
      await page.locator('input[name="scenario_fast_pct"]').fill('7');
      await page.locator('input[name="scenario_fast_pct"]').dispatchEvent('change');
      assert.equal((await posted).status(), 204);
      await page.waitForTimeout(400);
      assert.ok(recalcs.length > n0, 'a recalculation after the scenario form');
      // the stop field: recalculates and saves
      const n1 = recalcs.length;
      const stopSave = page.waitForResponse(r => r.url().endsWith('/api/simulator/routes') && r.request().method() === 'POST');
      await page.locator('#stop-min').fill('4'); await page.locator('#stop-min').dispatchEvent('change');
      await stopSave; assert.ok(recalcs.length > n1);
      await page.locator('#settings-sheet [data-sim="closeSettings"]').last().click();
      assert.equal(await page.locator('#settings-sheet').isHidden(), true);
      // the race sheet: from the menu, saved
      await page.locator('#plan-more summary').click();
      await page.locator('#plan-more [data-sim="openRaceSheet"]').click();
      await page.locator('#save-name').fill('Course renommée');
      await page.locator('#race-sheet [data-sim="saveRaceSheet"]').click();
      assert.equal(await page.locator('#race-name').textContent(), 'Course renommée');
      // a failed save shows its retry, which saves again
      await page.route('**/api/simulator/routes', r => r.request().method() === 'POST' ? r.fulfill({ status: 503, body: '{}' }) : r.continue());
      await page.evaluate(() => window._sim.saveRoute().catch(() => {}));
      await page.waitForSelector('#save-status [data-sim="retrySave"]');
      await page.unroute('**/api/simulator/routes');
      const retried = page.waitForResponse(r => r.url().endsWith('/api/simulator/routes') && r.request().method() === 'POST');
      await page.locator('#save-status [data-sim="retrySave"]').click();
      assert.equal((await retried).status(), 200);

      // the same partial swapped in by htmx: set up once, the old listeners gone (one recalculation per change)
      await page.evaluate(() => new Promise(resolve => {
        document.body.addEventListener('htmx:afterSettle', () => setTimeout(resolve, 300), { once: true });
        window.htmx.ajax('GET', '/api/simulator/routes/1', { target: '#rpanel-plan', swap: 'innerHTML' });
      }));
      await page.waitForFunction(() => window._sim && window._sim.setTarget && document.getElementById('simulator-root').pfPlanReady);
      await page.waitForTimeout(500);
      const n2 = recalcs.length;
      await page.evaluate(() => window._sim.setTarget(7, 5));
      await page.waitForTimeout(800);
      assert.equal(recalcs.length - n2, 1, 'one recalculation, not one per plan set up');
      await page.locator('#hero-time').click();
      assert.equal(await page.locator('#hero-edit').isVisible(), true);
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('#hero-edit').isHidden(), true);

      assert.deepEqual(errors, []);
      done.push(`${viewport.width}px`);
      await ctx.close();
    }
    console.log('plan smoke passed at ' + done.join(', '));
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
