import { test } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { agendaReturnUrl, agendaScheme, agendaSheetUrl, appShellScript, calendarFeedUrl, calendarOrigin, destinations, isBridgeUrl, messagePageUrl, prepareNavigationScript, refreshable, reloadable, sameScreen, screenUrl, tabForUrl, tabNavigationScript, tabs } from './navigation';

test('internal detail pages keep the right native tab; external URLs cannot change it', () => {
  assert.equal(tabForUrl('https://paceforge.fr/accueil'), 'health');
  assert.equal(tabForUrl('https://paceforge.fr/activity/42'), 'activities');
  assert.equal(tabForUrl('https://paceforge.fr/simulator/route/42'), 'courses');
  assert.equal(tabForUrl('https://paceforge.fr/sante?s=7j'), 'health');
  assert.equal(tabForUrl('https://paceforge.fr/settings#coros'), 'devices');
  assert.equal(tabForUrl('https://paceforge.fr.evil.test/sante'), null);
  assert.equal(tabForUrl('https://paceforge.fr/auth/login'), null);
});

test('four native tabs in the site\'s order, each with its destination; Coaching keeps its own pages', () => {
  assert.deepEqual(tabs.map(([key, label]) => [key, label, destinations[key]]), [
    ['courses', 'Courses', '/simulator'], ['activities', 'Activités', '/activities'], ['health', 'Santé', '/sante'],
    ['coaching', 'Coaching', '/coaching']]);
  for (const path of ['/coaching', '/coaching?d=2026-11-21&v=mois', '/coaching?refaire=1', '/coaching/athletes', '/coaching/athletes/12']) {
    assert.equal(tabForUrl('https://paceforge.fr' + path), 'coaching', path);
  }
  assert.equal(tabForUrl('https://paceforge.fr/coachings'), null);
  assert.equal(tabForUrl('https://paceforge.fr.evil.test/coaching'), null);
  // an activity opened from a session belongs to Activités; the saved Coaching screen is where its tab returns
  assert.equal(tabForUrl('https://paceforge.fr/activity/42'), 'activities');
  assert.equal(screenUrl('https://paceforge.fr/coaching/athletes/12'), 'https://paceforge.fr/coaching/athletes/12');
});

test('Coaching reloads on a pull and never joins the two reading tabs\' soft navigation or sync refresh', () => {
  for (const path of ['/coaching', '/coaching?d=2026-11-21', '/coaching/athletes', '/coaching/athletes/12']) {
    assert.equal(reloadable('https://paceforge.fr' + path), true, path);
    assert.equal(refreshable('https://paceforge.fr' + path), false, path);
  }
  for (const url of ['https://paceforge.fr/coaching?refaire=1', 'https://paceforge.fr/coaching/cal/' + 'a'.repeat(43) + '.ics',
    'https://paceforge.fr/coaching/athletes/12/sessions', 'https://paceforge.fr/sante', 'https://paceforge.fr/activities',
    'https://paceforge.fr/simulator', 'https://paceforge.fr/settings', 'https://paceforge.fr.evil.test/coaching', 'not a url']) {
    assert.equal(reloadable(url), false, url);
  }
  assert.equal(refreshable('https://paceforge.fr/sante'), true);
  assert.equal(refreshable('https://paceforge.fr/activities?m=distance'), true);
});

test('only the Coaching calendar feed of this site is handed to the system as webcal://', () => {
  const token = 'Kikqo8SpCo7sO8Rb_X1p5RlGL5HtHVQXf-lMJmevX_A';
  const feed = `webcal://paceforge.fr/coaching/cal/${token}.ics`;
  assert.equal(calendarOrigin, 'webcal://paceforge.fr');
  assert.equal(calendarFeedUrl(feed), feed);
  for (const url of [
    `webcal://paceforge.fr.evil.test/coaching/cal/${token}.ics`, `webcal://evil.test/coaching/cal/${token}.ics`,
    `webcal://paceforge.fr@evil.test/coaching/cal/${token}.ics`, `webcal://user:pw@paceforge.fr/coaching/cal/${token}.ics`,
    `webcal://paceforge.fr:8443/coaching/cal/${token}.ics`, `webcal://PACEFORGE.FR/coaching/cal/${token}.ics`,
    `WEBCAL://paceforge.fr/coaching/cal/${token}.ics`, `webcal:paceforge.fr/coaching/cal/${token}.ics`,
    `webcal://paceforge.fr/coaching/cal/${token}.ics?x=1`, `webcal://paceforge.fr/coaching/cal/${token}.ics#x`,
    `webcal://paceforge.fr/coaching/cal/${token}.ics/`, `webcal://paceforge.fr/coaching/cal/${token}.ics\n`,
    `webcal://paceforge.fr/coaching/cal/${token}`, `webcal://paceforge.fr/coaching/cal/../../settings.ics`,
    `webcal://paceforge.fr/coaching/cal/a/${token}.ics`, `webcal://paceforge.fr/coaching/cal/short.ics`,
    `webcal://paceforge.fr/coaching/cal/${'a'.repeat(129)}.ics`, `webcal://paceforge.fr/coaching/cal/${token}%2e.ics`,
    `webcal://paceforge.fr/settings`, `webcal://paceforge.fr/`, 'webcal://paceforge.fr',
    `webcals://paceforge.fr/coaching/cal/${token}.ics`, `https://paceforge.fr/coaching/cal/${token}.ics`,
    `http://paceforge.fr/coaching/cal/${token}.ics`, `tel:+33123456789`, `mailto:a@b.fr`, 'sms:1', 'itms-apps://x',
    `paceforge://coaching/cal/${token}.ics`, `javascript:location='${feed}'`, '',
  ]) assert.equal(calendarFeedUrl(url), null, url);
  assert.equal(calendarFeedUrl(undefined as unknown as string), null);
});

