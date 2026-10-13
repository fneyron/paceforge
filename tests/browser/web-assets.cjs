// Run against tests.browser.preview_app with Playwright installed.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = process.env.PF_PREVIEW_URL || 'http://127.0.0.1:8853';
function templates(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap(entry => {
    const file = path.join(dir, entry.name);
    return entry.isDirectory() ? templates(file) : entry.name.endsWith('.html') ? [fs.readFileSync(file, 'utf8')] : [];
  });
}
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 393, height: 852 }, isMobile: true, hasTouch: true });
    const page = await context.newPage();
    const errors = []; const externalScripts = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('request', r => {
      if (r.resourceType() === 'script' && new URL(r.url()).origin !== new URL(base).origin) externalScripts.push(r.url());
    });
    await page.addInitScript(() => { window.ReactNativeWebView = { postMessage() {} }; });
    for (const route of ['/sante', '/activities', '/simulator', '/settings', '/simulator/routes/1']) {
      await page.goto(base + route, { waitUntil: 'networkidle' });
      assert.equal(await page.evaluate(() => !!window.htmx && !!window.lucide), true, route);
      assert.equal(await page.evaluate(() => !!window.Alpine), false, `${route} needlessly loads Alpine`);
      assert.equal(await page.locator('i[data-lucide]').count(), 0, `${route} has an unresolved icon`);
    }
    // Every name present in a template must resolve in the reduced icon bundle.
    const icons = [...new Set(templates(path.join(__dirname, '../../app/templates')).flatMap(html => [...html.matchAll(/data-lucide="([a-z-]+)"/g)].map(m => m[1])))];
    const rendered = await page.evaluate(names => {
      const container = document.createElement('div');
      for (const name of names) { const icon = document.createElement('i'); icon.dataset.lucide = name; container.append(icon); }
      document.body.append(container); window.lucide.createIcons();
      const count = container.querySelectorAll('svg[data-lucide]').length; container.remove(); return count;
    }, icons);
    assert.equal(rendered, icons.length);
    await page.goto(base + '/activities', { waitUntil: 'networkidle' });
    const initialRows = await page.locator('[id^="activity-row-"]').count();
    await page.locator('#load-more button').click();
    await page.waitForFunction(count => document.querySelectorAll('[id^="activity-row-"]').length > count, initialRows);
    await page.waitForFunction(() => !document.querySelector('i[data-lucide]'));
    await page.goto(base + '/setup', { waitUntil: 'networkidle' });
    await page.getByRole('button', { name: /suivant/i }).click();
    await page.locator('[x-show="step === 2"]').waitFor({ state: 'visible' });
    await page.goto(base + '/activity/1', { waitUntil: 'networkidle' });
    assert.equal(await page.evaluate(() => !!window.Alpine), true);
    assert.deepEqual(externalScripts, []);
    assert.deepEqual(errors, []);
    console.log(`Web assets: no external scripts on main screens/plan/setup/activity; ${icons.length} icons resolved; HTMX pagination and Alpine setup passed.`);
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
