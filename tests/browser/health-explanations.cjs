// Temporary synthetic preview only; no production account data.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const output = process.env.PF_HEALTH_EXPLANATION_SCREENSHOTS || '/tmp/paceforge-health-explanations';
const scenarios = ['low', 'isolated', 'respiration', 'day-only', 'mixed'];
const focus = process.env.PF_HEALTH_EXPLANATION_FOCUS || '';
const focusedScenarios = focus === 'mixed' ? ['mixed'] : ['low', 'mixed'];
const brands = /COROS|Garmin|Apple Santé|Apple Watch|Health Connect/;

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  let configurations = 0;
  try {
    for (const [offset, scenario] of scenarios.entries()) {
      if (focus && !focusedScenarios.includes(scenario)) continue;
      const base = 'http://127.0.0.1:' + (Number(process.env.PF_HEALTH_EXPLANATION_PORT || 8871) + offset);  // the first scenario's port
      const fixture = await (await fetch(base + '/__preview/health-explanation')).json();
      assert.equal(fixture.synthetic, true, 'Never run against a real account');
      assert.equal(fixture.scenario, scenario);
      for (const width of [320, 393]) for (const scale of focus ? [1] : [1, 2]) for (const dark of focus ? [false] : [false, true]) {
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
        const key = `${scenario}-${width}-${scale}-${dark ? 'dark' : 'light'}`;
        const details = page.locator('#comprendre-indicateurs');
        assert.equal(await details.count(), 1, key + ': explanations are consolidated');
        assert.equal(await details.evaluate(el => el.open), false, key + ': details start closed');
        assert.doesNotMatch(await page.locator('#main-content').innerText(), brands,
          key + ': primary readouts do not repeatedly name providers');
        assert.equal(await page.locator('#vfc details, #fc details, #respiration details, #tendances details').count(), 0,
          key + ': no scattered card explanations');
        assert.equal(await page.locator('#qualite-bilan').count(), 0, key + ': old explanation block removed');
        // the cards print no sentence (owner, 2026-10-16: « pas du texte »): the VFC's is the explanation's first line
        assert.equal(await page.locator('.pf-measurement-insight').count(), 0, key + ': no sentence on the cards');
        const insight = (await page.locator('section[aria-labelledby="explain-vfc"] > p').first().textContent()).trim();
        assert.match(await page.locator('#vfc .pf-viz-read').innerText(), /\bms\b/);
        // the card rests on its mean (« 64 ms en moyenne »): the method is said in the shared explanation, the latest
        // night's provenance with it — a current SDNN named apart from the RMSSD trend
        const vfcExplained = await page.locator('section[aria-labelledby="explain-vfc"]').textContent();
        assert.match(vfcExplained, /RMSSD/, key + ': the trend’s method is said');
        if (scenario === 'mixed') {
          assert.match(await page.locator('[data-viz-detail-for="vfc"]').textContent(), /SDNN/, key + ': the latest SDNN is named');
          assert.match(vfcExplained, /SDNN, distincte de la tendance RMSSD/, key + ': a SDNN is never read as the RMSSD trend');
        }
        assert.match(await page.locator('#fc .pf-viz-read').innerText(), /\bbpm\b/);
        assert.doesNotMatch(await page.locator('#fc .pf-viz-read').innerText(), /SDNN|RMSSD/);
        // the cards print no status line since their harmonisation (2026-10-18, only the acute night HR alert keeps
        // one): a low 7-day VFC trend is said under the Récupération ring; an isolated low night is not a trend
        const ringNote = await page.locator('.pf-ring-recup .pf-ring-note').allInnerTexts();
        assert.equal(await page.locator('#vfc .pf-card-status').count(), 0, key + ': no status line on the VFC card');
        if (scenario === 'low' || scenario === 'day-only') {
          assert.match(insight, /baisse.*fatigue ou stress/);
          assert.match(ringNote.join(' '), /VFC plus basse que d'habitude/, key + ': the low trend is said');
        } else if (scenario === 'mixed') {
          assert.equal(insight, 'Ce relevé n’est pas comparable à la tendance affichée.',
            'Different HRV methods keep their explanation in the shared disclosure');
          assert.doesNotMatch(insight, /fatigue|stress|baisse/i,
            'Changing HRV method must not look like a physiological drop');
        } else {
          assert.match(insight, /Dernière mesure sous le repère, tendance sur 7 jours habituelle|dernier point est bas.*tendance sur 7 jours reste habituelle/);
          assert.doesNotMatch(ringNote.join(' '), /VFC/, key + ': an isolated low night is not said as a low trend');
        }
        if (scenario === 'respiration') {
          assert.equal(await page.locator('#mesures-nocturnes #respiration').count(), 1);
          assert.equal(await page.locator('#tendances #trend-resp_night-title').count(), 0);
          // the card rests on its mean since the harmonisation (2026-10-18): last night's 18 is its last night's readout
          assert.match(await page.locator('#respiration .pf-viz-read').innerText(), /resp\.\/min[\s\S]*en moyenne/);
          assert.match(await page.locator('#respiration .pf-viz-data').evaluate(el => JSON.parse(el.textContent).r.at(-1).join(' ')),
            /^18[\s\S]*resp\.\/min/, key + ': the received night respiration');
          assert.equal(await page.locator('#sommeil .pf-tl-seg').count(), 10);
        } else {
          assert.equal(await page.locator('#respiration').count(), 0, key + ': no manufactured nocturnal respiration');
          assert.equal(await page.locator('#sommeil .pf-tl').count(), 0, key + ': COROS totals do not manufacture stage chronology');
        }
        if (scenario === 'day-only') {
          assert.doesNotMatch(await page.locator('#main-content').innerText(), /Respiration sur 24 h/,
            key + ': the phone\u2019s 24-h breathing left the page');
          assert.doesNotMatch(await page.locator('#recuperation').innerText(), /Respiration/);
        }
        for (const selector of ['#sommeil-detail figure:visible', '#recuperation-detail figure', '#vfc figure', '#fc figure']) {
          assert.ok(await page.locator(selector).first().isVisible(), key + ': main graphs remain open');
        }
        await page.screenshot({ path: `${output}/${key}-top.png` });
        await page.locator('#vfc').screenshot({ path: `${output}/${key}-vfc.png` });
        if (scenario === 'respiration') await page.locator('#respiration').screenshot({ path: `${output}/${key}-respiration.png` });
        if (width === 393 && scale === 1 && !dark) await page.screenshot({ path: `${output}/${key}-full.png`, fullPage: true });
        for (const fig of await page.locator('#vfc figure, #fc figure, #respiration figure, #tendances figure').all()) {
          const selected = await fig.evaluate(el => {
            const data = JSON.parse(el.querySelector('.pf-viz-data').textContent);
            const index = Math.max(0, data.sel - 2), input = el.querySelector('.pf-viz-range');
            input.value = index; input.dispatchEvent(new Event('input'));
            return { date: data.r[index][2], spoken: data.a[index], detail: data.r[index][3], key: el.dataset.vizKey };
          });
          const readout = await fig.locator('.pf-viz-read').innerText();
          assert.doesNotMatch(readout, brands, key + ': selecting a chart does not restore visible brands');
          // the selected historical night says its method where its readout is spoken; its provenance names no SDNN
          if (selected.key === 'vfc') {
            assert.match(await fig.locator('.pf-viz-range').getAttribute('aria-valuetext'), /RMSSD/,
              key + ': the selected historical RMSSD is distinct from a current SDNN');
            assert.doesNotMatch(await page.locator('[data-viz-detail-for="vfc"]').textContent(), /SDNN/,
              key + ': the selected RMSSD night is not given the current SDNN');
          }
          assert.ok(readout.includes(selected.date), key + ': selected date remains visible');
          assert.equal(await fig.locator('.pf-viz-range').getAttribute('aria-valuetext'), selected.spoken);
          // Tendances' night HR has no provenance of its own: the FC de nuit card's is the one explained
          if (selected.key !== 'trend-hr') assert.equal(await page.locator(`[data-viz-detail-for="${selected.key}"]`).textContent(), selected.detail,
            key + ': central provenance follows selected chart point');
        }
        for (const fig of await page.locator('#sommeil-detail figure:visible, #recuperation-detail figure, #entrainement-semaine figure').all()) {
          const selected = await fig.evaluate(el => {
            const data = JSON.parse(el.querySelector('.pf-viz-data').textContent);
            const index = Math.max(0, data.d.length - 3), input = el.querySelector('.pf-viz-range');
            input.value = index; input.dispatchEvent(new Event('input'));
            return { date: (data.selectionDates || data.dateLabels)[index], spoken: data.a[index] };
          });
          const readout = await fig.locator('.pf-viz-read').innerText();
          assert.doesNotMatch(readout, brands, key + ': summary graph selection stays source-free');
          assert.ok(readout.includes(selected.date), key + ': summary selection keeps its date');
          assert.equal(await fig.locator('.pf-viz-range').getAttribute('aria-valuetext'), selected.spoken);
        }
        await details.locator('summary').click();
        assert.match(await details.innerText(), scenario === 'respiration' ? /Garmin/ : /COROS/);
        assert.match(await details.innerText(), /RMSSD|moyenne des logarithmes|logarithm/);
        assert.match(await details.innerText(), /n’indique pas la cause|ne permet pas, seule, de diagnostiquer/);
        assert.ok(await page.locator('#explain-vfc').isVisible());
        if (scenario === 'day-only') assert.doesNotMatch(await details.innerText(), /Respiration sur 24 h/);
        if (width === 393 && scale === 1 && !dark) await details.screenshot({ path: `${output}/${key}-details.png` });
        const layout = await page.evaluate(() => {
          const failures = [];
          if (document.documentElement.scrollWidth > innerWidth + 1) failures.push('horizontal page overflow');
          for (const el of document.querySelectorAll('.pf-viz-read, .pf-trend-delta, .pf-viz-legend, #comprendre-indicateurs > summary')) {
            if (!el.getClientRects().length) continue;
            const box = el.getBoundingClientRect();
            if (box.left < -1 || box.right > innerWidth + 1) failures.push(el.className + ' outside viewport');
          }
          return failures;
        });
        assert.deepEqual(layout, [], key + ': readable with large text');
        assert.deepEqual(errors, [], key + ': browser errors');
        await page.close();
        configurations++;
      }
    }
    fs.writeFileSync(`${output}/verification${focus ? '-' + focus : ''}.json`, JSON.stringify({ synthetic: true, configurations,
      scenarios: focus ? focusedScenarios : scenarios, viewport_widths: [320, 393],
      font_scales: focus ? [1] : [1, 2], themes: focus ? ['light'] : ['light', 'dark'],
      checked: ['low trend vs isolated low point', 'no primary provider names before/after chart selection',
        'central selected-point provenance and methodology', 'night respiration only when received',
        'day respiration never used as night respiration', 'visible graphs/dates/units', 'large text without page overflow'] }, null, 2));
    console.log(`Health explanations: ${configurations} synthetic configurations passed.`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