test('the shell announces the native Coaching tab to this site only', () => {
  const page = (origin: string) => {
    const classes = new Set<string>(), styles: { id: string }[] = [];
    const window: Record<string, unknown> = { ReactNativeWebView: { postMessage() {} } };
    const document = { documentElement: { classList: { add: (c: string) => classes.add(c), contains: () => false } },
      getElementById: (id: string) => styles.find(s => s.id === id) || null, createElement: () => ({ id: '' }),
      head: { appendChild: (s: { id: string }) => styles.push(s) } };
    class MutationObserver { observe() {} }
    vm.runInNewContext(appShellScript, { window, document, location: { origin }, MutationObserver, JSON });
    return { classes, window };
  };
  const site = page('https://paceforge.fr');
  assert.equal(site.window.paceforgeNativeCoaching, 1);
  assert.ok(site.classes.has('pf-native-coaching') && site.classes.has('pf-native-refresh'));
  assert.equal(site.window.paceforgeNativeAgenda, 1);
  assert.ok(site.classes.has('pf-native-agenda'));
  const other = page('https://example.org');
  assert.equal(other.window.paceforgeNativeCoaching, undefined);
  assert.equal(other.window.paceforgeNativeAgenda, undefined);
  assert.equal(other.classes.size, 0);
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

test('an older web Tabs object safely falls back and still acknowledges the save barrier', async () => {
  const messages: Record<string, unknown>[] = [];
  let navigations = 0;
  const context = { location: { href: 'https://paceforge.fr/sante' }, window: {
    paceforgeTabs: { navigate: async () => { navigations++; return true; } },
    ReactNativeWebView: { postMessage: (raw: string) => messages.push(JSON.parse(raw)) },
  } };
  vm.runInNewContext(tabNavigationScript('https://paceforge.fr/activities', 'compat'), context);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(navigations, 0);
  assert.equal(messages[0].status, 'fallback');
  assert.doesNotThrow(() => vm.runInNewContext('window.paceforgeTabs?.cancel?.(); true;', context));
  vm.runInNewContext(prepareNavigationScript('save-compat'), context);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(messages[1].ok, true);
  assert.equal(messages[1].tabs_ready, false);
});

test('« Mon agenda »: only the site\'s agenda sheet with a ticket opens the system sheet; its return is two known codes', () => {
  const ticket = 'eyJ1Ijo0MiwicCI6ImFnZW5kYS1nb29nbGUifQ.ZZ_x-1.abcDEF123';
  assert.deepEqual(agendaSheetUrl(`https://paceforge.fr/mobile/oauth/agenda/google#${ticket}`),
    { provider: 'google', url: `https://paceforge.fr/mobile/oauth/agenda/google#${ticket}` });
  assert.equal(agendaSheetUrl(`https://paceforge.fr/mobile/oauth/agenda/microsoft#${ticket}`)?.provider, 'microsoft');
  for (const url of [
    'https://paceforge.fr/mobile/oauth/agenda/google',  // no ticket
    `https://paceforge.fr/mobile/oauth/agenda/apple#${ticket}`,
    `https://paceforge.fr/mobile/oauth/agenda/google?next=x#${ticket}`,
    `https://paceforge.fr/mobile/oauth/agenda/google/confirm#${ticket}`,
    `https://paceforge.fr/mobile/oauth/strava#${ticket}`,
    `https://paceforge.fr.evil.test/mobile/oauth/agenda/google#${ticket}`,
    `https://accounts.google.com/mobile/oauth/agenda/google#${ticket}`,
    'https://paceforge.fr/mobile/oauth/agenda/google#<script>alert(1)</script>',
  ]) assert.equal(agendaSheetUrl(url), null, url);
  const base = 'https://paceforge.fr/coaching?outil=agenda&connexion=google&resultat=';
  assert.equal(agendaReturnUrl('google', `${agendaScheme}?connexion=google&resultat=ok`), `${base}ok#coach-tool-agenda`);
  assert.equal(agendaReturnUrl('google', `${agendaScheme}?connexion=google&resultat=organisation`), `${base}organisation#coach-tool-agenda`);
  assert.equal(agendaReturnUrl('google', null), `${base}annule#coach-tool-agenda`, 'the sheet closed by hand');
  assert.equal(agendaReturnUrl('google', `${agendaScheme}?connexion=google&resultat=<b>`), `${base}annule#coach-tool-agenda`);
  assert.equal(agendaReturnUrl('google', `${agendaScheme}?connexion=microsoft&resultat=ok`), `${base}annule#coach-tool-agenda`);
  assert.equal(agendaReturnUrl('google', 'paceforge://devices?connexion=google&resultat=ok'), `${base}annule#coach-tool-agenda`);
  assert.equal(agendaReturnUrl('microsoft', `${agendaScheme}?connexion=microsoft&resultat=panne`),
    'https://paceforge.fr/coaching?outil=agenda&connexion=microsoft&resultat=panne#coach-tool-agenda');
});
