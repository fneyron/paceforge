import React, { useEffect, useEffectEvent, useRef, useState } from 'react';
import { ActivityIndicator, AppState, Linking, Platform, Pressable, StyleSheet, Text, View } from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { StatusBar } from 'expo-status-bar';
import * as Crypto from 'expo-crypto';
import * as SecureStore from 'expo-secure-store';
import { WebView, WebViewMessageEvent } from 'react-native-webview';
import { api, ApiError, ORIGIN, trusted } from './src/api';
import { authorize, collect, collectSleep } from './src/health';
import { Pair } from './src/types';
import { due, fullDue, reportText, SyncReport, syncHealth } from './src/sync';
import { appShellScript, destinations, isBridgeUrl, refreshable, screenUrl, Tab, tabForUrl, tabs } from './src/navigation';
import { Connections, Provider } from './src/connections';
import { acknowledge, inlineSessionScript, Pending, retryBridgeScript, takeReply } from './src/bridge';
import { DeviceDetail, DevicesScreen } from './src/DevicesScreen';
import { Button } from './src/device-ui';

const KEY = 'paceforge.phone.v1';
const random = () => Array.from(Crypto.getRandomBytes(32), b => b.toString(16).padStart(2, '0')).join('');

function Companion() {
  const [tab, setTab] = useState<Tab>('health');
  const [url, setUrl] = useState(ORIGIN + '/sante');
  const [pair, setPair] = useState<Pair | null>(null);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pageRefreshing, setPageRefreshing] = useState(false);
  const [showWeb, setShowWeb] = useState(false);
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');
  const [last, setLast] = useState('');
  const [report, setReport] = useState<SyncReport | null>(null);
  const [automatic, setAutomatic] = useState(false);
  const [detail, setDetail] = useState<DeviceDetail>(null);
  const [connections, setConnections] = useState<Connections | null>(null);
  const [needsReconnect, setNeedsReconnect] = useState(false);
  const externalReturn = useRef(false);
  const web = useRef<WebView>(null);
  const pending = useRef<Pending | null>(null);
  const lock = useRef(false);
  const lastAttempt = useRef(0);
  const syncFull = useRef(true);
  const documentReady = useRef(false);
  const inlineSync = useRef(false);
  const refreshSources = useRef(false);
  const recoveringProcess = useRef(false);
  const cloudRefresh = useRef(false);
  const deferredRefresh = useRef<boolean | null>(null);
  const watchdog = useRef<ReturnType<typeof setTimeout> | null>(null);
  const webLocation = useRef(ORIGIN + '/sante');
  const syncReturn = useRef<{ tab: Tab; url: string; showWeb: boolean } | null>(null);

  useEffect(() => {
    Promise.all([SecureStore.getItemAsync(KEY), SecureStore.getItemAsync(KEY + '.last'), SecureStore.getItemAsync(KEY + '.auto'), SecureStore.getItemAsync(KEY + '.sync')])
      .then(([saved, time, auto, receipt]) => {
        const account = saved ? JSON.parse(saved) as Pair : null;
        const previous = receipt ? JSON.parse(receipt) as SyncReport : null;
        setPair(account); setLast(time || ''); setAutomatic(auto === 'true');
        if (previous?.userId === account?.user_id) { setReport(previous); setNeedsReconnect(!!previous?.reconnect); }
      })
      .catch(() => setError('Impossible de lire l’association. Reconnecte le téléphone.')).finally(() => setReady(true));
    Linking.getInitialURL().then(u => { if (u) onAppLink(u); });
    const subscription = Linking.addEventListener('url', e => onAppLink(e.url));
    return () => { subscription.remove(); if (watchdog.current) clearTimeout(watchdog.current); };
  }, []);

  const onAppLink = useEffectEvent((link: string) => {
    if (link === 'paceforge://privacy') openSettings('/mobile/privacy');
    if (link === 'paceforge://devices') { externalReturn.current = false; openTab('devices'); }
  });

  function finish() { lock.current = false; pending.current = null; setBusy(false); if (watchdog.current) clearTimeout(watchdog.current); }
  function refreshPage(sources = false) {
    web.current?.injectJavaScript(`if (window.paceforgeRefresh) window.paceforgeRefresh.refresh(${sources}); else { const u = new URL(location.href); u.searchParams.set('app_nav', Date.now()); location.replace(u.href); } true;`);
  }
  function restoreSync(refresh = false) {
    const back = syncReturn.current;
    syncReturn.current = null;
    if (inlineSync.current) {
      inlineSync.current = false;
      if (refresh && refreshable(webLocation.current)) {
        if (documentReady.current) refreshPage(refreshSources.current);
        else deferredRefresh.current = refreshSources.current;
      }
      return;
    }
    if (back || isBridgeUrl(url)) setUrl(screenUrl(back?.url || webLocation.current));
    if (refresh && refreshSources.current && back) deferredRefresh.current = true;
    if (back) { setTab(back.tab); setShowWeb(back.showWeb); }
  }
  function fail(e: unknown) {
    if (e instanceof ApiError && e.status === 401) setNeedsReconnect(true);
    // A timeout must leave an actionable screen, never the hidden bridge document.
    if (pending.current) { web.current?.stopLoading(); setShowWeb(false); }
    restoreSync();
    setError(e instanceof Error ? e.message : 'La synchronisation a échoué. Réessaie.'); finish();
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
    syncReturn.current = quiet ? { tab, url: screenUrl(webLocation.current), showWeb } : null;
    const nonce = random(), target = `${ORIGIN}/mobile/session?nonce=${nonce}`;
    inlineSync.current = quiet && documentReady.current && refreshable(webLocation.current);
    refreshSources.current = forceSources;
    pending.current = { kind: 'check', nonce, url: target, consent,
      ...(inlineSync.current ? { pageUrl: webLocation.current } : {}) };
    syncFull.current = !quiet || fullDue(report);
    lastAttempt.current = Date.now(); setStatus('Vérification du compte…');
    if (inlineSync.current) web.current?.injectJavaScript(inlineSessionScript(pending.current));
    else { setTab('devices'); setDetail('health'); setShowWeb(false); setUrl(target); }
    guard();
  }
  function refresh() {
    if (lock.current || cloudRefresh.current || !documentReady.current) return;
    if (pair && !needsReconnect) sync(true, false, true);
    else refreshPage(true);
  }
  async function upload(account: Pair, consent = false) {
    pending.current = null; if (watchdog.current) clearTimeout(watchdog.current);
    if (!inlineSync.current) { setShowWeb(false); setTab('devices'); setDetail('health'); }
    let refresh = false;
    try {
      if (consent) { setStatus('Choisis les données à partager…'); await authorize(); }
      const full = consent || syncFull.current;
      const result = await syncHealth({
        userId: account.user_id, full, previous: report,
        daily: () => collect(() => setStatus('Lecture des mesures quotidiennes…'), full ? 30 : 7),
        sleep: () => collectSleep(() => setStatus('Lecture du sommeil…'), full ? 60 : 7),
        send: (path, body) => api(path, 'POST', body, account.token), progress: setStatus,
        save: async receipt => {
          await SecureStore.setItemAsync(KEY + '.sync', JSON.stringify(receipt)); setReport(receipt);
          if (receipt.lastReceivedAt) {
            await SecureStore.setItemAsync(KEY + '.last', receipt.lastReceivedAt); setLast(receipt.lastReceivedAt);
          }
        },
      });
      setNeedsReconnect(result.reconnect); setStatus(reportText(result));
      refresh = result.received > 0 || refreshSources.current;
      if (!result.complete) setError(result.reconnect ? 'Reconnecte le téléphone dans Réglages.' : 'Synchronisation incomplète. Réessaie depuis Réglages.');
    } catch (e) { fail(e); } finally {
      restoreSync(refresh); finish();
    }
  }
  async function message(event: WebViewMessageEvent) {
    if (refreshable(event.nativeEvent.url)) {
      try {
        const data = JSON.parse(event.nativeEvent.data);
        if (data?.type === 'refresh-state' && typeof data.active === 'boolean') {
          cloudRefresh.current = data.active; setPageRefreshing(data.active); return;
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
      if (reply.kind === 'unavailable') throw new Error('Impossible de vérifier le compte. Vérifie le réseau puis réessaie.');
      if (!inlineSync.current) setShowWeb(false);
      if (reply.kind === 'connections') {
        setConnections(reply.snapshot); setShowWeb(false); setStatus(''); finish();
        return;
      }
      if (reply.kind === 'pair') {
        const account: Pair = await api('/api/mobile/exchange', 'POST', { code: reply.code, verifier: reply.task.verifier });
        await SecureStore.setItemAsync(KEY, JSON.stringify(account)); setPair(account); setShowWeb(false); setTab('devices');
        await SecureStore.deleteItemAsync(KEY + '.last'); setLast('');
        await SecureStore.deleteItemAsync(KEY + '.sync'); setReport(null);
        await SecureStore.deleteItemAsync(KEY + '.auto'); setAutomatic(false);
        setNeedsReconnect(false);
        await upload(account, true);
      } else if (reply.kind === 'check') {
        if (!pair || reply.userId !== pair.user_id) {
          setNeedsReconnect(true);
          throw new Error('Le compte affiché a changé. Utilise « Connecter » pour confirmer le compte qui recevra tes mesures.');
        }
        await upload(pair, reply.task.consent);
      }
    } catch (e) { fail(e); }
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
    pending.current = { kind: 'connections', nonce, url: target };
    setUrl(target); guard();
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
      await SecureStore.deleteItemAsync(KEY); await SecureStore.deleteItemAsync(KEY + '.auto'); await SecureStore.deleteItemAsync(KEY + '.last'); await SecureStore.deleteItemAsync(KEY + '.sync');
      setPair(null); setAutomatic(false); setLast(''); setReport(null); setStatus('Téléphone dissocié. L’historique reçu reste dans ton compte.');
    } catch (e) { fail(e); } finally { finish(); }
  }
  const onForeground = useEffectEvent(() => {
    if (lock.current || !ready) return;
    if (externalReturn.current) {
      externalReturn.current = false; setTab('devices'); setShowWeb(false); setDetail(null); refreshConnections();
    } else if (automatic && pair && !needsReconnect && documentReady.current && refreshable(webLocation.current) && due(report, lastAttempt.current)) sync(true);
  });
  useEffect(() => {
    const subscription = AppState.addEventListener('change', state => { if (state === 'active') onForeground(); });
    return () => subscription.remove();
  }, []);
  useEffect(() => {
    if (ready) onForeground();
  }, [ready]);

  function openTab(next: Tab) {
    if (lock.current && (!inlineSync.current || pending.current || next === 'devices')) return;
    setTab(next); setShowWeb(false); setDetail(null); setError(''); setStatus('');
    if (next !== 'devices') setUrl(`${ORIGIN}${destinations[next]}?app_nav=${Date.now()}`);
    else refreshConnections();
  }
  function openSettings(path = '/settings') { setTab('devices'); setUrl(ORIGIN + path); setShowWeb(true); }
  const webVisible = tab !== 'devices' || showWeb;
  const navigationLocked = busy && (!inlineSync.current || pending.current !== null);
  return <SafeAreaView style={styles.root}>
    <StatusBar style="dark" />
    <View style={styles.header}><View style={styles.heading}><Text style={styles.brand}>PaceForge</Text></View>
      {webVisible && refreshable(webLocation.current) && <Pressable accessibilityRole="button" accessibilityLabel={busy || pageRefreshing ? 'Actualisation en cours' : 'Actualiser les données'} accessibilityState={{ disabled: busy || pageRefreshing, busy: busy || pageRefreshing }} disabled={busy || pageRefreshing} onPress={refresh} style={styles.refreshButton}>
        {busy || pageRefreshing ? <ActivityIndicator size="small" color="#225649" /> : <Text style={styles.refreshIcon}>↻</Text>}
      </Pressable>}
      <Pressable accessibilityRole="button" accessibilityLabel="Réglages et connexions" accessibilityState={{ disabled: busy }} disabled={busy} onPress={() => openTab('devices')} style={styles.settings}><Text style={styles.settingsText}>Réglages</Text></Pressable></View>
    {webVisible && error ? <Text accessibilityRole="alert" style={[styles.error, styles.banner]}>{error}</Text> : null}
    <View style={styles.content}>
    {!webVisible && <DevicesScreen detail={detail} select={setDetail} pair={pair} last={last} report={report} automatic={automatic}
      busy={busy} ready={ready} status={status} error={error} connections={connections} needsReconnect={needsReconnect}
      connectHealth={connectHealth} sync={() => sync()} disconnect={disconnect}
      auto={v => { setAutomatic(v); SecureStore.setItemAsync(KEY + '.auto', String(v)).catch(fail); }}
      provider={openProvider} refresh={refreshConnections} settings={openSettings} health={() => openTab('health')} />}
    <View style={[styles.web, !webVisible && styles.hidden]} pointerEvents={webVisible ? 'auto' : 'none'} accessibilityElementsHidden={!webVisible} importantForAccessibility={webVisible ? 'auto' : 'no-hide-descendants'}>
      {showWeb && <Button text="Retour aux connexions" disabled={busy && !pending.current} onPress={() => { if (pending.current) { web.current?.stopLoading(); restoreSync(); finish(); setShowWeb(false); setTab('devices'); setDetail(null); setStatus(''); } else openTab('devices'); }} secondary />}
      <WebView ref={web} source={{ uri: url }} style={styles.web} sharedCookiesEnabled thirdPartyCookiesEnabled={false}
        pullToRefreshEnabled={Platform.OS === 'ios' && refreshable(webLocation.current) && !busy && !pageRefreshing}
        bounces decelerationRate="normal" automaticallyAdjustContentInsets={false} contentInsetAdjustmentBehavior="never"
        injectedJavaScriptBeforeContentLoaded={appShellScript} injectedJavaScript={appShellScript}
        originWhitelist={['https://*', 'paceforge://*']} mixedContentMode="never" allowFileAccess={false} setSupportMultipleWindows
        onMessage={message} onError={() => fail(new Error('Impossible de charger PaceForge. Vérifie ta connexion.'))}
        onLoadEnd={event => {
          if (pending.current && isBridgeUrl(event.nativeEvent.url)) web.current?.injectJavaScript(retryBridgeScript);
          if (refreshable(event.nativeEvent.url)) {
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
        onHttpError={event => { if (event.nativeEvent.url === url) fail(new Error(`PaceForge ne répond pas correctement (${event.nativeEvent.statusCode}).`)); }}
        onShouldStartLoadWithRequest={request => {
          if (trusted(request.url)) {
            if (request.navigationType === 'reload' && request.isTopFrame !== false && refreshable(request.url)) {
              if (recoveringProcess.current) recoveringProcess.current = false;
              else { refresh(); return false; }
            }
            const path = new URL(request.url).pathname;
            if (path === '/settings' && new URL(request.url).hash === '#connections') { openTab('devices'); return false; }
            if (['/auth/strava', '/coros/connect', '/setup'].includes(path)) {
              // Start AND finish OAuth in the same browser session.
              openProvider(path === '/coros/connect' ? 'coros' : 'strava');
              return false;
            }
            return true;
          }
          if (request.url === 'paceforge://devices') { openTab('devices'); return false; }
          if (request.url.startsWith('https://')) Linking.openURL(request.url).catch(() => setError('Impossible d’ouvrir ce lien.'));
          return false;
        }}
        onNavigationStateChange={nav => {
          if (!trusted(nav.url)) return;
          const path = new URL(nav.url).pathname;
          if (pending.current && (path.startsWith('/auth/') || path === '/')) {
            setShowWeb(true); if (watchdog.current) clearTimeout(watchdog.current);
          }
          if (!nav.loading && pending.current && !(pending.current.kind === 'check' && pending.current.pageUrl) && (path === '/accueil' || path === '/sante' || path === '/activities')) {
            setUrl(pending.current.url + "#resume-" + Date.now()); guard();
          }
          // Bridge completion can arrive before the final navigation event.
          // Those hidden pages must never turn into a visible device detail.
          if (isBridgeUrl(nav.url)) {
            if (pending.current) guard();
            if (pending.current?.kind === 'check') setShowWeb(false);
            return;
          }
          if (nav.loading) {
            documentReady.current = false; cloudRefresh.current = false; setPageRefreshing(false);
          }
          if (!nav.loading && !pending.current && (!lock.current || inlineSync.current) && webVisible) {
            webLocation.current = nav.url;
            const next = tabForUrl(nav.url);
            if (next) { setTab(next); setShowWeb(next === 'devices'); }
          }
        }}
      />
    </View>
    </View>
    <View style={styles.tabs}>
      {tabs.map(([key, label]) => <Pressable key={key} accessibilityRole="tab" accessibilityState={{ selected: tab === key, disabled: navigationLocked }} disabled={navigationLocked} onPress={() => openTab(key)} style={[styles.tab, tab === key && styles.active]}><Text style={[styles.tabText, tab === key && styles.activeText]}>{label}</Text></Pressable>)}
    </View>
  </SafeAreaView>;
}
export default function App() { return <SafeAreaProvider><Companion /></SafeAreaProvider>; }
const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: '#f5f7f6' }, header: { paddingHorizontal: 18, paddingVertical: 6, backgroundColor: '#fff', flexDirection: 'row', alignItems: 'center', gap: 8 },
  heading: { flex: 1 }, settings: { minHeight: 44, justifyContent: 'center', paddingHorizontal: 8 }, settingsText: { color: '#173e38', fontSize: 13, fontWeight: '600' },
  brand: { color: '#163d35', fontSize: 23, fontWeight: '700' },
  refreshButton: { width: 44, height: 44, alignItems: 'center', justifyContent: 'center' },
  refreshIcon: { fontSize: 27, color: '#41554e' },
  error: { color: '#a33129', fontSize: 15, lineHeight: 23, marginVertical: 8 },
  banner: { paddingHorizontal: 20 },
  content: { flex: 1 }, web: { flex: 1 }, hidden: { position: 'absolute', top: 0, right: 0, bottom: 0, left: 0, opacity: 0 },
  tabs: { flexDirection: 'row', padding: 8, gap: 3, backgroundColor: '#fff', borderTopWidth: 1, borderColor: '#e4ebe6' },
  tab: { flex: 1, paddingVertical: 14, borderRadius: 10 }, active: { backgroundColor: '#e7f0ea' },
  tabText: { fontSize: 12, textAlign: 'center', color: '#62736d', fontWeight: '600' }, activeText: { color: '#173e38' },
});
