/* Synthetic loading profile, never physical-iPhone FPS or production-user timings.
   Start tests.browser.loading_preview on localhost:8891, then run with Playwright.
   PF_PREVIEW_OUTPUT optionally saves JSON and the initial 393px screenshots. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const base = process.env.PF_PREVIEW_URL || 'http://127.0.0.1:8891';
const output = process.env.PF_PREVIEW_OUTPUT;
assert.ok(['localhost', '127.0.0.1'].includes(new URL(base).hostname), 'Use a local synthetic fixture');
const frame = page => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
(async () => {
  const fixture = await (await fetch(base + '/__preview/performance')).json();
  assert.equal(fixture.synthetic, true);
  const browser = await chromium.launch({ headless: true });
  const measurements = [];
  try {
    for (const stressed of [false, true]) {
      const context = await browser.newContext({ viewport: { width: 393, height: 700 }, isMobile: true, hasTouch: true });
      await context.addInitScript(() => {
        window.ReactNativeWebView = { postMessage() {} };
        window.paceforgeNativeRefresh = 2;
        window.profileLongTasks = [];
        new PerformanceObserver(list => list.getEntries().forEach(entry => profileLongTasks.push(entry.duration)))
          .observe({ type: 'longtask', buffered: true });
      });
      const page = await context.newPage();
      if (stressed) {
        const protocol = await context.newCDPSession(page);
        await protocol.send('Network.enable');
        await protocol.send('Network.emulateNetworkConditions', {
          offline: false, latency: 150, downloadThroughput: 200000, uploadThroughput: 100000,
        });
        await protocol.send('Emulation.setCPUThrottlingRate', { rate: 4 });
      }
      await page.goto(base + '/sante', { waitUntil: 'load' });
      await frame(page);
      const initial = await page.evaluate(() => {
        const navigation = performance.getEntriesByType('navigation')[0];
        return { ttfb_ms: navigation.responseStart, dom_content_loaded_ms: navigation.domContentLoadedEventEnd,
          fcp_ms: performance.getEntriesByName('first-contentful-paint')[0]?.startTime,
          html_encoded_bytes: navigation.encodedBodySize, html_decoded_bytes: navigation.decodedBodySize,
          dom_nodes: document.querySelectorAll('*').length, figures: document.querySelectorAll('figure').length,
          long_tasks_ms: profileLongTasks.slice() };
      });
      if (output && !stressed) {
        fs.mkdirSync(output, { recursive: true });
        await page.screenshot({ path: output + '/loading-health-393.png' });
      }
      const visits = [];
      for (const [label, path] of [['activities-first', '/activities'], ['health-return', '/sante'],
        ['activities-return', '/activities'], ['health-return-2', '/sante'], ['activities-return-2', '/activities']]) {
        let documents = 0;
        const requested = request => { if (request.isNavigationRequest()) documents++; };
        page.on('request', requested);
        await page.evaluate(() => { window.profileLongTasks = []; });
        const start = Date.now();
        const soft = await page.evaluate(() => !!window.paceforgeTabs);
        if (soft) await page.evaluate(url => window.paceforgeTabs.navigate(url), base + path);
        else await page.goto(base + path, { waitUntil: 'load' });
        await frame(page);
        visits.push({ label, visible_ms: Date.now() - start, full_document_requests: documents,
          long_tasks_ms: await page.evaluate(() => window.profileLongTasks.slice()) });
        page.off('request', requested);
        if (output && !stressed && label === 'activities-first') await page.screenshot({ path: output + '/loading-activities-393.png' });
        await page.waitForTimeout(350);
      }
      measurements.push({ mode: stressed ? 'artificial-stress-150ms-1.6Mbps-cpu4x' : 'local-unthrottled', initial, visits });
      await context.close();
    }
    const result = { fixture, conditions: 'Desktop Chromium mobile viewport, artificial stress; not physical iPhone. Pause other CPU-heavy checks.', measurements };
    if (output) fs.writeFileSync(output + '/loading-profile.json', JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result, null, 2));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
