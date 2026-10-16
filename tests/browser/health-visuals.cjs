// Synthetic preview only. Run with the same server as iphone-ux.cjs.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const base = process.env.PF_PREVIEW_URL || 'http://127.0.0.1:8853';
const output = process.env.PF_HEALTH_SCREENSHOTS || '/tmp/paceforge-health-visuals';
const required = ['#sommeil-detail [data-range-panel]:not([hidden]) figure', '#recuperation-detail figure',
  '#vfc figure', '#fc figure', '#entrainement-semaine figure', '#tendances figure'];

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  try {
    for (const width of [320, 393]) for (const scale of [1, 2]) for (const dark of [false, true]) {
      const page = await browser.newPage({ viewport: { width, height: 852 }, isMobile: true, hasTouch: true, reducedMotion: 'reduce' });
      const errors = []; page.on('pageerror', error => errors.push(error.message));
      await page.addInitScript(() => { window.ReactNativeWebView = { postMessage() {} }; window.paceforgeNativeRefresh = 2; });
      await page.goto(base + '/sante', { waitUntil: 'networkidle' });
      await page.evaluate(({ scale, dark }) => {
        document.documentElement.style.fontSize = 16 * scale + 'px';
        document.documentElement.classList.toggle('pf-large-text', scale >= 1.4);
        document.documentElement.classList.toggle('dark', dark);
      }, { scale, dark });
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      const mode = dark ? 'dark' : 'light', capture = `${width}-${scale}-${mode}`;
      const label = `${width}px/${scale * 100}%/${mode}`;
      assert.ok(await page.locator('#synthese-sante').isVisible(), `${label}: measured signals need a visible synthesis`);
      assert.ok(await page.locator('#synthese-sante .pf-synthesis-reasons > li').count() <= 2, `${label}: the primary synthesis has at most two reasons`);
      assert.ok((await page.locator('.pf-synthesis-freshness').innerText()).trim(), `${label}: the synthesis needs a measurement date`);
      assert.equal(await page.locator('#ressenti').evaluate(el => el.open), false, `${label}: the optional check-in starts collapsed`);
      assert.equal(await page.locator('#ressenti input:checked').count(), 0, `${label}: no feeling is inferred from the measurements`);
      await page.evaluate(() => window.paceforgeRememberPage());
      assert.deepEqual(await page.evaluate(() => {
        const key = 'pf-view:' + document.querySelector('#main-content').dataset.account + ':/sante';
        return JSON.parse(sessionStorage.getItem(key)).charts;
      }), {}, `${label}: untouched defaults must follow the newest measurement on the next visit`);
      for (const selector of required) {
        assert.ok(await page.locator(selector).count(), `${label}: missing main graph ${selector}`);
        for (const figure of await page.locator(selector).all()) assert.ok(await figure.isVisible(), `${label}: folded main graph ${selector}`);
      }
      if (await page.locator('#forme').count()) assert.ok(await page.locator('#forme figure').isVisible(), `${label}: fitness graph folded`);
      assert.ok(await page.locator('.pf-phases-legend').isVisible(), `${label}: sleep phases missing from the fixture`);
      for (const selector of ['#qualite-bilan', '#charge-detail', '#sommeil-habitudes']) {
        if (await page.locator(selector).count()) assert.equal(await page.locator(selector).evaluate(el => el.open), false, `${label}: secondary information opens by default`);
      }
      await page.screenshot({ path: `${output}/health-default-${capture}.png`, fullPage: true });
      await page.screenshot({ path: `${output}/health-top-${capture}.png` });
      await page.locator('.pf-dials').screenshot({ path: `${output}/dials-${capture}.png` });
      assert.match(await page.locator('.pf-ring-recup .pf-ring-value').innerText(), /^100\s*\/100$/, `${label}: fixture must exercise a three-digit index, never a recovery percentage`);
      await page.locator('#ressenti > summary').click();
      for (const choice of await page.locator('.pf-checkin-choice').all()) {
        const box = await choice.boundingBox();
        assert.ok(box.height >= 44 && box.x >= 0 && box.x + box.width <= width + 1, `${label}: check-in choices are readable tap targets`);
      }
      await page.locator('.pf-health-checkin').screenshot({ path: `${output}/checkin-${capture}.png` });
      await page.locator('#ressenti > summary').click();
      // Every available main chart keeps a visible date and accessible value on selection.
      for (const selector of required) for (const figure of await page.locator(selector).all()) {
        const selected = await figure.evaluate(fig => {
          const data = JSON.parse(fig.querySelector('.pf-viz-data').textContent), input = fig.querySelector('.pf-viz-range');
          if (!input || !data.d?.length) return null;
          const index = Math.max(0, data.d.length - 3);
          input.value = index; input.dispatchEvent(new Event('input'));
          return { expected: data.a[index], date: fig.classList.contains('pf-viz-quiet') ? data.dateLabels[index] : data.r[index][2], key: fig.dataset.vizKey };
        });
        if (!selected) continue;
        assert.equal(await figure.locator('.pf-viz-range').getAttribute('aria-valuetext'), selected.expected, `${label}: ${selected.key} accessible selection`);
        assert.ok((await figure.locator('.pf-viz-read').innerText()).includes(selected.date), `${label}: ${selected.key} selected date is not visible`);
      }
      const failures = await page.evaluate(() => {
        const failures = [], visible = el => el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
        const rect = el => el.getBoundingClientRect();
        const overlap = (a, b) => Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1 && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1;
        if (document.documentElement.scrollWidth > innerWidth + 1) failures.push('page overflows horizontally');
        for (const ring of document.querySelectorAll('.pf-ring')) {
          const dial = rect(ring.querySelector('.pf-ring-dial'));
          const value = ring.querySelector('.pf-ring-value'), range = document.createRange();
          range.selectNodeContents(value);
          const text = range.getBoundingClientRect(), inset = dial.width * .08;
          if (text.left < dial.left + inset - 1 || text.right > dial.right - inset + 1 || text.top < dial.top + inset - 1 || text.bottom > dial.bottom - inset + 1) failures.push(`dial value touches its arc: ${value.textContent}`);
          const labels = [...ring.querySelectorAll('.pf-ring-label, .pf-ring-sub, .pf-ring-note')].filter(visible);
          for (const [index, label] of labels.entries()) {
            if (overlap(dial, rect(label))) failures.push(`dial overlaps label: ${label.textContent}`);
            for (const other of labels.slice(index + 1)) if (overlap(rect(label), rect(other))) failures.push(`dial labels overlap: ${label.textContent}`);
            const walker = document.createTreeWalker(label, NodeFilter.SHOW_TEXT);
            while (walker.nextNode()) for (const word of walker.currentNode.textContent.matchAll(/\S+/g)) {
              range.setStart(walker.currentNode, word.index); range.setEnd(walker.currentNode, word.index + word[0].length);
              if (range.getClientRects().length > 1) failures.push(`dial label breaks inside a word: ${word[0]}`);
            }
          }
        }
        for (const legend of document.querySelectorAll('.pf-viz-legend, .pf-phases-legend')) {
          if (!visible(legend)) continue;
          const box = rect(legend), items = [...legend.children].filter(visible);
          for (const [index, item] of items.entries()) {
            const itemBox = rect(item);
            if (itemBox.left < box.left - 1 || itemBox.right > box.right + 1 || item.scrollWidth > item.clientWidth + 1) failures.push(`legend overflows: ${item.textContent.trim()}`);
            for (const other of items.slice(index + 1)) if (overlap(itemBox, rect(other))) failures.push(`legend items overlap: ${item.textContent.trim()}`);
          }
          const figure = legend.closest('figure'), plot = figure?.querySelector('.pf-viz-plot');
          if (plot && overlap(box, rect(plot))) failures.push(`legend overlaps plot: ${figure.dataset.vizKey}`);
          for (const readout of figure?.querySelectorAll('.pf-viz-l1, .pf-viz-l2') || []) {
            if (visible(readout) && overlap(box, rect(readout))) failures.push(`legend overlaps selected value/date: ${figure.dataset.vizKey}`);
          }
        }
        return failures;
      });
      assert.deepEqual(failures, [], `${label}: graph layout regressions`);
      await page.evaluate(() => scrollTo({ top: 0, behavior: 'instant' }));
      await page.screenshot({ path: `${output}/health-selected-${capture}.png`, fullPage: true });
      await page.locator('#vfc').screenshot({ path: `${output}/hrv-selected-${capture}.png` });
      await page.locator('#sommeil').screenshot({ path: `${output}/sleep-selected-${capture}.png` });
      await page.locator('#vfc .pf-viz-range').dispatchEvent('keydown', { key: 'Escape' });
      const reset = await page.locator('#vfc figure').evaluate(fig => {
        const data = JSON.parse(fig.querySelector('.pf-viz-data').textContent);
        window.paceforgeRememberPage();
        const key = 'pf-view:' + document.querySelector('#main-content').dataset.account + ':/sante';
        return { expected: data.a[data.sel], actual: fig.querySelector('input').getAttribute('aria-valuetext'),
          pinned: fig.dataset.vizKey in JSON.parse(sessionStorage.getItem(key)).charts };
      });
      assert.equal(reset.actual, reset.expected, `${label}: Escape returns to the latest measurement`);
      assert.equal(reset.pinned, false, `${label}: returning to the latest measurement must not pin its date`);
      assert.deepEqual(errors, [], `${label}: browser errors`);
      await page.close();
    }
    console.log('Health visuals: compact synthesis, optional check-in, main plots, dated selections and readable index/legends at 320/393px, 100/200% text, light/dark passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
