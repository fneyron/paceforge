import React, { useEffect, useEffectEvent, useRef, useState } from 'react';
import { AppState, Linking, Platform, Pressable, StyleSheet, Text, View } from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { StatusBar } from 'expo-status-bar';
import * as Crypto from 'expo-crypto';
import * as SecureStore from 'expo-secure-store';
import { WebView, WebViewMessageEvent } from 'react-native-webview';
import { api, ApiError, ORIGIN, trusted } from './src/api';
import { authorize, collect } from './src/health';
import { labels, Metric, Pair } from './src/types';
import { appShellScript, destinations, Tab, tabForUrl, tabs } from './src/navigation';
import { Connections, Provider, readConnections } from './src/connections';
import { DeviceDetail, DevicesScreen } from './src/DevicesScreen';
import { Button } from './src/device-ui';

type Pending = { kind: 'pair'; verifier: string; challenge: string; url: string } | { kind: 'check'; nonce: string; url: string; consent: boolean } | { kind: 'connections'; nonce: string; url: string };
const KEY = 'paceforge.phone.v1';
const random = () => Array.from(Crypto.getRandomBytes(32), b => b.toString(16).padStart(2, '0')).join('');

function Companion() {
  const [tab, setTab] = useState<Tab>('home');
  const [url, setUrl] = useState(ORIGIN + '/accueil');
  const [pair, setPair] = useState<Pair | null>(null);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [showWeb, setShowWeb] = useState(false);
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');
  const [last, setLast] = useState('');
  const [automatic, setAutomatic] = useState(false);
  const [detail, setDetail] = useState<DeviceDetail>(null);
  const [connections, setConnections] = useState<Connections | null>(null);
  const [needsReconnect, setNeedsReconnect] = useState(false);
  const externalReturn = useRef(false);
  const web = useRef<WebView>(null);
  const pending = useRef<Pending | null>(null);
  const lock = useRef(false);
  const lastAttempt = useRef(0);
  const watchdog = useRef<ReturnType<typeof setTimeout> | null>(null);
  const webLocation = useRef(ORIGIN + '/accueil');
  const syncReturn = useRef<{ tab: Tab; url: string; showWeb: boolean } | null>(null);

  useEffect(() => {
    Promise.all([SecureStore.getItemAsync(KEY), SecureStore.getItemAsync(KEY + '.last'), SecureStore.getItemAsync(KEY + '.auto')])
      .then(([saved, time, auto]) => { if (saved) setPair(JSON.parse(saved)); setLast(time || ''); setAutomatic(auto === 'true'); })
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
  function fail(e: unknown) {
    if (e instanceof ApiError && e.status === 401) setNeedsReconnect(true);
    setError(e instanceof Error ? e.message : 'La synchronisation a échoué. Réessaie.'); finish();
  }
  function start() { if (lock.current || !ready) return false; lock.current = true; setBusy(true); setError(''); return true; }
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
  function sync(quiet = false, consent = false) {
    if (!pair || !start()) return;
    syncReturn.current = quiet ? { tab, url: webLocation.current, showWeb } : null;
    const nonce = random(), target = `${ORIGIN}/mobile/session?nonce=${nonce}`;
    pending.current = { kind: 'check', nonce, url: target, consent };
    lastAttempt.current = Date.now(); setStatus('Vérification du compte…'); setUrl(target); guard();
  }
  async function upload(account: Pair, consent = false) {
    pending.current = null; if (watchdog.current) clearTimeout(watchdog.current);
    const returnTo = syncReturn.current;
    setShowWeb(false); setTab('devices'); setDetail('health');
    try {
      if (consent) { setStatus('Choisis les données à partager…'); await authorize(); }
      const result = await collect(() => setStatus('Synchronisation de tes mesures…'));
      if (!result.days.length) { setStatus('Aucune mesure reçue. Vérifie que des données sont présentes et que PaceForge peut les lire dans les réglages de santé du téléphone.'); return; }
      setStatus(`Envoi de ${result.days.length} mesures quotidiennes…`);
      const response = await api('/api/mobile/daily', 'POST', { days: result.days }, account.token);
      const counts = Object.entries(labels).flatMap(([m, label]) => { const n = result.days.filter(d => d.metric === m as Metric).length; return n ? [`${label} : ${n} jour${n > 1 ? 's' : ''}`] : []; });
      const time = response.checked_at as string;
      await SecureStore.setItemAsync(KEY + '.last', time); setLast(time);
      setStatus(`${response.received} mesures reçues par PaceForge.\n${counts.join(' · ')}${result.notices.length ? '\n' + result.notices.join('\n') : ''}`);
    } catch (e) { fail(e); } finally {
      if (returnTo) { setTab(returnTo.tab); setShowWeb(returnTo.showWeb); setUrl(returnTo.url); }
      syncReturn.current = null; finish();
    }
  }
  async function message(event: WebViewMessageEvent) {
    const task = pending.current;
    if (!task) return;
    try {
      const data = JSON.parse(event.nativeEvent.data);
      if (task.kind === 'connections') {
        const snapshot = readConnections(data, event.nativeEvent.url, task.nonce);
        if (snapshot) { setConnections(snapshot); setShowWeb(false); setStatus(''); finish(); }
        return;
      }
      if (task.kind === 'pair' && data.type === 'pair' && data.challenge === task.challenge && trusted(event.nativeEvent.url, '/mobile/connect')) {
        pending.current = null; if (watchdog.current) clearTimeout(watchdog.current);
        const account: Pair = await api('/api/mobile/exchange', 'POST', { code: data.code, verifier: task.verifier });
        await SecureStore.setItemAsync(KEY, JSON.stringify(account)); setPair(account); setShowWeb(false); setTab('devices');
        await SecureStore.deleteItemAsync(KEY + '.last'); setLast('');
        setNeedsReconnect(false);
        await upload(account, true);
      } else if (task.kind === 'check' && data.type === 'session' && data.nonce === task.nonce && trusted(event.nativeEvent.url, '/mobile/session')) {
        if (!pair || data.user_id !== pair.user_id) {
          setNeedsReconnect(true);
          throw new Error('Le compte affiché a changé. Utilise « Connecter » pour confirmer le compte qui recevra tes mesures.');
        }
        await upload(pair, task.consent);
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
      await SecureStore.deleteItemAsync(KEY); await SecureStore.deleteItemAsync(KEY + '.auto'); await SecureStore.deleteItemAsync(KEY + '.last');
      setPair(null); setAutomatic(false); setLast(''); setStatus('Téléphone dissocié. L’historique reçu reste dans ton compte.');
    } catch (e) { fail(e); } finally { finish(); }
  }
  const onForeground = useEffectEvent(() => {
    if (lock.current || !ready) return;
    if (externalReturn.current) {
      externalReturn.current = false; setTab('devices'); setShowWeb(false); setDetail(null); refreshConnections();
    } else if (automatic && pair && !needsReconnect && Date.now() - Math.max(Date.parse(last) || 0, lastAttempt.current) > 6 * 3600000) sync(true);
  });
  useEffect(() => {
    const subscription = AppState.addEventListener('change', state => { if (state === 'active') onForeground(); });
    return () => subscription.remove();
  }, []);

  function openTab(next: Tab) {
    if (lock.current) return;
    setTab(next); setShowWeb(false); setDetail(null); setError(''); setStatus('');
    if (next !== 'devices') setUrl(`${ORIGIN}${destinations[next]}?app_nav=${Date.now()}`);
    else refreshConnections();
  }
  function openSettings(path = '/settings') { setTab('devices'); setUrl(ORIGIN + path); setShowWeb(true); }
  const webVisible = tab !== 'devices' || showWeb;
  return <SafeAreaView style={styles.root}>
    <StatusBar style="dark" />
    <View style={styles.header}><View style={styles.heading}><Text style={styles.brand}>PaceForge</Text></View><Pressable accessibilityRole="button" accessibilityLabel="Réglages et connexions" accessibilityState={{ disabled: busy }} disabled={busy} onPress={() => openTab('devices')} style={styles.settings}><Text style={styles.settingsText}>Réglages</Text></Pressable></View>
    {webVisible && error ? <Text accessibilityRole="alert" style={[styles.error, styles.banner]}>{error}</Text> : null}
    {!webVisible && <DevicesScreen detail={detail} select={setDetail} pair={pair} last={last} automatic={automatic}
      busy={busy} ready={ready} status={status} error={error} connections={connections} needsReconnect={needsReconnect}
      connectHealth={connectHealth} sync={() => sync()} disconnect={disconnect}
      auto={v => { setAutomatic(v); SecureStore.setItemAsync(KEY + '.auto', String(v)).catch(fail); }}
      provider={openProvider} refresh={refreshConnections} settings={openSettings} health={() => openTab('health')} />}
    <View style={webVisible ? styles.web : styles.hidden}>
      {showWeb && <Button text="Retour aux connexions" disabled={busy && !pending.current} onPress={() => { if (pending.current) { finish(); setShowWeb(false); setTab('devices'); setDetail(null); setStatus(''); } else openTab('devices'); }} secondary />}
      <WebView ref={web} source={{ uri: url }} style={styles.web} sharedCookiesEnabled thirdPartyCookiesEnabled={false}
        injectedJavaScriptBeforeContentLoaded={appShellScript} injectedJavaScript={appShellScript}
        originWhitelist={['https://*', 'paceforge://*']} mixedContentMode="never" allowFileAccess={false} setSupportMultipleWindows
        onMessage={message} onError={() => fail(new Error('Impossible de charger PaceForge. Vérifie ta connexion.'))}
        onHttpError={event => { if (event.nativeEvent.url === url) fail(new Error(`PaceForge ne répond pas correctement (${event.nativeEvent.statusCode}).`)); }}
        onShouldStartLoadWithRequest={request => {
          if (trusted(request.url)) {
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
          if (!nav.loading && pending.current && (path === '/accueil' || path === '/sante' || path === '/activities')) {
            setUrl(pending.current.url + "#resume-" + Date.now()); guard();
          }
          // Bridge completion can arrive before the final navigation event.
          // Those hidden pages must never turn into a visible device detail.
          if (['/mobile/session', '/mobile/connections', '/mobile/connect'].includes(path)) return;
          if (!nav.loading && !pending.current && !lock.current && webVisible) {
            webLocation.current = nav.url;
            const next = tabForUrl(nav.url);
            if (next) { setTab(next); setShowWeb(next === 'devices'); }
          }
        }}
      />
    </View>
    <View style={styles.tabs}>
      {tabs.map(([key, label]) => <Pressable key={key} accessibilityRole="tab" accessibilityState={{ selected: tab === key, disabled: busy }} disabled={busy} onPress={() => openTab(key)} style={[styles.tab, tab === key && styles.active]}><Text style={[styles.tabText, tab === key && styles.activeText]}>{label}</Text></Pressable>)}
    </View>
  </SafeAreaView>;
}
export default function App() { return <SafeAreaProvider><Companion /></SafeAreaProvider>; }
const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: '#f5f7f6' }, header: { paddingHorizontal: 18, paddingVertical: 6, backgroundColor: '#fff', flexDirection: 'row', alignItems: 'center', gap: 8 },
  heading: { flex: 1 }, settings: { minHeight: 44, justifyContent: 'center', paddingHorizontal: 8 }, settingsText: { color: '#173e38', fontSize: 13, fontWeight: '600' },
  brand: { color: '#163d35', fontSize: 23, fontWeight: '700' },
  error: { color: '#a33129', fontSize: 15, lineHeight: 23, marginVertical: 8 },
  banner: { paddingHorizontal: 20 },
  web: { flex: 1 }, hidden: { width: 1, height: 1, opacity: 0, position: 'absolute', overflow: 'hidden' },
  tabs: { flexDirection: 'row', padding: 8, gap: 3, backgroundColor: '#fff', borderTopWidth: 1, borderColor: '#e4ebe6' },
  tab: { flex: 1, paddingVertical: 14, borderRadius: 10 }, active: { backgroundColor: '#e7f0ea' },
  tabText: { fontSize: 12, textAlign: 'center', color: '#62736d', fontWeight: '600' }, activeText: { color: '#173e38' },
});
