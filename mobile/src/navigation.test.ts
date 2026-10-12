import { test } from 'node:test';
import assert from 'node:assert/strict';
import { tabForUrl } from './navigation';

test('internal detail pages keep the right native tab; external URLs cannot change it', () => {
  assert.equal(tabForUrl('https://paceforge.fr/accueil'), 'home');
  assert.equal(tabForUrl('https://paceforge.fr/activity/42'), 'activities');
  assert.equal(tabForUrl('https://paceforge.fr/simulator/route/42'), 'courses');
  assert.equal(tabForUrl('https://paceforge.fr/sante?s=7j'), 'health');
  assert.equal(tabForUrl('https://paceforge.fr/settings#coros'), 'devices');
  assert.equal(tabForUrl('https://paceforge.fr.evil.test/sante'), null);
  assert.equal(tabForUrl('https://paceforge.fr/auth/login'), null);
});
