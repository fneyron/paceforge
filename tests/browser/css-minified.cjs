// interface.css as the image serves it (app/css_minify.py, run by the Dockerfile) reads the same as the source: the
// same rules to the browser (CSSOM, rule for rule), and the same computed styles for every element of a few pages,
// light and dark, phone and desktop, with one stylesheet then the other in its place.
// Start: python -m uvicorn tests.browser.preview_app:app --host 127.0.0.1 --port 8853
// Run with Playwright installed: node tests/browser/css-minified.cjs   (PF_PYTHON: the venv's python, .venv/bin/python)
const { chromium } = require('playwright');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');
const raw = fs.readFileSync(path.join(root, 'app/static/css/interface.css'), 'utf8');
const min = execFileSync(process.env.PF_PYTHON || path.join(root, '.venv/bin/python'),
  ['-c', 'import sys; from app.css_minify import minify; sys.stdout.write(minify(sys.stdin.read()))'],
  { cwd: root, input: raw, encoding: 'utf8' });
const base = process.env.PF_PREVIEW_URL || 'http://127.0.0.1:8853';
assert.ok(min.length < raw.length * 0.8, 'minified');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.goto(base + '/auth/login');
  const rules = await page.evaluate(([a, b]) => {
    const read = text => { const s = new CSSStyleSheet(); s.replaceSync(text); return [...s.cssRules].map(r => r.cssText); };
    return [read(a), read(b)];
  }, [raw, min]);
  assert.equal(rules[0].length, rules[1].length);
  // a declaration holding var() or env() keeps its text as written (it is parsed once substituted): there, the
  // whitespace around a comma or doubled is not CSSOM-normalised, though no grammar reads it; compared without it
  const norm = t => t.split(/("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')/).map((part, k) => k % 2 ? part : part.replace(/\s+/g, ' ').replace(/ ?, ?/g, ',')).join('');
  let exact = 0;
  rules[0].forEach((r, i) => { assert.equal(norm(rules[1][i]), norm(r), 'rule ' + i); if (rules[1][i] === r) exact++; });
  console.log(`CSSOM: ${rules[0].length} rules, the same (${exact} character for character, the rest differ only by whitespace in a var()/env() value)`);

  // computed styles: every element of a few pages, once loaded, with the raw stylesheet then the minified one in its
  // place (the same DOM, motion off: only the stylesheet changes between the two readings)
  const widths = [[{ width: 393, height: 800 }, 'light'], [{ width: 1280, height: 900 }, 'light'], [{ width: 393, height: 800 }, 'dark']];
  for (const url of ['/simulator/routes/1', '/sante', '/activities', '/settings', '/simulator', '/']) {
    for (const [viewport, colorScheme] of widths) {
      const p = await browser.newPage({ viewport, colorScheme, reducedMotion: 'reduce' });
      await p.goto(base + url, { waitUntil: 'networkidle' });
      await p.waitForTimeout(300);
      const result = await p.evaluate(async ([a, b]) => {
        const still = document.createElement('style');
        still.textContent = '*,*::before,*::after{transition:none!important;animation:none!important}';
        document.head.appendChild(still);
        const link = document.querySelector('link[href*="/static/css/interface.css"]');
        const style = document.createElement('style'); link.replaceWith(style);
        const frames = () => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
        const snap = () => [...document.querySelectorAll('body *')].map(el => {
          const cs = getComputedStyle(el); let s = '';
          for (let i = 0; i < cs.length; i++) s += cs[i] + ':' + cs.getPropertyValue(cs[i]) + ';';
          for (const pseudo of ['::before', '::after']) { const ps = getComputedStyle(el, pseudo); s += pseudo + ps.content + ps.display + ps.width + ps.color + ps.background; }
          return s;
        });
        style.textContent = a; await frames(); const first = snap();
        style.textContent = b; await frames(); const second = snap();
        style.textContent = a; await frames(); const again = snap();
        let diff = 0, noise = 0;
        first.forEach((s, i) => { if (s !== again[i]) noise++; else if (s !== second[i]) diff++; });
        return { n: first.length, diff, noise };
      }, [raw, min]);
      await p.close();
      assert.equal(result.diff, 0, `${url} @${viewport.width}: ${result.diff} elements differ`);
      console.log(`${url} @${viewport.width}px ${colorScheme}: ${result.n} elements, the same computed styles` + (result.noise ? ` (${result.noise} moving by themselves, left out)` : ''));
    }
  }
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
