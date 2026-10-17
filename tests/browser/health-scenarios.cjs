// Explicit synthetic fixtures only; health_scenarios.py uses temporary SQLite.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const output = process.env.PF_HEALTH_SCENARIO_SCREENSHOTS || '/tmp/paceforge-health-scenarios';
const scenarios = [
  ['conflict', process.env.PF_HEALTH_CONFLICT_URL || 'http://127.0.0.1:8868'],
  ['missing', process.env.PF_HEALTH_MISSING_URL || 'http://127.0.0.1:8869'],
  ['stages', process.env.PF_HEALTH_STAGES_URL || 'http://127.0.0.1:8870'],
];

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  try {
    for (const [scenario, base] of scenarios) {
      const fixture = await (await fetch(base + '/__preview/health-scenario')).json();
      assert.equal(fixture.synthetic, true, 'Never run this script against a real account');
      assert.equal(fixture.scenario, scenario);
      for (const width of [320, 393]) for (const scale of [1, 2]) for (const dark of [false, true]) {
        const page = await browser.newPage({ viewport: { width, height: 852 }, isMobile: true,
          hasTouch: true, reducedMotion: 'reduce' });
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.addInitScript(() => {
          window.ReactNativeWebView = { postMessage() {} }; window.paceforgeNativeRefresh = 2;
        });
        await page.goto(base + '/sante', { waitUntil: 'networkidle' });
        await page.evaluate(({ scale, dark }) => {
          document.documentElement.style.fontSize = 16 * scale + 'px';
          document.documentElement.classList.toggle('pf-large-text', scale >= 1.4);
          document.documentElement.classList.toggle('dark', dark);
        }, { scale, dark });
        const key = `synthetic-${scenario}-${width}-${scale}-${dark ? 'dark' : 'light'}`;
        // the card of signals left (owner, 2026-10-17): its reason under the Récupération ring, the race on the curves
        assert.equal(await page.locator('#synthese-sante').count(), 0, key);
        const dials = page.locator('.pf-dials'), note = page.locator('.pf-ring-recup .pf-ring-note');
        assert.match(await page.locator('.pf-curves-race').innerText(), /Ultra synthétique · J\+13/);
        assert.ok(await page.locator('#vfc .pf-viz-race').count() >= 1, key + ': the race days are marked on the curves');
        assert.doesNotMatch(await page.locator('#main-content').innerText(), /\bNone\b|prêt à|tu peux courir/i);
        if (scenario === 'conflict') {
          assert.ok(await page.locator('.pf-ring-recup').evaluate(el => el.classList.contains('is-warn')));
          assert.match(await note.innerText(), /FC de nuit plus haute que d'habitude/);
          assert.doesNotMatch(await note.innerText(), /VFC|fatigue|chaleur|alcool|maladie/i,
            'The ring names the measured change without the generic explanatory paragraph; the HRV stays habitual');
          assert.match(await page.locator('#comprendre-indicateurs').textContent(), /fatigue|chaleur|alcool|maladie/i,
            'The explanation remains accessible in the disclosure');
          assert.match(await page.locator('#fc .pf-viz-read').innerText(), /56/);
          assert.match(await page.locator('#vfc .pf-viz-read').innerText(), /65/);
        } else if (scenario === 'missing') {
          assert.match(await page.locator('.pf-sante-h1').innerText(), /Dernier bilan/);
          assert.match(await page.locator('.pf-sante-head').innerText(), /En attente de la prochaine nuit/);
          const latestDay = await page.locator('#recuperation-detail .pf-viz-data').evaluate(el => {
            const data = JSON.parse(el.textContent); return data.d.at(-1);
          });
          assert.ok(latestDay < fixture.day, 'A missing night must not create an index for today');
        }
        if (scenario === 'stages') {
          const timeline = page.locator('#sommeil .pf-tl');
          assert.ok(await timeline.isVisible(), 'Provided stage intervals must produce a visible chronological hypnogram');
          assert.equal(await timeline.locator('.pf-tl-seg').count(), 10);
          assert.equal(await timeline.locator('.is-deep').count(), 2);
          assert.equal(await timeline.locator('.is-rem').count(), 2);
          assert.match(await page.locator('#sommeil').innerText(), /Déroulé de la nuit/);
          const positions = await timeline.locator('.pf-tl-seg').evaluateAll(els => els.map(el => +el.getAttribute('x')));
          assert.deepEqual(positions, [...positions].sort((a, b) => a - b), 'Stage positions follow their timestamps');
          assert.deepEqual(await timeline.locator('.pf-tl-lane').evaluateAll(labels => labels.flatMap(el => {
            const box = el.getBBox(); return box.x + box.width > 60 ? [el.textContent] : [];
          })), [], 'Stage lane labels leave room for the chronology');
        } else {
          assert.equal(await page.locator('#sommeil .pf-tl').count(), 0, 'Stage totals must not fabricate a chronology');
        }
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, key + ': horizontal overflow');
        for (const selector of ['#sommeil-detail figure', '#recuperation-detail figure', '#vfc figure', '#fc figure']) {
          assert.ok(await page.locator(selector).first().isVisible(), key + ': hidden primary plot');
        }
        await page.screenshot({ path: `${output}/${key}-top.png` });
        await dials.screenshot({ path: `${output}/${key}-dials.png` });
        if (scenario === 'stages') await page.locator('#sommeil').screenshot({ path: `${output}/${key}-sleep.png` });
        if (width === 393 && scale === 1 && !dark) {
          await page.screenshot({ path: `${output}/${key}-full.png`, fullPage: true });
        }
        assert.deepEqual(errors, [], key + ': browser errors');
        await page.close();
      }
    }
    fs.writeFileSync(`${output}/README.txt`, 'Synthetic fixtures only. No real account or production data.\nConflict: habitual RMSSD, elevated nocturnal HR, recorded ultra J+13.\nMissing: no sleep/HR/HRV for today, latest assessment dated yesterday.\nStages: ten timestamped Garmin sleep intervals, including two deep and two REM periods.\n320/393px, light/dark, text 100/200%.\n');
    console.log('Synthetic Health conflict, missing-night and chronological sleep scenarios passed at 320/393px, light/dark, text 100/200%.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
