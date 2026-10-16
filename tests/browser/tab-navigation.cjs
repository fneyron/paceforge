/* Local synthetic preview only. Tests the real soft navigation scripts in Chromium;
   it does not measure a physical iPhone or store user data. */
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const fs = require('node:fs');
const base = process.env.PF_PREVIEW_URL || 'http://127.0.0.1:8891';
const output = process.env.PF_PREVIEW_OUTPUT || '/tmp';
assert.ok(['127.0.0.1', 'localhost'].includes(new URL(base).hostname), 'Use a local synthetic fixture');

function barrier() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}
async function appPage(browser) {
  const page = await browser.newPage({ viewport: { width: 393, height: 700 }, isMobile: true, hasTouch: true });
  await page.addInitScript(() => {
    window.nativeMessages = [];
    window.ReactNativeWebView = { postMessage: value => nativeMessages.push(JSON.parse(value)) };
    window.paceforgeNativeRefresh = 2;
    window.originalDocument = document;
  });
  await page.goto(base + '/sante', { waitUntil: 'networkidle' });
  await page.waitForFunction(() => window.paceforgeTabs && window.paceforgeRefresh && window.PFViz);
  return page;
}
async function go(page, path) {
  assert.equal(await page.evaluate(url => window.paceforgeTabs.navigate(url), base + path), true);
  await page.waitForURL(base + path);
}
async function sameDocument(page) {
  assert.equal(await page.evaluate(() => document === window.originalDocument), true, 'Tab change reloaded the document');
  assert.equal(await page.locator('#main-content').count(), 1);
}

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const results = {};
  try {
    const page = await appPage(browser);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let anchorGets = 0;
    page.on('request', request => {
      if (request.method() === 'GET' && new URL(request.url()).pathname === '/sante') anchorGets++;
    });
    await page.locator('.pf-ring-sommeil').click();
    await page.waitForTimeout(150);
    assert.equal(anchorGets, 0, 'A sleep ring anchor requested the whole health page');
    assert.ok(await page.locator('#sommeil-detail').isVisible());
    assert.equal(await page.locator('#sommeil-detail').evaluate(el => {
      for (let node = el; node; node = node.parentElement) if (node.tagName === 'DETAILS' && !node.open) return false;
      return true;
    }), true, 'Sleep ring did not reveal its target');
    await sameDocument(page);
    await page.evaluate(() => { history.replaceState(null, '', '/sante'); scrollTo({ top: 0, behavior: 'instant' }); });
    const originalAccount = await page.locator('#main-content').getAttribute('data-account');
    if (await page.locator('.pf-checkin-form button[value="clear"]').count()) {
      await page.locator('#ressenti').evaluate(el => { el.open = true; });
      await page.locator('.pf-checkin-form button[value="clear"]').click();
      await page.waitForFunction(() => document.querySelector('.pf-checkin-summary')?.textContent === 'Facultatif');
    }
    // One existing tab remains immediately readable while the next one is requested.
    await page.evaluate(() => {
      document.querySelector('#main-content').dataset.cachedMarker = 'health-visited';
      document.querySelector('#sommeil-habitudes').open = true;
      document.querySelector('#ressenti').open = true;
      document.querySelector('.pf-checkin-form input[name="pain"][value="present"]').checked = true;
      scrollTo({ top: 650, behavior: 'instant' });
    });
    await page.waitForTimeout(100);
    const savedY = await page.evaluate(() => scrollY);
    const coldStarted = Date.now();
    await go(page, '/activities');
    results.cold_activity_visible_ms = Date.now() - coldStarted;
    await sameDocument(page);
    const firstRows = await page.locator('[id^="activity-row-"]').count();
    assert.ok(firstRows > 0);
    await page.getByRole('button', { name: /Semaines précédentes/ }).click();
    await page.waitForFunction(n => document.querySelectorAll('[id^="activity-row-"]').length > n, firstRows);
    const pagedRows = await page.locator('[id^="activity-row-"]').count();
    await page.locator('#semaines button[data-range="distance"]').click();
    const rememberedActivities = new URL(page.url()).pathname + new URL(page.url()).search;
    assert.match(rememberedActivities, /m=distance/);
    await page.locator('#main-content').evaluate(el => { el.dataset.measureMarker = 'distance-visited'; });

    const waiting = barrier(), release = barrier();
    await page.route('**/sante', async route => {
      const response = await route.fetch();
      const body = (await response.text()).replace('id="main-content"', 'id="main-content" data-server-seq="2"');
      waiting.resolve(); await release.promise;
      await route.fulfill({ response, body });
    });
    const cacheStarted = Date.now();
    await go(page, '/sante');
    results.visited_health_visible_ms = Date.now() - cacheStarted;
    await waiting.promise;
    assert.equal(await page.locator('#main-content').getAttribute('data-cached-marker'), 'health-visited', 'Visited page waited for the delayed GET');
    assert.equal(await page.locator('#sommeil-habitudes').evaluate(el => el.open), true);
    assert.ok(await page.locator('.pf-checkin-form input[name="pain"][value="present"]').isChecked(), 'Draft check-in disappeared on tab return');
    assert.ok(Math.abs((await page.evaluate(() => scrollY)) - savedY) < 4, 'Tab return lost the scroll position');
    await page.locator('.pf-checkin-form button[value="save"]').click();
    await page.waitForFunction(() => document.querySelector('.pf-checkin-summary')?.textContent === 'Douleur signalée');
    release.resolve();
    await page.waitForFunction(() => document.querySelector('#main-content')?.dataset.serverSeq === '2');
    assert.ok(await page.locator('.pf-checkin-form input[name="pain"][value="present"]').isChecked(), 'Background revalidation replaced the saved answer');
    assert.match(await page.locator('.pf-checkin-summary').innerText(), /Douleur signalée/, 'Late GET replaced a completed save');
    await page.unroute('**/sante');
    await go(page, rememberedActivities);
    assert.equal(await page.locator('#main-content').getAttribute('data-measure-marker'), 'distance-visited', 'Measure toggle lost the cached URL');
    assert.ok(await page.locator('#semaines [data-range-panel="distance"]').isVisible());
    assert.equal(await page.locator('[id^="activity-row-"]').count(), pagedRows, 'Cached return lost older weeks');
    await page.waitForTimeout(350);
    assert.equal(await page.locator('[id^="activity-row-"]').count(), pagedRows, 'Revalidation lost older weeks');
    await sameDocument(page);

    const clearWaiting = barrier(), releaseClearGet = barrier();
    await page.route('**/sante', async route => {
      const response = await route.fetch(); clearWaiting.resolve(); await releaseClearGet.promise;
      await route.fulfill({ response, body: (await response.text()).replace('id="main-content"', 'id="main-content" data-clear-revalidated="yes"') });
    });
    await go(page, '/sante');
    await clearWaiting.promise;
    await page.locator('#ressenti').evaluate(el => { el.open = true; });
    await page.locator('.pf-checkin-form button[value="clear"]').click();
    await page.waitForFunction(() => document.querySelector('.pf-checkin-summary')?.textContent === 'Facultatif');
    releaseClearGet.resolve();
    await page.waitForFunction(() => document.querySelector('#main-content')?.dataset.clearRevalidated === 'yes');
    assert.equal(await page.locator('.pf-checkin-summary').innerText(), 'Facultatif', 'Late GET replaced a completed clear');
    assert.equal(await page.locator('.pf-checkin-form input:checked').count(), 0);
    await page.unroute('**/sante');
    await go(page, rememberedActivities);
    await sameDocument(page);

    // A filter's URL, visible records and selected chart measure agree after soft navigation.
    await page.locator('#semaines button[data-range="distance"]').click();
    const bike = page.getByRole('navigation', { name: 'Filtrer les activités par sport' }).getByRole('link', { name: 'Vélo', exact: true });
    await bike.click();
    await page.waitForURL('**/activities?**sport=bike**');
    await page.waitForFunction(() => !!document.querySelector('#semaines [data-range-panel="distance"]:not([hidden])'));
    await sameDocument(page);
    const bikeRows = await page.locator('.pf-activity-row').count();
    assert.ok(bikeRows > 0);
    assert.equal(await page.locator('.pf-activity-row').filter({ hasText: 'Renforcement et mobilité' }).count(), 0);
    assert.match(page.url(), /m=distance/);

    // Leaving during an old GET cannot put the old screen back when it finally arrives.
    const staleWaiting = barrier(), staleRelease = barrier();
    await page.route('**/sante', async route => {
      const response = await route.fetch();
      const body = (await response.text()).replace('id="main-content"', 'id="main-content" data-stale-response="yes"');
      staleWaiting.resolve(); await staleRelease.promise;
      await route.fulfill({ response, body }).catch(() => {});
    });
    await go(page, '/sante');
    await staleWaiting.promise;
    await go(page, '/activities?sport=bike&m=distance');
    staleRelease.resolve();
    await page.waitForTimeout(450);
    assert.equal(new URL(page.url()).pathname, '/activities');
    assert.equal(await page.locator('#main-content[data-stale-response]').count(), 0);
    await page.unroute('**/sante');

    // A refresh invalidates another tab's saved measurements: its next visit needs a fresh GET.
    await page.evaluate(() => window.paceforgeRefresh.refresh(false));
    const invalidWaiting = barrier(), invalidRelease = barrier();
    await page.route('**/sante', async route => {
      const response = await route.fetch(); invalidWaiting.resolve(); await invalidRelease.promise;
      await route.fulfill({ response });
    });
    await page.evaluate(() => { window.invalidVisit = window.paceforgeTabs.navigate('/sante'); });
    await invalidWaiting.promise;
    assert.equal(new URL(page.url()).pathname, '/activities', 'Invalidated health snapshot was shown as current');
    invalidRelease.resolve();
    assert.equal(await page.evaluate(() => window.invalidVisit), true);
    await page.waitForURL(base + '/sante');
    await page.unroute('**/sante');
    await sameDocument(page);

    assert.equal(await page.evaluate(() => window.paceforgeTabs.navigate('/simulator')), false);
    assert.equal(await page.evaluate(() => window.paceforgeTabs.navigate('https://example.com/sante')), false);
    assert.equal(await page.locator('#main-content').getAttribute('data-account'), originalAccount);
    assert.deepEqual(errors, []);
    await page.screenshot({ path: output + '/tab-navigation-health-393.png' });
    await page.close();

    // A provider sync finishing after a tab change invalidates snapshots made mid-sync.
    const syncPage = await appPage(browser);
    const syncPending = barrier(), releaseSync = barrier();
    await syncPage.route('**/sante/sync', route => route.fulfill({ json: { watches: [], errors: [], since: new Date().toISOString() } }));
    await syncPage.route('**/api/sync', async route => {
      syncPending.resolve(); await releaseSync.promise;
      await route.fulfill({ json: { received: 1, errors: [] } });
    });
    await syncPage.evaluate(() => { window.longSync = window.paceforgeRefresh.refresh(true); });
    await syncPending.promise;
    await go(syncPage, '/activities');
    releaseSync.resolve();
    await syncPage.evaluate(() => window.longSync);
    const freshPending = barrier(), releaseFresh = barrier();
    await syncPage.route('**/sante', async route => {
      const response = await route.fetch(); freshPending.resolve(); await releaseFresh.promise;
      await route.fulfill({ response });
    });
    await syncPage.evaluate(() => { window.afterSyncVisit = window.paceforgeTabs.navigate('/sante'); });
    await freshPending.promise;
    assert.equal(new URL(syncPage.url()).pathname, '/activities', 'Snapshot created during provider sync survived its completion');
    releaseFresh.resolve();
    assert.equal(await syncPage.evaluate(() => window.afterSyncVisit), true);
    await syncPage.waitForURL(base + '/sante');
    await sameDocument(syncPage);
    await syncPage.close();

    // A started HTMX write must finish before its live target is detached.
    const writingPage = await appPage(browser);
    const posted = barrier(), releasePost = barrier();
    await writingPage.route('**/sante/ressenti', async route => {
      const response = await route.fetch(); posted.resolve(); await releasePost.promise;
      await route.fulfill({ response });
    });
    await writingPage.locator('#ressenti').evaluate(el => { el.open = true; });
    await writingPage.locator('.pf-checkin-form input[name="pain"][value="present"]').check();
    await writingPage.locator('.pf-checkin-form button[value="save"]').click();
    await posted.promise;
    await writingPage.evaluate(() => { window.leaveDuringWrite = window.paceforgeTabs.navigate('/activities'); });
    await writingPage.waitForTimeout(200);
    assert.equal(new URL(writingPage.url()).pathname, '/sante', 'Tab detached an in-flight check-in write');
    releasePost.resolve();
    assert.equal(await writingPage.evaluate(() => window.leaveDuringWrite), true);
    await writingPage.waitForURL(base + '/activities');
    await go(writingPage, '/sante');
    assert.match(await writingPage.locator('#ressenti .pf-checkin-summary').innerText(), /Douleur signalée/);
    assert.ok(await writingPage.locator('.pf-checkin-form input[name="pain"][value="present"]').isChecked(), 'Completed save disappeared on tab return');
    await sameDocument(writingPage);
    await writingPage.close();

    // A changed account must leave the memory snapshots before the full reload.
    const accountPage = await appPage(browser);
    await go(accountPage, '/activities');
    await accountPage.evaluate(() => { window.testDocumentMarker = 'old'; });
    let requests = 0;
    await accountPage.route('**/sante', async route => {
      requests++;
      const response = await route.fetch();
      if (route.request().isNavigationRequest()) return route.fulfill({ response });
      return route.fulfill({ response, body: (await response.text()).replace(/data-account="\d+"/, 'data-account="999"') });
    });
    await accountPage.evaluate(() => { window.paceforgeTabs.navigate('/sante'); });
    await accountPage.waitForFunction(() => !window.testDocumentMarker, null, { timeout: 10000 });
    // The reloaded preview returns the original account, never the foreign fetched fragment.
    await accountPage.waitForLoadState('load');
    assert.equal(await accountPage.locator('#main-content[data-account="999"]').count(), 0);
    assert.ok(requests >= 2, 'Account mismatch did not fall back to a fresh document');
    await accountPage.close();

    // A deploy cannot mix newly fetched HTML with the old document's JS/CSS.
    for (const mode of ['tab', 'refresh']) {
      const buildPage = await appPage(browser);
      assert.ok(await buildPage.locator('#main-content').getAttribute('data-build'));
      let foreignBuildMounted = 0, fullLoads = 0;
      await buildPage.exposeFunction('noteForeignBuildMount', () => { foreignBuildMounted++; });
      await buildPage.evaluate(() => {
        window.testBuildDocumentMarker = 'old';
        new MutationObserver(records => {
          for (const record of records) for (const node of record.addedNodes) {
            if (node.nodeType === 1 && (node.matches('[data-test-future-build]') || node.querySelector('[data-test-future-build]'))) {
              window.noteForeignBuildMount();
            }
          }
        }).observe(document.body, { childList: true, subtree: true });
      });
      const target = mode === 'tab' ? '/activities' : '/sante';
      await buildPage.route(base + target, async route => {
        const response = await route.fetch();
        if (route.request().isNavigationRequest()) {
          fullLoads++;
          return route.fulfill({ response });
        }
        const body = (await response.text()).replace(/data-build="[^"]*"/, 'data-build="future-build" data-test-future-build="yes"');
        return route.fulfill({ response, body });
      });
      await buildPage.evaluate(({ mode, target }) => {
        if (mode === 'tab') window.paceforgeTabs.navigate(target);
        else window.paceforgeRefresh.refresh(false);
      }, { mode, target });
      await buildPage.waitForFunction(() => !window.testBuildDocumentMarker, null, { timeout: 10000 });
      await buildPage.waitForLoadState('load');
      assert.equal(fullLoads, 1, mode + ': changed build did not cause a full document load');
      assert.equal(foreignBuildMounted, 0, mode + ': new-build fragment was mounted in the old shell');
      assert.equal(await buildPage.locator('[data-test-future-build]').count(), 0);
      await buildPage.close();
    }

    // Session expiry similarly falls back to the normal full-page authentication flow.
    const expiredPage = await appPage(browser);
    await expiredPage.route('**/activities', async route => {
      if (route.request().isNavigationRequest()) return route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><title>Connexion</title><main id="session-fallback">Reconnecte-toi</main>' });
      return route.fulfill({ status: 401, contentType: 'text/html', body: '<main id="foreign-fragment">Ne pas afficher</main>' });
    });
    await expiredPage.evaluate(() => { window.paceforgeTabs.navigate('/activities'); });
    await expiredPage.locator('#session-fallback').waitFor();
    assert.equal(await expiredPage.locator('#foreign-fragment').count(), 0);
    await expiredPage.close();
    fs.writeFileSync(output + '/tab-navigation-verification.json', JSON.stringify({ synthetic: true,
      browser: 'Chromium desktop mobile viewport; not physical iPhone', results,
      checked: ['same document', 'ring anchors without GET', 'visited content before delayed GET', 'later revalidation', 'scroll and draft retention',
        'pagination', 'measure toggle URL retention', 'pending HTMX write before tab change', 'save and clear during cached revalidation', 'sport filter and measure', 'stale GET ignored', 'refresh invalidation', 'provider completion after tab change invalidation', 'account fallback', 'build change full reload for tab and refresh', 'expired session fallback', 'Courses full navigation'] }, null, 2));
    console.log('Tab navigation: cached return, revalidation, pagination, filters, stale requests, account and session isolation passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
