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

    // A native-like cancel followed by a full leave: nothing is saved in place any more (the feel left on
    // 2026-10-31), so the leave barrier the installed apps call resolves at once; the cancelled tab never mounts.
    const leaving = await browser.newPage({ viewport: { width: 393, height: 700 } });
    await leaving.addInitScript(() => { window.ReactNativeWebView = { postMessage() {} }; });
    await leaving.goto(base + '/sante', { waitUntil: 'networkidle' });
    let fullLeaves = 0, releaseTab, tabRequested;
    const tabHeld = new Promise(resolve => { releaseTab = resolve; });
    const tabStarted = new Promise(resolve => { tabRequested = resolve; });
    await leaving.route(base + '/activities', async route => {
      const response = await route.fetch();
      tabRequested(); await tabHeld;
      try { await route.fulfill({ response }); } catch (_) { /* The GET was aborted. */ }
    });
    leaving.on('request', request => {
      if (new URL(request.url()).pathname === '/simulator' && request.isNavigationRequest()) fullLeaves++;
    });
    assert.equal(await leaving.evaluate(() => typeof window.paceforgeTabs.beforeLeave), 'function', 'Installed apps need the leave barrier');
    await leaving.evaluate(() => { window.cancelledTab = window.paceforgeTabs.navigate('/activities'); });
    await tabStarted;
    assert.equal(await leaving.locator('#main-content').evaluate(main => main.hidden), true);
    await leaving.evaluate(() => {
      window.paceforgeTabs.cancel();
      window.beforeLeaveSettled = false;
      window.fullLeave = window.paceforgeTabs.beforeLeave().then(() => { window.beforeLeaveSettled = true; });
    });
    await leaving.waitForFunction(() => window.beforeLeaveSettled === true, null, { timeout: 2000 });
    assert.equal(await leaving.locator('#main-content').evaluate(main => main.hidden), false);
    assert.equal(await leaving.locator('.pf-refresh-status.is-loading').count(), 0);
    releaseTab();
    await leaving.evaluate(() => window.cancelledTab);
    await leaving.waitForTimeout(100);
    assert.equal(new URL(leaving.url()).pathname, '/sante', 'Cancelled tab mounted after the leave barrier');
    assert.equal(fullLeaves, 0);
    await leaving.evaluate(() => { location.href = '/simulator'; });
    await leaving.waitForURL(base + '/simulator');
    await leaving.waitForLoadState('load');
    assert.equal(fullLeaves, 1);
    await leaving.close();
    console.log('Tab cancellation: late GET ignored, original pane restored, subsequent navigation works; the leave barrier resolves at once and a cancelled pane stays put.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
