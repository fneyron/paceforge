// A cancelled first tab load must leave its original reading pane usable.
// Local synthetic fixture only; no account or physical iPhone performance claim.
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const base = process.env.PF_PREVIEW_URL || 'http://127.0.0.1:8892';
assert.ok(['localhost', '127.0.0.1'].includes(new URL(base).hostname));
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 393, height: 700 } });
    await page.addInitScript(() => { window.ReactNativeWebView = { postMessage() {} }; });
    await page.goto(base + '/sante', { waitUntil: 'networkidle' });
    let release, started;
    const held = new Promise(resolve => { release = resolve; });
    const requested = new Promise(resolve => { started = resolve; });
    await page.route(base + '/activities', async route => {
      const response = await route.fetch();
      started(); await held;
      try { await route.fulfill({ response }); } catch (_) { /* The GET was aborted. */ }
    });
    await page.evaluate(() => { window.pendingTab = window.paceforgeTabs.navigate('/activities'); });
    await requested;
    assert.equal(await page.locator('#main-content').evaluate(main => main.hidden), true);
    // the loading status says it in words (an aria-label on a status is not read)
    await page.waitForFunction(() => document.querySelector('.pf-refresh-status.is-loading [class="sr-only"]')?.textContent === 'Chargement…');
    assert.equal(await page.locator('.pf-refresh-status.is-loading').getAttribute('aria-label'), null);
    await page.evaluate(() => window.paceforgeTabs.cancel());
    assert.equal(await page.locator('#main-content').evaluate(main => main.hidden), false);
    assert.equal(await page.locator('.pf-refresh-status.is-loading').count(), 0);
    release();
    await page.evaluate(() => window.pendingTab);
    await page.waitForTimeout(100);
    assert.equal(new URL(page.url()).pathname, '/sante');
    assert.equal(await page.locator('.pf-sante4').count(), 1);
    await page.evaluate(() => window.paceforgeTabs.navigate('/activities'));
    assert.equal(new URL(page.url()).pathname, '/activities', 'A deliberate later navigation must still work');
    await page.close();

    // A native-like cancel followed by a full leave still waits for a real HTMX save.
    const saving = await browser.newPage({ viewport: { width: 393, height: 700 } });
    await saving.addInitScript(() => { window.ReactNativeWebView = { postMessage() {} }; });
    await saving.goto(base + '/sante', { waitUntil: 'networkidle' });
    let releasePost, posted, savedAtLeave = null, fullLeaves = 0, activityRequests = 0;
    const postHeld = new Promise(resolve => { releasePost = resolve; });
    const postStarted = new Promise(resolve => { posted = resolve; });
    await saving.route(base + '/sante/ressenti', async route => {
      const response = await route.fetch();
      posted(); await postHeld;
      await route.fulfill({ response });
    });
    saving.on('request', request => {
      const path = new URL(request.url()).pathname;
      if (path === '/activities') activityRequests++;
      if (path === '/simulator' && request.isNavigationRequest()) fullLeaves++;
    });
    await saving.exposeFunction('noteSavedBeforeLeave', summary => { savedAtLeave = summary; });
    await saving.locator('#ressenti').getByRole('button', { name: 'Cassé' }).click();
    await postStarted;
    await saving.evaluate(() => { window.cancelledTab = window.paceforgeTabs.navigate('/activities'); });
    assert.equal(await saving.locator('#main-content').evaluate(main => main.hidden), true);
    await saving.evaluate(() => {
      window.paceforgeTabs.cancel();
      window.beforeLeaveSettled = false;
      window.fullLeave = window.paceforgeTabs.beforeLeave().then(async () => {
        window.beforeLeaveSettled = true;
        await window.noteSavedBeforeLeave(document.querySelector('#ressenti [aria-pressed="true"]')?.textContent);
        location.href = '/simulator';
      });
    });
    await saving.waitForTimeout(200);
    assert.equal(await saving.evaluate(() => window.beforeLeaveSettled), false, 'Leave barrier resolved before the held save');
    assert.equal(await saving.locator('#main-content').evaluate(main => main.hidden), false);
    assert.equal(new URL(saving.url()).pathname, '/sante');
    assert.equal(fullLeaves, 0, 'Full navigation started while the save was held');
    releasePost();
    await saving.waitForURL(base + '/simulator');
    await saving.waitForLoadState('load');
    assert.match(savedAtLeave, /Cassé/, 'Leave barrier resolved before the saved HTMX target was updated');
    assert.equal(fullLeaves, 1);
    assert.equal(activityRequests, 0, 'Cancelled tab resumed after the save completed');
    await saving.close();
    console.log('Tab cancellation: late GET ignored, original pane restored, subsequent navigation works; held HTMX save completes before a cancelled pane can leave.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
