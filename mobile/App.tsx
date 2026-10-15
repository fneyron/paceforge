import React, { useEffect, useEffectEvent, useRef, useState } from 'react';
import { ActivityIndicator, AppState, Linking, Platform, Pressable, StyleSheet, Text, useColorScheme, useWindowDimensions, View } from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { StatusBar } from 'expo-status-bar';
import * as Crypto from 'expo-crypto';
import { WebView, WebViewMessageEvent } from 'react-native-webview';
import { api, ApiError, authorized, Credentials, ORIGIN, rotateToken, rotationDue, trusted } from './src/api';
import { authorize, collect, collectSleep } from './src/health';
import { Pair } from './src/types';
import { due, fullDue, reportText, syncIssue, SyncReport, syncHealth, Trigger } from './src/sync';
import { KEYS, KeychainLocked, secure } from './src/secure';
import { backgroundBlock, backgroundPending, blockBackgroundSync, disableBackgroundSync, enableBackgroundSync,
  onBackgroundReport, resetBackgroundState, skipSent, syncExclusively } from './src/background';
import type { Block } from './src/background-engine';
import { appShellScript, destinations, isBridgeUrl, prepareNavigationScript, presentationScript, refreshable, sameScreen, screenUrl, Tab, tabForUrl, tabs } from './src/navigation';
import { Connections, Provider } from './src/connections';
import { acknowledge, inlineConnectionsScript, inlineSessionScript, Pending, retryBridgeScript, takeReply } from './src/bridge';
import { DeviceDetail, DevicesScreen } from './src/DevicesScreen';
import { Button, DeviceTheme } from './src/device-ui';
import { useOfflinePlans } from './src/useOfflinePlans';
import { OfflinePlansScreen } from './src/OfflinePlansScreen';

const random = () => Array.from(Crypto.getRandomBytes(32), b => b.toString(16).padStart(2, '0')).join('');

