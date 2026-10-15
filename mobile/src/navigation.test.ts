import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isBridgeUrl, messagePageUrl, sameScreen, screenUrl, tabForUrl, tabNavigationScript } from './navigation';

test('internal detail pages keep the right native tab; external URLs cannot change it', () => {
  assert.equal(tabForUrl('https://paceforge.fr/accueil'), 'health');
  assert.equal(tabForUrl('https://paceforge.fr/activity/42'), 'activities');
  assert.equal(tabForUrl('https://paceforge.fr/simulator/route/42'), 'courses');
  assert.equal(tabForUrl('https://paceforge.fr/sante?s=7j'), 'health');
  assert.equal(tabForUrl('https://paceforge.fr/settings#coros'), 'devices');
  assert.equal(tabForUrl('https://paceforge.fr.evil.test/sante'), null);
  assert.equal(tabForUrl('https://paceforge.fr/auth/login'), null);
});

test('a sync always returns to a real screen, even if its saved location was a bridge page', () => {
  for (const path of ['/mobile/session?nonce=old', '/mobile/connections', '/mobile/connect#resume']) {
    assert.equal(isBridgeUrl('https://paceforge.fr' + path), true);
    assert.equal(screenUrl('https://paceforge.fr' + path), 'https://paceforge.fr/sante');
  }
  for (const path of ['/sante#poids', '/activity/12', '/mobile/privacy', '/settings']) {
    assert.equal(screenUrl('https://paceforge.fr' + path), 'https://paceforge.fr' + path);
  }
  assert.equal(screenUrl('https://example.org/sante'), 'https://paceforge.fr/sante');
});

test('native screen reuse ignores only its navigation marker, preserving filters and section destinations', () => {
  assert.equal(sameScreen('https://paceforge.fr/sante?r=14&app_nav=old', 'https://paceforge.fr/sante?r=14&app_nav=new'), true);
  assert.equal(sameScreen('https://paceforge.fr/sante?r=14', 'https://paceforge.fr/sante?r=90'), false);
  assert.equal(sameScreen('https://paceforge.fr/sante', 'https://paceforge.fr/sante#sommeil'), false);
  assert.equal(sameScreen('https://paceforge.fr/activity/1', 'https://paceforge.fr/activity/2'), false);
  assert.equal(sameScreen('https://paceforge.fr/mobile/session', 'https://paceforge.fr/mobile/session'), false);
  assert.equal(sameScreen('https://other.test/sante', 'https://other.test/sante'), false);
});

test('soft tab navigation includes a separate nonce and an explicit fallback for an older page', () => {
  const script = tabNavigationScript('https://paceforge.fr/activities?m=distance', 'tab-nonce');
  assert.match(script, /window\.paceforgeTabs\.navigate/);
  assert.match(script, /type:'tab-navigation'/);
  assert.match(script, /status:'fallback'/);
  assert.match(script, /nonce:"tab-nonce"/);
  assert.doesNotMatch(script, /navigation-ready/);
});

test('logical reading URLs remain bound to the original trusted frame after history changes', () => {
  const frame = 'https://paceforge.fr/sante';
  const logical = 'https://paceforge.fr/activities?m=distance&app_nav=one';
  assert.equal(messagePageUrl(frame, logical, frame, { page_url: logical }), logical);
  assert.equal(messagePageUrl(frame, logical, frame, {}), frame);
  assert.equal(messagePageUrl(frame, logical, frame, { page_url: frame }), frame);
  assert.equal(messagePageUrl('https://evil.test/sante', logical, frame, { page_url: logical }), 'https://evil.test/sante');
  assert.equal(messagePageUrl(frame, 'https://paceforge.fr/mobile/session', frame, { page_url: 'https://paceforge.fr/mobile/session' }), frame);
  assert.equal(messagePageUrl(frame, logical, 'https://paceforge.fr/settings', { page_url: logical }), frame);
});
