// Réglages › Accès API at the phone's width and the desktop's, light and dark: a key made, shown once (whole,
// copied or selectable), read by the API, listed by its first characters, revoked behind the confirmation, then
// refused by the API. No horizontal scroll at 393 px.
// Start: PYTHONPATH=. python -m uvicorn tests.browser.preview_app:app --host 127.0.0.1 --port 8971
// Run with Playwright installed: node tests/browser/api-settings.cjs
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const base = process.env.PF_PREVIEW_URL || 'http://127.0.0.1:8971';
const out = process.env.PF_UX_SCREENSHOTS || '/tmp/paceforge-ux/api';

async function api(page, token) {
  return page.request.get(base + '/api/v1/me', { headers: { Authorization: 'Bearer ' + token } });
}

(async () => {
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  try {
    for (const [label, viewport, mobile] of [['393', { width: 393, height: 852 }, true], ['1280', { width: 1280, height: 900 }, false]]) {
      for (const scheme of ['light', 'dark']) {
        const context = await browser.newContext({ viewport, isMobile: mobile, hasTouch: mobile, colorScheme: scheme });
        await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: base });
        const page = await context.newPage();
        const errors = []; page.on('pageerror', e => errors.push(e.message));
        await page.goto(base + '/settings#api', { waitUntil: 'networkidle' });
        assert.equal(await page.evaluate(() => document.documentElement.classList.contains('dark')), scheme === 'dark');
        const section = page.locator('#api');
        await section.scrollIntoViewIfNeeded();
        assert.match(await section.innerText(), /Accès API/);
        assert.equal(await section.locator('a[href="/developpeurs"]').count(), 1);
        // the form opens by itself while there is no key; the health box is there (the consent is given), unticked
        const create = section.locator('details.pf-api-create');
        if (!(await create.evaluate(d => d.open))) await create.locator('summary').click();
        assert.equal(await section.locator('input[name="health"]').isChecked(), false);
        await section.screenshot({ path: `${out}/reglages-api-${label}-${scheme}-vide.png` });
        await section.locator('#api-name').fill(`Clé ${label} ${scheme}`);
        await section.locator('input[name="health"]').check();
        await Promise.all([page.waitForNavigation(), section.getByRole('button', { name: 'Créer la clé' }).click()]);
        const secret = page.locator('#api-new-key');
        const token = await secret.inputValue();
        assert.match(token, /^pf_[A-Za-z0-9_-]{43}$/);
        assert.match(await section.innerText(), /Copie-la maintenant : elle ne sera plus affichée\./);
        // shown whole: the field wraps it, nothing hidden past its edge
        assert.ok(await secret.evaluate(el => el.scrollWidth <= el.clientWidth + 1 && el.scrollHeight <= el.clientHeight + 1));
        await section.getByRole('button', { name: 'Copier' }).click();
        await page.waitForFunction(() => /Clé copiée|Sélectionne la clé/.test(document.querySelector('[data-api-copied]').textContent));
        if (/Clé copiée/.test(await page.locator('[data-api-copied]').innerText())) {
          assert.equal(await page.evaluate(() => navigator.clipboard.readText()), token);
        }
        if (mobile) assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'no horizontal scroll');
        await section.screenshot({ path: `${out}/reglages-api-${label}-${scheme}-cle.png` });
        assert.equal((await api(page, token)).status(), 200);
        // once: a reload no longer has it, the list names it by its first characters
        await page.reload({ waitUntil: 'networkidle' });
        assert.equal(await page.locator('#api-new-key').count(), 0);
        assert.ok(!(await page.content()).includes(token));
        const row = section.locator('.pf-api-key', { hasText: `Clé ${label} ${scheme}` });
        assert.match(await row.innerText(), new RegExp(token.slice(0, 8).replace(/[-_]/g, '\\$&') + '…'));
        assert.match(await row.innerText(), /Lecture et santé · créée le .* · dernière utilisation le/);
        await section.screenshot({ path: `${out}/reglages-api-${label}-${scheme}-liste.png` });
        // « Révoquer » asks first; refused, nothing happens
        page.once('dialog', d => d.dismiss());
        await row.getByRole('button', { name: 'Révoquer' }).click();
        await page.waitForTimeout(200);
        assert.equal((await api(page, token)).status(), 200);
        page.once('dialog', d => { assert.match(d.message(), /Révoquer la clé « Clé /); d.accept(); });
        await Promise.all([page.waitForNavigation(), row.getByRole('button', { name: 'Révoquer' }).click()]);
        assert.match(await section.innerText(), /Clé révoquée/);
        assert.equal(await section.locator('.pf-api-key', { hasText: `Clé ${label} ${scheme}` }).count(), 0);
        const refused = await api(page, token);
        assert.equal(refused.status(), 401);
        assert.equal((await refused.json()).code, 'token_revoked');
        await section.screenshot({ path: `${out}/reglages-api-${label}-${scheme}-revoquee.png` });
        assert.deepEqual(errors, []);
        await context.close();
      }
    }
    // the documentation page, at the phone's width: no horizontal scroll outside its code blocks
    for (const [label, viewport, mobile] of [['393', { width: 393, height: 852 }, true], ['1280', { width: 1280, height: 900 }, false]]) {
      const page = await browser.newPage({ viewport, isMobile: mobile });
      await page.goto(base + '/developpeurs', { waitUntil: 'networkidle' });
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'docs: no horizontal scroll');
      await page.screenshot({ path: `${out}/developpeurs-${label}-haut.png` });
      await page.locator('#plan').scrollIntoViewIfNeeded();
      await page.screenshot({ path: `${out}/developpeurs-${label}-plan.png` });
      await page.locator('#erreurs').scrollIntoViewIfNeeded();
      await page.screenshot({ path: `${out}/developpeurs-${label}-erreurs.png` });
      await page.close();
    }
    console.log('api-settings: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
