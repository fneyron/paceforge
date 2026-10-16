/* Local synthetic preview: readout sizing and chart stability at iPhone widths/text sizes.
   Browser layout regression only; not a physical-device rendering benchmark. */
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const fs = require('node:fs');
const base = process.env.PF_PREVIEW_URL || 'http://127.0.0.1:8891';
const output = process.env.PF_PREVIEW_OUTPUT || '/tmp';
assert.ok(['127.0.0.1', 'localhost'].includes(new URL(base).hostname), 'Use a local synthetic fixture');
(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const measurements = [];
  try {
    for (const width of [320, 393]) for (const scale of [1, 2]) {
      const page = await browser.newPage({ viewport: { width, height: 700 }, isMobile: true, hasTouch: true });
      await page.addInitScript(() => { window.ReactNativeWebView = { postMessage() {} }; });
      for (const path of ['/sante?r=90', '/activities']) {
        await page.goto(base + path, { waitUntil: 'networkidle' });
        await page.evaluate(scale => {
          document.documentElement.style.fontSize = 16 * scale + 'px';
          document.documentElement.classList.toggle('pf-large-text', scale > 1);
          document.querySelectorAll('details').forEach(el => { el.open = true; });
          window.PFViz.scan(document);
        }, scale);
        await page.waitForTimeout(250);
        const figures = await page.evaluate(() => [...document.querySelectorAll('figure[data-viz]')].flatMap(fig => {
          const input = fig.querySelector('.pf-viz-range'), read = fig.querySelector('.pf-viz-read'), svg = fig.querySelector('svg.pf-viz-svg');
          if (!input || !read || !svg || !fig.getBoundingClientRect().height || fig.classList.contains('pf-viz-quiet')) return [];
          const data = JSON.parse(fig.querySelector('.pf-viz-data').textContent);
          const initial = { height: read.getBoundingClientRect().height, plotY: svg.getBoundingClientRect().y - fig.getBoundingClientRect().y };
          let min = initial.height, max = initial.height, movement = 0, longest = '', values = 0;
          for (let i = 0; i < data.r.length + (data.rest ? 1 : 0); i++) {
            input.value = i; input.dispatchEvent(new Event('input'));
            const height = read.getBoundingClientRect().height;
            min = Math.min(min, height); max = Math.max(max, height);
            movement = Math.max(movement, Math.abs(svg.getBoundingClientRect().y - fig.getBoundingClientRect().y - initial.plotY));
            const text = read.innerText;
            if (text.length > longest.length) longest = text;
            values++;
          }
          return [{ key: fig.dataset.vizKey || fig.getAttribute('aria-label'), initial_height: initial.height,
            min_height: min, max_height: max, plot_movement_px: movement, values_checked: values, longest_readout: longest }];
        }));
        assert.ok(figures.length, `${path} ${width}px ${scale}x: no sized readouts checked`);
        for (const fig of figures) {
          assert.ok(fig.plot_movement_px <= 1, `${path} ${width}px ${scale}x ${fig.key}: chart moved ${fig.plot_movement_px}px while selecting`);
          assert.ok(fig.max_height - fig.min_height <= 1, `${path} ${width}px ${scale}x ${fig.key}: readout height changed`);
        }
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'Horizontal overflow');
        measurements.push({ path, width, scale, figures });
        if (scale === 2) await page.screenshot({ path: `${output}/chart-readouts-${path.startsWith('/sante') ? 'health' : 'activities'}-${width}-2x.png` });
      }
      await page.close();
    }
    fs.writeFileSync(output + '/chart-readouts-verification.json', JSON.stringify({ synthetic: true, measurements }, null, 2));
    console.log('Chart readouts: 320/393px, 100/200% text, every selection, stable plot position passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