function Companion() {
  const systemDark = useColorScheme() === 'dark';
  const { fontScale } = useWindowDimensions();
  const [leaving, setLeaving] = useState(false);
  const nextNavigation = useRef<{ nonce: string; from: string; go: () => void; timer: ReturnType<typeof setTimeout> } | null>(null);
  const retryNavigation = useRef<(() => void) | null>(null);
  const tabLocations = useRef<Record<string, string>>({});
  const [tab, setTab] = useState<Tab>('health');
  const [url, setUrl] = useState(ORIGIN + '/sante');
  const [pair, setPair] = useState<Pair | null>(null);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pageRefreshing, setPageRefreshing] = useState(false);
  const [userRefreshing, setUserRefreshing] = useState(false);
  const [refreshIssue, setRefreshIssue] = useState('');
  const [healthIssue, setHealthIssue] = useState('');
  const [dark, setDark] = useState(systemDark);
  const [showWeb, setShowWeb] = useState(false);
  const [bridgeDocument, setBridgeDocument] = useState(false);
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');
  const [loadError, setLoadError] = useState('');
  const [last, setLast] = useState('');
  const [report, setReport] = useState<SyncReport | null>(null);
  const [automatic, setAutomatic] = useState(false);
  const [detail, setDetail] = useState<DeviceDetail>(null);
  const [connections, setConnections] = useState<Connections | null>(null);
  const [needsReconnect, setNeedsReconnect] = useState(false);
  // Web logout or another account in the web session: no background sync until confirmed.
  const [block, setBlock] = useState<Block>('');
  // A background launch (HealthKit, periodic task) mounts this screen without showing it:
  // the WebView would load Santé for nobody. It mounts on the first return to the foreground.
  const [webStarted, setWebStarted] = useState(AppState.currentState !== 'background');
  const stored = useRef(false);
  const storedJob = useRef<Promise<void>>(Promise.resolve());
  const reconciled = useRef(false);
  const rotationRefused = useRef(false);
  const syncTrigger = useRef<Trigger>('manual');
  const externalReturn = useRef(false);
  const web = useRef<WebView>(null);
  const offline = useOfflinePlans(script => web.current?.injectJavaScript(script), random);
  const pending = useRef<Pending | null>(null);
  const lock = useRef(false);
  const lastAttempt = useRef(0);
  const syncFull = useRef(true);
  const documentReady = useRef(false);
  const pageLoadFailed = useRef(false);
  const inlineSync = useRef(false);
  const refreshSources = useRef(false);
  const recoveringProcess = useRef(false);
  const cloudRefresh = useRef(false);
  const deferredRefresh = useRef<boolean | null>(null);
  const watchdog = useRef<ReturnType<typeof setTimeout> | null>(null);
  const webLocation = useRef(ORIGIN + '/sante');
  const documentUrl = useRef(ORIGIN + '/sante');
  const navigationId = useRef(0);
  const recoveringBridge = useRef(false);
  const syncReturn = useRef<{ tab: Tab; url: string; showWeb: boolean } | null>(null);

  // A locked phone (background launch) cannot read the keychain: this is never « not
  // paired ». The read is retried on the first return to the foreground.
  const readStored = useEffectEvent(async () => {
    try {
      await secure.migrate().catch(e => { if (e instanceof KeychainLocked) throw e; });
      const [saved, time, auto, receipt, blocked] = await Promise.all([secure.recover(KEYS.pair), secure.get(KEYS.last),
        secure.get(KEYS.auto), secure.get(KEYS.sync), backgroundBlock()]);
      const account = saved ? JSON.parse(saved) as Pair : null;
      const previous = receipt ? JSON.parse(receipt) as SyncReport : null;
      stored.current = true;
      setPair(account); setLast(time || ''); setAutomatic(auto === 'true'); setBlock(blocked);
      if (previous?.userId === account?.user_id) { setReport(previous); setNeedsReconnect(!!previous?.reconnect); }
    } catch (e) {
      if (e instanceof KeychainLocked) return;
      stored.current = true;
      setError('Impossible de lire l’association. Reconnecte le téléphone.');
    }
    setReady(true);
  });
  // Serialized: a request during a read (the user's tap during a background launch)
  // waits for it, then reads again only if that read was refused.
  const loadStored = useEffectEvent(() => {
    storedJob.current = storedJob.current.then(() => stored.current ? undefined : readStored());
    return storedJob.current;
  });
  // Background runs keep the receipt up to date while the app is away.
  const rereadReceipt = useEffectEvent(async () => {
    if (lock.current || !pair) return;
    try {
      const [time, receipt] = await Promise.all([secure.get(KEYS.last), secure.get(KEYS.sync)]);
      const previous = receipt ? JSON.parse(receipt) as SyncReport : null;
      if (lock.current || previous?.userId !== pair.user_id) return;
      setReport(previous); setLast(time || '');
      if (previous.reconnect) setNeedsReconnect(true);
    } catch { /* the receipt on screen stays */ }
  });
  const backgroundReport = useEffectEvent((r: SyncReport) => {
    if (lock.current || !pair || r.userId !== pair.user_id) return;
    setReport(r); if (r.lastReceivedAt) setLast(r.lastReceivedAt);
    if (r.reconnect) setNeedsReconnect(true);
  });
  useEffect(() => onBackgroundReport(r => backgroundReport(r)), []);
  useEffect(() => {
    void loadStored();
    Linking.getInitialURL().then(u => { if (u) onAppLink(u); });
    const subscription = Linking.addEventListener('url', e => onAppLink(e.url));
    return () => { subscription.remove(); if (watchdog.current) clearTimeout(watchdog.current); if (nextNavigation.current) clearTimeout(nextNavigation.current.timer); };
  }, []);

  useEffect(() => {
    web.current?.injectJavaScript(presentationScript(fontScale, Platform.OS === 'ios' ? 'Apple Santé' : 'Health Connect'));
  }, [fontScale]);

  const onAppLink = useEffectEvent((link: string) => {
    if (link === 'paceforge://privacy') openSettings('/mobile/privacy');
    if (link === 'paceforge://devices') { externalReturn.current = false; openTab('devices'); }
    if (link === 'paceforge://health') openHealth();
  });

  function finish() { lock.current = false; pending.current = null; setBusy(false); if (watchdog.current) clearTimeout(watchdog.current); }
  function loadScreen(destination: string) {
    if (documentReady.current && sameScreen(destination, documentUrl.current)) return;
    // WKWebView's actual document may differ from its last React source prop.
    // A fresh URL guarantees a real navigation, even when returning to that prop.
    const target = new URL(screenUrl(destination));
    target.searchParams.set('app_nav', `${Date.now()}-${++navigationId.current}`);
    documentReady.current = false;
    setLoadError('');
    setUrl(target.href);
  }
  function refreshPage(sources = false) {
    web.current?.injectJavaScript(`if (window.paceforgeRefresh && ['/sante','/activities'].includes(location.pathname)) window.paceforgeRefresh.refresh(${sources}); else { const u = new URL(${JSON.stringify(screenUrl(webLocation.current))}); u.searchParams.set('app_nav', Date.now()); location.replace(u.href); } true;`);
  }
  function restoreSync(refresh = false) {
    const back = syncReturn.current;
    syncReturn.current = null;
    if (inlineSync.current) {
      inlineSync.current = false;
      if (isBridgeUrl(documentUrl.current)) {
        if (refresh && refreshable(webLocation.current)) deferredRefresh.current = refreshSources.current;
        loadScreen(webLocation.current);
      } else if (refresh && refreshable(webLocation.current)) {
        if (documentReady.current) refreshPage(refreshSources.current);
        else deferredRefresh.current = refreshSources.current;
      }
      return;
    }
    if (back || isBridgeUrl(url) || isBridgeUrl(documentUrl.current)) loadScreen(back?.url || webLocation.current);
    if (refresh && refreshSources.current && back) deferredRefresh.current = true;
    if (back) { setTab(back.tab); setShowWeb(back.showWeb); }
  }
  function recoverBridge() {
    if (pending.current || lock.current || recoveringBridge.current) return;
    recoveringBridge.current = true;
    loadScreen(webLocation.current);
  }
  function fail(e: unknown) {
    const connectionCheck = pending.current?.kind === 'connections';
    if (e instanceof ApiError && e.status === 401) setNeedsReconnect(true);
    // A timeout must leave an actionable screen, never the hidden bridge document.
    if (pending.current) { web.current?.stopLoading(); setShowWeb(false); }
    restoreSync();
    const message = e instanceof Error ? e.message : 'La synchronisation a échoué. Réessaie.';
    setError(message); if (!connectionCheck) setHealthIssue(message); setUserRefreshing(false); finish();
  }
  function start() { if (lock.current || cloudRefresh.current || !ready) return false; lock.current = true; setBusy(true); setError(''); return true; }
  function guard() {
    if (watchdog.current) clearTimeout(watchdog.current);
    watchdog.current = setTimeout(() => { if (pending.current) fail(new Error('La connexion au compte prend trop de temps. Vérifie le réseau ou connecte-toi, puis réessaie.')); }, 60000);
  }
  async function associate() {
    if (!start()) return;
    try {
      const verifier = random(); const challenge = await Crypto.digestStringAsync(Crypto.CryptoDigestAlgorithm.SHA256, verifier);
      const target = `${ORIGIN}/mobile/connect?challenge=${challenge}&platform=${Platform.OS}`;
      syncReturn.current = null;
      pending.current = { kind: 'pair', verifier, challenge, url: target };
      setUrl(target); setShowWeb(true); setStatus('Confirme le compte auquel envoyer les mesures.'); guard();
    } catch (e) { fail(e); }
  }
  function sync(quiet = false, consent = false, forceSources = false) {
    if (!pair || !start()) return;
    setHealthIssue('');
    syncTrigger.current = quiet && !forceSources ? 'auto' : 'manual';
    syncReturn.current = quiet ? { tab, url: screenUrl(webLocation.current), showWeb } : null;
    const nonce = random(), target = `${ORIGIN}/mobile/session?nonce=${nonce}`;
    inlineSync.current = quiet && documentReady.current && refreshable(documentUrl.current);
    refreshSources.current = forceSources;
    pending.current = { kind: 'check', nonce, url: target, consent,
      ...(inlineSync.current ? { pageUrl: webLocation.current } : {}) };
    // A pull or a manual sync reads the 7-day overlap; the history only when its reread is due.
    syncFull.current = fullDue(report);
    lastAttempt.current = Date.now(); setStatus('Vérification du compte…');
    if (inlineSync.current) web.current?.injectJavaScript(inlineSessionScript(pending.current));
    else { setTab('devices'); setDetail('health'); setShowWeb(false); setUrl(target); }
    guard();
  }
  function refresh() {
    if (lock.current || cloudRefresh.current || !documentReady.current) return;
    setUserRefreshing(true); setRefreshIssue(''); setHealthIssue(''); setError('');
    if (pair && !needsReconnect) sync(true, false, true);
    else {
      if (pair && needsReconnect) setHealthIssue('Reconnecte le téléphone dans Réglages.');
      refreshPage(true);
    }
  }
  async function upload(account: Pair, consent = false) {
    pending.current = null; if (watchdog.current) clearTimeout(watchdog.current);
    if (!inlineSync.current) { setShowWeb(false); setTab('devices'); setDetail('health'); }
    let refresh = false;
    try {
      // One sync at a time, also with a background run (another runtime on Android).
      await syncExclusively(async () => {
        if (consent) { setStatus('Choisis les données à partager…'); await authorize(automatic && Platform.OS === 'android'); }
        const full = consent || syncFull.current;
        let credentials: Pair = account;
        const keep = async (next: Credentials) => {
          credentials = { ...credentials, token: next.token, prev_token: next.prev_token };
          await secure.rewrite(KEYS.pair, JSON.stringify(credentials)); setPair(credentials);
        };
        // Days and nights the server already confirmed (here or in a background run) are not sent again.
        const skip = await skipSent(account.user_id);
        const result = await syncHealth({
          userId: account.user_id, full, previous: report, trigger: consent ? 'manual' : syncTrigger.current,
          daily: () => collect(() => setStatus('Lecture des mesures quotidiennes…'), full ? 30 : 7),
          sleep: () => collectSleep(() => setStatus('Lecture du sommeil…'), full ? 60 : 7),
          send: (path, body, meta) => authorized(credentials,
            token => api(path, 'POST', body, token, { headers: { 'X-PaceForge-Read-At': meta.readAt } }), keep),
          progress: setStatus,
          save: async receipt => {
            await secure.set(KEYS.sync, JSON.stringify(receipt)); setReport(receipt);
            if (receipt.lastReceivedAt) {
              await secure.set(KEYS.last, receipt.lastReceivedAt); setLast(receipt.lastReceivedAt);
            }
          },
          unsent: skip.unsent, sent: skip.sent,
        });
        await skip.commit().catch(() => { /* sent again next time */ });
        setNeedsReconnect(result.reconnect); setStatus(reportText(result));
        refresh = result.received > 0 || refreshSources.current;
        setHealthIssue(syncIssue(result, Platform.OS === 'ios' ? 'Apple Santé' : 'Health Connect'));
        if (!result.reconnect && !rotationRefused.current && rotationDue(credentials.rotated_at)) {
          // Foreground only. The old token stays valid while still used (7 days after its last use),
          // until the new one is first used.
          await rotateToken(credentials, async next => { await secure.rewrite(KEYS.pair, JSON.stringify(next)); setPair(next); })
            .catch(e => { if (e instanceof ApiError && [404, 405].includes(e.status)) rotationRefused.current = true; });
        }
      });
    } catch (e) { fail(e); } finally {
      if (!refresh || !refreshable(webLocation.current)) setUserRefreshing(false);
      restoreSync(refresh); finish();
    }
  }
  async function message(event: WebViewMessageEvent) {
    if (offline.receive(event.nativeEvent.data, event.nativeEvent.url)) return;
    if (trusted(event.nativeEvent.url)) {
      try {
        const data = JSON.parse(event.nativeEvent.data);
        const navigation = nextNavigation.current;
        if (data?.type === 'navigation-ready') {
          if (!navigation || data.nonce !== navigation.nonce || event.nativeEvent.url !== navigation.from) return;
          clearTimeout(navigation.timer); nextNavigation.current = null; setLeaving(false);
          if (data.ok === true) navigation.go();
          else { retryNavigation.current = navigation.go; setError('Ton plan n’a pas été enregistré. Réessaie avant de quitter cet écran.'); }
          return;
        }
        if (data?.type === 'appearance' && typeof data.dark === 'boolean') { setDark(data.dark); return; }
      } catch { /* Other messages are validated by the account bridge below. */ }
    }
    if (refreshable(event.nativeEvent.url)) {
      try {
        const data = JSON.parse(event.nativeEvent.data);
        if (data?.type === 'refresh-state' && typeof data.active === 'boolean') {
          cloudRefresh.current = data.active; setPageRefreshing(data.active);
          if (!data.active) setUserRefreshing(false);
          return;
        }
        if (data?.type === 'refresh-notice' && typeof data.message === 'string') {
          setRefreshIssue(data.message); return;
        }
      } catch { /* The nonce-bound account bridge validates its own messages. */ }
    }
    const reply = takeReply(pending, event.nativeEvent.data, event.nativeEvent.url);
    if (!reply) return;
    if (watchdog.current) clearTimeout(watchdog.current);
    web.current?.injectJavaScript(acknowledge(reply.task));
    try {
      if (reply.kind === 'login') {
        pending.current = { ...reply.task, pageUrl: undefined }; inlineSync.current = false;
        setTab('devices'); setShowWeb(true); setUrl(reply.task.url); guard(); return;
      }
      if (reply.kind === 'unavailable') {
        if (reply.task.kind === 'connections') {
          setError('Impossible de vérifier les connexions. Vérifie le réseau puis réessaie.'); finish(); return;
        }
        throw new Error('Impossible de vérifier le compte. Vérifie le réseau puis réessaie.');
      }
      if (!inlineSync.current) setShowWeb(false);
      if (reply.kind === 'connections') {
        setConnections(reply.snapshot); setShowWeb(false); setStatus('');
        if (pair) confirmAccount(reply.snapshot.user_id === pair.user_id);
        if (reply.task.pageUrl && offline.library.userId !== reply.snapshot.user_id) {
          // A session may have changed while Settings covered the document.
          // Clear the old account's copies and replace its still-mounted page.
          offline.forget(); tabLocations.current = {}; webLocation.current = ORIGIN + '/sante';
          documentReady.current = false; loadScreen(webLocation.current);
        }
        restoreSync(); finish();
        return;
      }
      if (reply.kind === 'pair') {
        const account: Pair = { ...await api('/api/mobile/exchange', 'POST', { code: reply.code, verifier: reply.task.verifier }),
          rotated_at: new Date().toISOString() };
        const sameAccount = pair?.user_id === account.user_id;
        await secure.rewrite(KEYS.pair, JSON.stringify(account)); setPair(account); setShowWeb(false); setTab('devices');
        await secure.remove(KEYS.last); setLast('');
        await secure.remove(KEYS.sync); setReport(null);
        // Reconnecting the same account (refused token) keeps the automatic update choice.
        if (!sameAccount) { await secure.remove(KEYS.auto); setAutomatic(false); }
        await resetBackgroundState(account.user_id); setBlock('');
        setNeedsReconnect(false);
        await upload(account, true);
      } else if (reply.kind === 'check') {
        if (!pair || reply.userId !== pair.user_id) {
          setNeedsReconnect(true); confirmAccount(false);
          throw new Error('Le compte affiché a changé. Utilise « Connecter » pour confirmer le compte qui recevra tes mesures.');
        }
        confirmAccount(true);
        await upload(pair, reply.task.consent);
      }
    } catch (e) { fail(e); }
  }
  /** The web session matches the paired account (or not): background sync follows. */
  function confirmAccount(same: boolean) {
    const next: Block = same ? '' : 'mismatch';
    if (same ? !block : block === 'mismatch') return;
    setBlock(next); void blockBackgroundSync(next).catch(() => {});
  }
  function connectHealth() {
    setDetail('health');
    if (!pair || needsReconnect || (connections && connections.user_id !== pair.user_id)) void associate();
    else sync(false, true);
  }
  function refreshConnections() {
    if (!start()) return;
    const nonce = random(), target = `${ORIGIN}/mobile/connections?nonce=${nonce}`;
    setConnections(null); setStatus('Vérification des connexions…');
    const inline = documentReady.current && trusted(documentUrl.current) && !isBridgeUrl(documentUrl.current);
    pending.current = { kind: 'connections', nonce, url: target, ...(inline ? { pageUrl: documentUrl.current } : {}) };
    if (inline) web.current?.injectJavaScript(inlineConnectionsScript(pending.current));
    else setUrl(target);
    guard();
  }
  function openProvider(provider: Provider) {
    if (lock.current) return;
    if (provider === 'garmin') { openSettings('/connections/garmin'); return; }
    externalReturn.current = true;
    setShowWeb(false); setTab('devices'); setDetail(null); setError('');
    Linking.openURL(`${ORIGIN}/connections/${provider}`).catch(fail);
  }
  async function disconnect() {
    if (!pair || !start()) return;
    try {
      await api('/api/mobile/device', 'DELETE', undefined, pair.token);
      await secure.erase(KEYS.pair); await secure.remove(KEYS.auto); await secure.remove(KEYS.last); await secure.remove(KEYS.sync);
      await disableBackgroundSync().catch(() => {}); await secure.remove(KEYS.background).catch(() => {});
      setPair(null); setAutomatic(false); setLast(''); setReport(null); setHealthIssue(''); setNeedsReconnect(false);
      setStatus('Téléphone dissocié. L’historique reçu reste dans ton compte.');
    } catch (e) { fail(e); } finally { finish(); }
  }
  const onForeground = useEffectEvent(() => {
    // Never during a background launch: the screen is not shown, nothing to verify.
    if (AppState.currentState !== 'active' || lock.current || !ready || offline.view !== null) return;
    if (externalReturn.current) {
      externalReturn.current = false; setTab('devices'); setShowWeb(false); setDetail(null); refreshConnections();
    } else if (tab !== 'devices' && automatic && pair && !needsReconnect && documentReady.current && refreshable(documentUrl.current) &&
      (due(report, lastAttempt.current) || (backgroundPending() && Date.now() - lastAttempt.current >= 60000))) sync(true);
  });
  // Background sync follows the toggle, the pairing and the account. Registered from
  // the foreground only (iOS: after the HealthKit permission sheet).
  const wantBackground = !!pair && automatic && !needsReconnect && !block && !(connections && pair && connections.user_id !== pair.user_id);
  const reconcile = useEffectEvent(() => {
    if (!ready || AppState.currentState !== 'active') return;
    reconciled.current = true;
    void (wantBackground ? enableBackgroundSync() : disableBackgroundSync()).catch(() => {});
  });
  useEffect(() => {
    const active = () => {
      setWebStarted(true);
      if (!stored.current) void loadStored();
      else void rereadReceipt();
      if (!reconciled.current) reconcile();
      onForeground();
    };
    const subscription = AppState.addEventListener('change', state => { if (state === 'active') active(); });
    // Already active before this listener existed (a tap during a HealthKit cold launch):
    // no change event will come, and the WebView would stay unmounted.
    if (AppState.currentState === 'active') active();
    return () => subscription.remove();
  }, []);
  useEffect(() => { reconcile(); }, [ready, wantBackground]);
  useEffect(() => {
    if (ready) onForeground();
  }, [ready, tab]);

  function openTab(next: Tab) {
    if (pending.current?.kind === 'connections' && pending.current.pageUrl) {
      // This read has no side effects. A slow connection must not trap the user
      // in Settings, and a late reply must not navigate them back to it.
      finish();
    }
    if (lock.current && (!inlineSync.current || pending.current || next === 'devices')) return;
    if (offline.busy) return;
    if (next === tab && next !== 'devices' && !isBridgeUrl(documentUrl.current) && offline.view === null) return;
    afterSave(() => {
      offline.setView(null);
      setTab(next); setShowWeb(false); setDetail(null); setError(''); setStatus('');
      if (next !== 'devices') loadScreen(tabLocations.current[next] || ORIGIN + destinations[next]);
      else refreshConnections();
    });
  }
  function afterSave(go: () => void) {
    if (nextNavigation.current) return;
    retryNavigation.current = null; setError('');
    if (!webVisible || !documentReady.current || isBridgeUrl(documentUrl.current)) { go(); return; }
    const nonce = random();
    const timer = setTimeout(() => {
      nextNavigation.current = null; setLeaving(false);
      retryNavigation.current = go;
      setError('La sauvegarde prend trop de temps. Vérifie ta connexion, puis réessaie.');
    }, 20000);
    nextNavigation.current = { nonce, from: documentUrl.current, go, timer }; setLeaving(true);
    web.current?.injectJavaScript(prepareNavigationScript(nonce));
  }
  function openHealth() {
    if (lock.current) return;
    afterSave(() => { setTab('devices'); setShowWeb(false); setDetail('health'); setError(''); setStatus(''); });
  }
  function openSettings(path = '/settings') { setTab('devices'); setUrl(ORIGIN + path); setShowWeb(true); }
  function openSyncDetails() {
    if (healthIssue && !refreshIssue && pair && !needsReconnect) {
      // The phone receipt is local: reading it needs neither a connection check
      // nor another bridge navigation, especially after a network failure.
      setTab('devices'); setShowWeb(false); setDetail('health'); setStatus(''); setError('');
    } else openTab('devices');
  }
  function pageFailed(message: string) {
    pageLoadFailed.current = true;
    documentReady.current = false;
    if (pending.current) fail(new Error(message));
    else setLoadError(message);
  }
  const webVisible = (tab !== 'devices' || showWeb) && offline.view === null;
  const concealBridge = bridgeDocument && pending.current?.kind !== 'pair';
  const checkingConnectionsInline = pending.current?.kind === 'connections' && !!pending.current.pageUrl;
  const navigationLocked = leaving || offline.busy || (busy && !checkingConnectionsInline && (!inlineSync.current || pending.current !== null));
  const visibleIssue = retryNavigation.current ? error : [healthIssue, refreshIssue].filter(Boolean).join('\n') || error;
  const night = dark;
  const textScaleScript = presentationScript(fontScale, Platform.OS === 'ios' ? 'Apple Santé' : 'Health Connect');
  return <DeviceTheme.Provider value={dark}><SafeAreaView style={[styles.root, night && styles.darkSurface]}>
    <StatusBar style={night ? 'light' : 'dark'} />
    <View style={[styles.header, night && styles.darkSurface]}><View style={styles.heading}><Text style={[styles.brand, night && styles.darkInk]}
      accessibilityActions={webVisible && refreshable(webLocation.current) ? [{ name: 'refresh', label: 'Actualiser les données' }] : []}
      onAccessibilityAction={event => { if (event.nativeEvent.actionName === 'refresh') refresh(); }}>PaceForge</Text></View>
      <Pressable accessibilityRole="button" accessibilityLabel="Réglages et connexions" accessibilityState={{ disabled: busy || leaving || offline.busy }} disabled={busy || leaving || offline.busy} onPress={() => openTab('devices')} style={styles.settings}><Text style={[styles.settingsText, night && styles.darkAccent]}>Réglages</Text></Pressable></View>
    {webVisible && refreshable(webLocation.current) && userRefreshing && (busy || pageRefreshing) ? <View style={[styles.syncStatus, night && styles.darkSurface]} accessibilityLiveRegion="polite">
      <ActivityIndicator size="small" color={night ? '#a6a6ad' : '#62736d'} /><Text style={[styles.syncText, night && styles.darkMuted]}>Actualisation…</Text>
    </View> : null}
    {webVisible && visibleIssue && !busy && !leaving && !pageRefreshing ? <Pressable style={[styles.syncNotice, night && styles.darkSurface]} accessibilityRole="button"
      accessibilityLabel={`${visibleIssue} ${retryNavigation.current ? 'Réessayer la sauvegarde.' : 'Ouvrir les connexions.'}`}
      onPress={() => retryNavigation.current ? afterSave(retryNavigation.current) : openSyncDetails()}><Text style={[styles.syncText, night && styles.darkMuted]}>{visibleIssue}</Text><Text style={[styles.noticeLink, night && styles.darkAccent]}>{retryNavigation.current ? 'Réessayer' : 'Détails ›'}</Text></Pressable> : null}
    <View style={styles.content}>
    {offline.view !== null && <OfflinePlansScreen plans={offline.library.plans} selected={offline.view} select={offline.setView}
      notice={offline.notice} remove={offline.remove} close={() => { offline.setView(null); setTab('courses'); loadScreen(tabLocations.current.courses || ORIGIN + '/simulator'); }} />}
    {tab === 'devices' && !showWeb && offline.view === null && <DevicesScreen detail={detail} select={setDetail} pair={pair} last={last} report={report} automatic={automatic}
      busy={busy} ready={ready} status={status} error={error || (detail === 'health' ? '' : visibleIssue)} connections={connections} needsReconnect={needsReconnect}
      connectHealth={connectHealth} sync={() => sync()} disconnect={disconnect}
      auto={v => {
        setAutomatic(v); secure.set(KEYS.auto, String(v)).catch(fail);
        // Android: reading in the background needs its own Health Connect permission.
        if (v && Platform.OS === 'android') void authorize(true).catch(() => {}).finally(() => reconcile());
      }}
      provider={openProvider} refresh={refreshConnections} settings={openSettings} health={() => openTab('health')} />}
    {webVisible && tab === 'courses' && (offline.busy || offline.notice) && <View style={[styles.syncStatus, night && styles.darkSurface]} accessibilityLiveRegion="polite">
      {offline.busy && <ActivityIndicator size="small" />}<Text style={[styles.syncText, night && styles.darkMuted]}>{offline.busy ? 'Enregistrement du plan sur le téléphone…' : offline.notice}</Text>
    </View>}
    {webVisible && loadError ? <View style={styles.unavailable}>
      <Text accessibilityRole="header" style={[styles.brand, night && styles.darkInk]}>Écran indisponible</Text>
      <Text style={[styles.syncText, night && styles.darkMuted]}>{loadError}</Text>
      <Button text="Plans hors connexion" secondary onPress={() => { setTab('courses'); offline.setView('list'); }} />
      <Button text="Réessayer" onPress={() => { setLoadError(''); recoveringProcess.current = true; web.current?.reload(); }} />
    </View> : webVisible && concealBridge ? <View style={styles.returning} accessibilityLabel="Chargement de PaceForge"><ActivityIndicator color="#0b5f6e" /></View> : null}
    <View style={[styles.web, (!webVisible || concealBridge || !!loadError) && styles.hidden]} pointerEvents={webVisible && !concealBridge && !loadError ? 'auto' : 'none'} accessibilityElementsHidden={!webVisible || concealBridge || !!loadError} importantForAccessibility={webVisible && !concealBridge && !loadError ? 'auto' : 'no-hide-descendants'}>
      {showWeb && <Button text="Retour aux connexions" disabled={busy && !pending.current} onPress={() => { if (pending.current) { web.current?.stopLoading(); restoreSync(); finish(); setShowWeb(false); setTab('devices'); setDetail(null); setStatus(''); } else openTab('devices'); }} secondary />}
      {webStarted && <WebView ref={web} source={{ uri: url }} style={styles.web} sharedCookiesEnabled thirdPartyCookiesEnabled={false}
        pullToRefreshEnabled={Platform.OS === 'ios' && webVisible && !concealBridge && refreshable(documentUrl.current) && !busy && !pageRefreshing}
        bounces decelerationRate="normal" automaticallyAdjustContentInsets={false} contentInsetAdjustmentBehavior="never"
        injectedJavaScriptBeforeContentLoaded={appShellScript + textScaleScript} injectedJavaScript={appShellScript + textScaleScript}
        originWhitelist={['https://*', 'paceforge://*']} mixedContentMode="never" allowFileAccess={false} setSupportMultipleWindows
        onMessage={message} onError={() => pageFailed('Cet écran nécessite une connexion. Tes données déjà enregistrées restent conservées. Réessaie lorsque le réseau revient.')}
        onLoadStart={() => {
          documentReady.current = false; pageLoadFailed.current = false; setLoadError('');
          if (nextNavigation.current) { clearTimeout(nextNavigation.current.timer); nextNavigation.current = null; setLeaving(false); }
          if (retryNavigation.current) { retryNavigation.current = null; setError(''); }
        }}
        onLoadEnd={event => {
          if (pageLoadFailed.current) return;
          if (!isBridgeUrl(event.nativeEvent.url)) offline.loaded(event.nativeEvent.url);
          if (isBridgeUrl(event.nativeEvent.url)) {
            documentUrl.current = event.nativeEvent.url; documentReady.current = false; setBridgeDocument(true);
            if (pending.current) web.current?.injectJavaScript(retryBridgeScript);
            else recoverBridge();
            return;
          }
          documentReady.current = true;
          if (refreshable(event.nativeEvent.url)) {
            documentUrl.current = event.nativeEvent.url; setBridgeDocument(false); recoveringBridge.current = false;
            documentReady.current = true;
            if (!lock.current || inlineSync.current) webLocation.current = event.nativeEvent.url;
            if (deferredRefresh.current !== null) { const sources = deferredRefresh.current; deferredRefresh.current = null; refreshPage(sources); }
            else onForeground();
          }
        }}
        onContentProcessDidTerminate={() => {
          if (pending.current) fail(new Error('La vérification a été interrompue. Réessaie la synchronisation.'));
          else { recoveringProcess.current = true; web.current?.reload(); }
        }}
        onHttpError={event => { if ([url, documentUrl.current].includes(event.nativeEvent.url)) pageFailed(`PaceForge ne répond pas pour le moment (${event.nativeEvent.statusCode}). Réessaie dans quelques instants.`); }}
        onShouldStartLoadWithRequest={request => {
          if (trusted(request.url)) {
            if (isBridgeUrl(request.url) && (!pending.current || (pending.current.kind !== 'pair' && pending.current.pageUrl))) return false;
            if (request.navigationType === 'reload' && request.isTopFrame !== false && refreshable(request.url)) {
              if (recoveringProcess.current) recoveringProcess.current = false;
              else { refresh(); return false; }
            }
            const path = new URL(request.url).pathname;
            if (path === '/auth/logout') { offline.forget(); setBlock('logout'); void blockBackgroundSync('logout').catch(() => {}); }
            if (path === '/settings' && ['#connections', '#coros', '#garmin', '#strava'].includes(new URL(request.url).hash)) { openTab('devices'); return false; }
            if (['/auth/strava', '/coros/connect', '/setup'].includes(path)) {
              // Start AND finish OAuth in the same browser session.
              openProvider(path === '/coros/connect' ? 'coros' : 'strava');
              return false;
            }
            return true;
          }
          const saveOffline = /^paceforge:\/\/offline\/save\/(\d+)$/.exec(request.url);
          if (saveOffline) { afterSave(() => offline.save(Number(saveOffline[1]), documentUrl.current)); return false; }
          if (request.url === 'paceforge://offline') { afterSave(() => { setTab('courses'); offline.setView('list'); }); return false; }
          if (request.url === 'paceforge://devices') { openTab('devices'); return false; }
          if (request.url === 'paceforge://health') { openHealth(); return false; }
          if (request.url.startsWith('https://')) Linking.openURL(request.url).catch(() => setError('Impossible d’ouvrir ce lien.'));
          return false;
        }}
        onNavigationStateChange={nav => {
          if (!trusted(nav.url)) return;
          documentUrl.current = nav.url;
          if (nav.loading) {
            documentReady.current = false; cloudRefresh.current = false; setPageRefreshing(false);
          }
          const path = new URL(nav.url).pathname;
          if (pending.current && (path.startsWith('/auth/') || path === '/')) {
            setShowWeb(true); if (watchdog.current) clearTimeout(watchdog.current);
          }
          if (!nav.loading && pending.current && !(pending.current.kind !== 'pair' && pending.current.pageUrl) && (path === '/accueil' || path === '/sante' || path === '/activities')) {
            setUrl(pending.current.url + "#resume-" + Date.now()); guard();
          }
          // Bridge completion can arrive before the final navigation event.
          // Those hidden pages must never turn into a visible device detail.
          if (isBridgeUrl(nav.url)) {
            documentReady.current = false; setBridgeDocument(true);
            if (pending.current) guard();
            if (pending.current?.kind === 'check') setShowWeb(false);
            if (!nav.loading) recoverBridge();
            return;
          }
          if (!nav.loading) { setBridgeDocument(false); recoveringBridge.current = false; }
          if (!nav.loading && !pending.current && (!lock.current || inlineSync.current) && webVisible) {
            webLocation.current = nav.url;
            const next = tabForUrl(nav.url);
            if (next) {
              if (next !== 'devices') tabLocations.current[next] = nav.url;
              setTab(next); setShowWeb(next === 'devices');
            } else if (path.startsWith('/auth/')) tabLocations.current = {};
          }
        }}
      />}
    </View>
    </View>
    <View style={[styles.tabs, night && styles.darkSurface]}>
      {tabs.map(([key, label]) => <Pressable key={key} accessibilityRole="tab" accessibilityState={{ selected: tab === key, disabled: navigationLocked }} disabled={navigationLocked} onPress={() => openTab(key)} style={[styles.tab, tab === key && styles.active, night && tab === key && styles.darkActive]}><Text style={[styles.tabText, night && styles.darkMuted, tab === key && styles.activeText, night && tab === key && styles.darkAccent]}>{label}</Text></Pressable>)}
    </View>
  </SafeAreaView></DeviceTheme.Provider>;
}
export default function App() { return <SafeAreaProvider><Companion /></SafeAreaProvider>; }
const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: '#fff' }, header: { paddingHorizontal: 20, paddingVertical: 4, backgroundColor: '#fff', flexDirection: 'row', alignItems: 'center', gap: 8 },
  heading: { flex: 1 }, settings: { minHeight: 44, justifyContent: 'center', paddingHorizontal: 4 }, settingsText: { color: '#0b5f6e', fontSize: 13, fontWeight: '600' },
  brand: { color: '#0b1a1f', fontSize: 21, fontWeight: '700', letterSpacing: -0.65 },
  syncStatus: { minHeight: 32, paddingHorizontal: 20, paddingVertical: 5, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, backgroundColor: '#fff' },
  syncText: { color: '#62736d', fontSize: 12, lineHeight: 18, flexShrink: 1 },
  syncNotice: { minHeight: 44, paddingHorizontal: 20, paddingVertical: 8, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12, backgroundColor: '#f4f6f8' },
  noticeLink: { color: '#0b5f6e', fontSize: 12, fontWeight: '600' },
  content: { flex: 1 }, web: { flex: 1 }, returning: { flex: 1, alignItems: 'center', justifyContent: 'center' }, hidden: { position: 'absolute', top: 0, right: 0, bottom: 0, left: 0, opacity: 0 },
  unavailable: { flex: 1, padding: 24, justifyContent: 'center', gap: 16 },
  tabs: { flexDirection: 'row', paddingHorizontal: 16, gap: 12, backgroundColor: '#fff', borderTopWidth: StyleSheet.hairlineWidth, borderColor: '#e4ebed' },
  tab: { flex: 1, paddingVertical: 18, borderTopWidth: 2, borderTopColor: 'transparent' }, active: { borderTopColor: '#0b5f6e' },
  tabText: { fontSize: 12, textAlign: 'center', color: '#63636a', fontWeight: '500' }, activeText: { color: '#0b5f6e', fontWeight: '700' },
  darkSurface: { backgroundColor: '#18181b', borderColor: '#2c2c31' }, darkInk: { color: '#f4f4f6' },
  darkMuted: { color: '#a6a6ad' }, darkAccent: { color: '#5cc3d1' }, darkActive: { borderTopColor: '#5cc3d1' },
});
