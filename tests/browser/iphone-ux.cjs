// Start: python -m uvicorn tests.browser.preview_app:app --host 127.0.0.1 --port 8853
// Run with Playwright installed: node tests/browser/iphone-ux.cjs
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const base = process.env.PF_PREVIEW_URL || 'http://127.0.0.1:8853';
const out = process.env.PF_UX_SCREENSHOTS || '/tmp/paceforge-ux';
(async () => {
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 393, height: 680 }, isMobile: true, hasTouch: true });
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => { window.ReactNativeWebView = { postMessage() {} }; window.paceforgeNativeRefresh = 2; document.addEventListener('DOMContentLoaded', () => document.documentElement.classList.add('pf-offline'));  });
    await page.goto(base + '/simulator/routes/1', { waitUntil: 'networkidle' });
    await page.waitForFunction(() => window._sim?.setTarget);
    // Leaving immediately must wait for persistence, including a second edit during an in-flight save.
    let writes = 0;
    await page.route('**/api/simulator/routes', async route => {
      if (route.request().method() === 'POST') { writes++; await new Promise(r => setTimeout(r, 250)); }
      await route.continue();
    });
    await page.evaluate(() => {
      window._sim.setTarget(5, 31);
      void window._sim.saveRoute();
      window._sim.setTarget(5, 32);
      const link = document.createElement('a'); link.href = '/sante'; link.textContent = 'Quitter'; link.id = 'test-leave'; document.body.append(link);
    });
    await page.locator('#test-leave').click();
    await page.waitForURL('**/sante');
    assert.ok(writes >= 2);
    await page.goto(base + '/simulator/routes/1', { waitUntil: 'networkidle' });
    assert.equal(await page.locator('#target-m').inputValue(), '32');
    // Failed save keeps the plan editable and exposes a retry, with the edited value intact.
    await page.route('**/api/simulator/routes', route => route.fulfill({ status: 503, body: '{}' }));
    await page.evaluate(() => {
      window._sim.setTarget(5, 33);
      const link = document.createElement('a'); link.href = '/sante'; link.id = 'test-leave'; link.textContent = 'Quitter'; document.body.append(link);
    });
    await page.locator('#test-leave').click();
    await page.getByRole('button', { name: 'Non enregistré · réessayer' }).waitFor();
    assert.ok(page.url().includes('/simulator/routes/1'));
    assert.equal(await page.locator('#target-m').inputValue(), '33');
    await page.unroute('**/api/simulator/routes');
    await page.getByRole('button', { name: 'Non enregistré · réessayer' }).click();
    await page.waitForFunction(() => document.querySelector('#save-status').dataset.state === 'saved');
    await page.locator('#plan-more > summary').click();
    assert.equal(await page.getByRole('menuitem', { name: 'Garder hors connexion' }).isVisible(), true);
    await page.locator('#plan-more > summary').click();
    await page.getByRole('button', { name: 'Exporter pour ma montre', exact: true }).click();
    assert.equal(await page.getByRole('button', { name: /TCX course/ }).isVisible(), false);
    await page.getByText('Autres formats', { exact: true }).click();
    assert.equal(await page.getByRole('button', { name: /TCX course/ }).isVisible(), true);
    await page.evaluate(() => window._sim.closeExport());
    await page.goto(base + '/sante', { waitUntil: 'networkidle' });
    await page.locator('#charge-detail > summary').click();
    const selected = await page.locator('#vfc figure').evaluate(fig => {
      const data = JSON.parse(fig.querySelector('.pf-viz-data').textContent), input = fig.querySelector('.pf-viz-range');
      input.value = Math.max(0, data.d.length - 3); input.dispatchEvent(new Event('input'));
      return { key: fig.dataset.vizKey, date: data.d[Number(input.value)], aria: input.getAttribute('aria-valuetext') };
    });
    await page.evaluate(() => scrollTo({ top: 720, behavior: 'instant' }));
    await page.goto(base + '/activities', { waitUntil: 'networkidle' });
    const first = await page.locator('[id^="activity-row-"]').first().boundingBox();
    assert.ok(first.y < 500, 'first activity is visible before scrolling');
    // Simulate a session saved by the previous, collapsible Health dashboard.
    await page.evaluate(selected => {
      const key = 'pf-view:' + document.querySelector('#main-content').dataset.account + ':/sante';
      const saved = JSON.parse(sessionStorage.getItem(key));
      if (saved.charts[selected.key] !== selected.date) throw new Error('The dated chart selection was not remembered');
      for (const id of ['sommeil', 'sommeil-detail', 'recuperation', 'entrainement', 'forme', 'mesures-nocturnes', 'tendances']) saved.folds[id] = false;
      saved.folds['qualite-bilan'] = true;
      sessionStorage.setItem(key, JSON.stringify(saved));
    }, selected);
    await page.goto(base + '/sante?app_nav=return', { waitUntil: 'networkidle' });
    assert.equal(await page.locator('#charge-detail').evaluate(el => el.open), true, 'optional history still restores its state');
    assert.equal(await page.locator('#qualite-bilan').evaluate(el => el.open), false, 'method information must not reopen from an obsolete saved state');
    assert.equal(await page.locator('#vfc .pf-viz-range').getAttribute('aria-valuetext'), selected.aria, 'tab return restores the dated selection');
    for (const selector of ['#sommeil-detail [data-range-panel]:not([hidden]) figure', '#recuperation-detail figure', '#vfc figure', '#fc figure', '#entrainement-semaine figure', '#tendances figure']) {
      assert.ok(await page.locator(selector).first().isVisible(), `old fold state hid ${selector}`);
    }
    assert.ok(Math.abs(await page.evaluate(() => scrollY) - 720) < 20, 'tab return restores scroll');
    await page.goto(base + '/sante#qualite-bilan', { waitUntil: 'networkidle' });
    assert.equal(await page.locator('#qualite-bilan').evaluate(el => el.open), true, 'an explicit help link still opens the requested information');
    await page.evaluate(() => window.paceforgeRememberPage());
    assert.equal(await page.evaluate(() => {
      const key = 'pf-view:' + document.querySelector('#main-content').dataset.account + ':/sante';
      return 'qualite-bilan' in JSON.parse(sessionStorage.getItem(key)).folds;
    }), false, 'transient information is not persisted');
    // No horizontal page overflow at narrow widths or with 200% system text.
    for (const width of [320, 393]) for (const scale of [1, 2]) {
      await page.setViewportSize({ width, height: 680 });
      for (const path of ['/sante', '/activities', '/simulator', '/simulator/routes/1', '/settings']) {
        await page.goto(base + path, { waitUntil: 'networkidle' });
        await page.evaluate(scale => {
          document.documentElement.style.fontSize = 16 * scale + 'px';
          document.documentElement.classList.toggle('pf-large-text', scale >= 1.4);
          scrollTo({ top: 0, behavior: 'instant' });
        }, scale);
        // measured against the screen, not innerWidth: a page that overflows widens the layout viewport with it
        const fits = async state => { const sizes = await page.evaluate(() => ({ viewport: innerWidth, page: document.documentElement.scrollWidth })); assert.ok(sizes.page <= width + 1, `${path} ${width}px at ${scale * 100}%${state} overflows the screen: ${JSON.stringify(sizes)}`); };
        await fits('');
        if (width === 393) await page.screenshot({ path: `${out}/${path.replaceAll('/', '_')}-${scale}.png`, fullPage: true });
        if (path === '/simulator/routes/1') { await page.waitForFunction(() => window._sim?.heroEdit); await page.evaluate(() => window._sim.heroEdit()); await fits(' with the objective open'); }
      }
    }
    // The objective's own fields keep the size of the number on a coarse pointer (the 16 px rule for phone inputs spares them).
    // On a page of its own: after a full-page screenshot the page above no longer emulates touch, so (pointer: coarse) is false there.
    const phone = await browser.newPage({ viewport: { width: 393, height: 680 }, isMobile: true, hasTouch: true });
    await phone.goto(base + '/simulator/routes/1', { waitUntil: 'networkidle' });
    await phone.waitForFunction(() => window._sim?.heroEdit);
    await phone.evaluate(() => window._sim.heroEdit());
    const hero = await phone.evaluate(() => ({ coarse: matchMedia('(pointer: coarse)').matches, input: getComputedStyle(document.getElementById('target-h')).fontSize, number: getComputedStyle(document.getElementById('hero-num')).fontSize }));
    assert.equal(hero.coarse, true, 'the phone context emulates a coarse pointer');
    assert.equal(hero.input, hero.number, `the objective inputs are as big as the number: ${JSON.stringify(hero)}`);
    assert.ok(parseFloat(hero.number) >= 60, `the number stays a hero: ${hero.number}`);
    await phone.close();
    await page.goto(base + '/settings', { waitUntil: 'networkidle' });
    assert.equal(await page.locator('#strava').isVisible(), false);
    assert.equal(await page.getByRole('link', { name: 'Gérer mes connexions ›' }).isVisible(), true);
    assert.deepEqual(errors, []);
    await page.close();
    console.log('iPhone UX: save ordering/failure/retry, old fold migration, restored chart dates/history/scroll, explicit help links, exports, 320/393px at 100/200% text and the objective editor on a coarse pointer passed.');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
