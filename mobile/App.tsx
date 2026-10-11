import React, { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Alert, AppState, Linking, Platform, Pressable, ScrollView, StyleSheet, Switch, Text, View } from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { StatusBar } from 'expo-status-bar';
import * as Crypto from 'expo-crypto';
import * as SecureStore from 'expo-secure-store';
import { WebView, WebViewMessageEvent } from 'react-native-webview';
import { api, ORIGIN, trusted } from './src/api';
import { authorize, collect } from './src/health';
import { labels, Metric, Pair } from './src/types';
import { appShellScript, destinations, Tab, tabForUrl, tabs } from './src/navigation';
import { useHeartRate } from './src/use-heart-rate';

type Pending = { kind: 'pair'; verifier: string; challenge: string; url: string } | { kind: 'check'; nonce: string; url: string };
const KEY = 'paceforge.phone.v1';
const random = () => Array.from(Crypto.getRandomBytes(32), b => b.toString(16).padStart(2, '0')).join('');

function Companion() {
  const [tab, setTab] = useState<Tab>('health');
  const [url, setUrl] = useState(ORIGIN + '/sante');
  const [pair, setPair] = useState<Pair | null>(null);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [showWeb, setShowWeb] = useState(false);
  const [status, setStatus] = useState('Associe ton téléphone puis choisis les données à partager.');
  const [error, setError] = useState('');
  const [last, setLast] = useState('');
  const [automatic, setAutomatic] = useState(false);
  const web = useRef<WebView>(null);
  const pending = useRef<Pending | null>(null);
  const lock = useRef(false);
  const lastAttempt = useRef(0);
  const watchdog = useRef<ReturnType<typeof setTimeout> | null>(null);
  const heart = useHeartRate();
  const webLocation = useRef(ORIGIN + '/sante');
  const syncReturn = useRef<{ tab: Tab; url: string; showWeb: boolean } | null>(null);

  useEffect(() => {
    Promise.all([SecureStore.getItemAsync(KEY), SecureStore.getItemAsync(KEY + '.last'), SecureStore.getItemAsync(KEY + '.auto')])
      .then(([saved, time, auto]) => { if (saved) setPair(JSON.parse(saved)); setLast(time || ''); setAutomatic(auto === 'true'); })
      .catch(() => setError('Impossible de lire l’association. Reconnecte le téléphone.')).finally(() => setReady(true));
    const open = ({ url }: { url: string }) => { if (url === 'paceforge://privacy') { setUrl(ORIGIN + '/mobile/privacy'); setShowWeb(true); } };
    Linking.getInitialURL().then(u => { if (u) open({ url: u }); });
    const subscription = Linking.addEventListener('url', open);
    return () => { subscription.remove(); if (watchdog.current) clearTimeout(watchdog.current); };
  }, []);

  function finish() { lock.current = false; pending.current = null; setBusy(false); if (watchdog.current) clearTimeout(watchdog.current); }
  function fail(e: unknown) { setError(e instanceof Error ? e.message : 'La synchronisation a échoué. Réessaie.'); finish(); }
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
      pending.current = { kind: 'pair', verifier, challenge, url: target };
      setUrl(target); setShowWeb(true); setStatus('Confirme le compte auquel envoyer les mesures.'); guard();
    } catch (e) { fail(e); }
  }
  function sync(quiet = false) {
    if (!pair || !start()) return;
    syncReturn.current = quiet ? { tab, url: webLocation.current, showWeb } : null;
    const nonce = random(), target = `${ORIGIN}/mobile/session?nonce=${nonce}`;
    pending.current = { kind: 'check', nonce, url: target };
    lastAttempt.current = Date.now(); setStatus('Vérification du compte…'); setUrl(target); guard();
  }
  async function upload(account: Pair) {
    pending.current = null; if (watchdog.current) clearTimeout(watchdog.current);
    const returnTo = syncReturn.current;
    setShowWeb(false); setTab('devices');
    try {
      const result = await collect(setStatus);
      if (!result.days.length) { setStatus(result.notices.join('\n')); return; }
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
      if (task.kind === 'pair' && data.type === 'pair' && data.challenge === task.challenge && trusted(event.nativeEvent.url, '/mobile/connect')) {
        pending.current = null;
        const account: Pair = await api('/api/mobile/exchange', 'POST', { code: data.code, verifier: task.verifier });
        await SecureStore.setItemAsync(KEY, JSON.stringify(account)); setPair(account); setShowWeb(false); setTab('devices');
        setStatus('Téléphone associé. Autorise maintenant les catégories à synchroniser.'); finish();
      } else if (task.kind === 'check' && data.type === 'session' && data.nonce === task.nonce && trusted(event.nativeEvent.url, '/mobile/session')) {
        if (!pair || data.user_id !== pair.user_id) throw new Error('Le compte affiché a changé. Reconnecte ce téléphone au compte voulu avant tout envoi.');
        await upload(pair);
      }
    } catch (e) { fail(e); }
  }
  async function permissions() {
    if (!start()) return;
    try { await authorize(); setStatus('Autorisations demandées. Lance une synchronisation pour voir les données réellement disponibles.'); } catch (e) { fail(e); } finally { finish(); }
  }
  async function disconnect() {
    if (!pair || !start()) return;
    try {
      await api('/api/mobile/device', 'DELETE', undefined, pair.token);
      await SecureStore.deleteItemAsync(KEY); await SecureStore.deleteItemAsync(KEY + '.auto'); await SecureStore.deleteItemAsync(KEY + '.last');
      setPair(null); setAutomatic(false); setLast(''); setStatus('Téléphone dissocié. L’historique reçu reste dans ton compte.');
    } catch (e) { fail(e); } finally { finish(); }
  }
  useEffect(() => {
    const subscription = AppState.addEventListener('change', state => {
      if (state === 'active' && automatic && pair && !lock.current && Date.now() - Math.max(Date.parse(last) || 0, lastAttempt.current) > 6 * 3600000) sync(true);
    });
    return () => subscription.remove();
  }, [automatic, pair, last, ready, tab, showWeb]);

  function openTab(next: Tab) {
    if (busy) return;
    setTab(next); setShowWeb(false);
    if (next !== 'devices') setUrl(`${ORIGIN}${destinations[next]}?app_nav=${Date.now()}`);
  }
  function openSettings(path = '/settings') { setTab('devices'); setUrl(ORIGIN + path); setShowWeb(true); }
  const webVisible = tab !== 'devices' || showWeb;
  return <SafeAreaView style={styles.root}>
    <StatusBar style="dark" />
    <View style={styles.header}><Text style={styles.brand}>PaceForge</Text><Text style={styles.sub}>Tes données de santé, réunies</Text></View>
    {tab === 'health' && heart.state.phase === 'connected' && <View style={styles.liveBar}>
      <View style={{ flex: 1 }}><Text style={styles.cardTitle}>Pouls en direct · {heart.reading ? `${heart.reading.bpm} bpm` : '—'}</Text>
        <Text style={styles.small}>{heart.state.sensor?.name} · {heart.reading ? 'Bluetooth' : 'En attente de mesures'}</Text></View>
      <Button text="Capteur" onPress={() => openTab('devices')} disabled={busy} secondary />
    </View>}
    {webVisible && error ? <Text accessibilityRole="alert" style={[styles.error, styles.banner]}>{error}</Text> : null}
    {!webVisible && <ScrollView contentContainerStyle={styles.content}>
      <Text style={styles.title}>Appareils</Text>
      <Text style={styles.body}>Connecte tes sources pour retrouver tes mesures dans Santé et tes séances dans Activités.</Text>
      <View style={styles.card}><Text style={styles.eyebrow}>BLUETOOTH DIRECT</Text><Text style={styles.cardTitle}>Brassard et capteurs cardiaques</Text>
        <Text style={styles.body}>Porte ton brassard COROS, puis recherche-le. Une montre APEX 4 peut aussi diffuser son pouls si tu actives sa fonction de diffusion cardiaque.</Text>
        {heart.state.phase === 'connected' && <View style={styles.measurement}>
          <Text style={styles.small}>{heart.state.sensor?.name}</Text>
          <Text style={styles.pulse}>{heart.reading ? heart.reading.bpm : '—'}<Text style={styles.unit}> bpm</Text></Text>
          <Text style={styles.small}>{heart.reading ? `Reçu à ${new Date(heart.reading.at).toLocaleTimeString('fr-FR')}` : 'En attente d’une mesure récente'}</Text>
          <Button text="Voir dans Santé" onPress={() => openTab('health')} disabled={busy} secondary />
        </View>}
        {['preparing', 'scanning', 'connecting'].includes(heart.state.phase) && <ActivityIndicator color="#225649" />}
        <Text accessibilityLiveRegion="polite" style={styles.body}>{heart.state.phase === 'connected' && heart.state.reading && !heart.reading ? 'Le capteur ne transmet plus de mesure récente. Vérifie le brassard.' : heart.state.message}</Text>
        {heart.state.error ? <Text accessibilityRole="alert" style={styles.error}>{heart.state.error}</Text> : null}
        {['idle', 'scanning'].includes(heart.state.phase) && heart.state.sensors.map((sensor, index) =>
          <Button key={sensor.id} text={`Connecter ${sensor.name}${heart.state.sensors.filter(s => s.name === sensor.name).length > 1 ? ` · ${index + 1}` : ''}`} onPress={() => { void heart.controller.current?.connect(sensor); }} secondary />)}
        {heart.state.phase === 'idle'
          ? <Button text="Rechercher un capteur" onPress={() => { void heart.controller.current?.scan(); }} />
          : <Button text={heart.state.phase === 'connected' ? 'Déconnecter le capteur' : 'Arrêter'} onPress={() => heart.controller.current?.stop()} secondary />}
        <Text style={styles.small}>Lecture en direct tant que PaceForge reste ouvert. Ces valeurs ne sont ni enregistrées dans l’historique ni utilisées pour calculer la récupération.</Text>
        <Text style={styles.small}>Le brassard COROS accepte jusqu’à 3 connexions simultanées. Si ta montre, DURA et Zwift les occupent déjà, libère une connexion.</Text>
      </View>
      <View style={styles.card}><Text style={styles.eyebrow}>MONTRES ET COMPTEURS</Text><Text style={styles.cardTitle}>COROS et Garmin</Text>
        <Text style={styles.body}>Tes nuits et mesures de santé disponibles depuis la montre, et tes activités enregistrées avec la montre ou le compteur.</Text>
        <Text style={styles.small}>APEX 4 et DURA : synchronise-les avec l’application COROS, puis relie ce compte à PaceForge. La diffusion Bluetooth du pouls ne transfère pas l’historique des nuits.</Text>
        <Button text="Connexion COROS · APEX et DURA" onPress={() => openSettings('/settings#coros')} disabled={busy} />
        <Button text="Connexion Garmin" onPress={() => openSettings('/settings#garmin')} disabled={busy} secondary />
      </View>
      <View style={styles.card}><Text style={styles.eyebrow}>ACTIVITÉS EN INTÉRIEUR</Text><Text style={styles.cardTitle}>Zwift</Text>
        <Text style={styles.body}>Pour retrouver tes séances Zwift, active leur partage vers COROS ou Strava, puis connecte ce compte à PaceForge.</Text>
        <Text style={styles.small}>Dans COROS : Profil → Applications tierces → Synchronisation des données → Zwift. Vérifie ensuite qu’une séance terminée apparaît dans Activités.</Text>
        <Button text="Connexion Strava" onPress={() => openSettings('/settings#strava')} disabled={busy} secondary />
      </View>
      <View style={styles.card}><Text style={styles.eyebrow}>BALANCE ET MESURES DU TÉLÉPHONE</Text><Text style={styles.cardTitle}>{Platform.OS === 'ios' ? 'Apple Santé' : 'Health Connect'}</Text>
        <Text style={styles.body}>Poids · Masse grasse · Pas · Fréquence cardiaque · Respiration</Text>
        <Text style={styles.body}>{Platform.OS === 'ios' ? 'Balance EXZACT avec SenssunHealth : active le partage vers Apple Santé et vérifie qu’une pesée y apparaît. PaceForge peut alors la recevoir.' : 'Active le partage vers Health Connect dans les applications compatibles. Le partage de SenssunHealth sur Android reste à vérifier sur ton téléphone.'}</Text>
        <Text style={styles.small}>Le Bluetooth direct des balances EXZACT n’est pas encore pris en charge. Les mesures disponibles dépendent du modèle et des autorisations.</Text>
        <Text style={styles.body}>{pair ? `Téléphone associé au compte de ${pair.name}.` : '1. Associe ce téléphone au compte auquel envoyer tes mesures.'}</Text>
        <Button text={pair ? 'Changer l’association au compte' : '1. Associer ce téléphone'} onPress={associate} disabled={busy || !ready} secondary={!!pair} />
        <Button text={pair ? 'Choisir les autorisations' : '2. Choisir les autorisations'} onPress={permissions} disabled={busy || !pair} secondary />
        <Button text={pair ? 'Synchroniser maintenant' : '3. Synchroniser maintenant'} onPress={() => sync()} disabled={busy || !pair} />
        <Text style={styles.small}>Import des 30 derniers jours. Le sommeil et la VFC ne sont pas importés par cette connexion.</Text>
        <View style={styles.toggle}><Text style={[styles.body, { flex: 1 }]}>Synchroniser au retour dans l’application, au plus toutes les 6 h</Text><Switch accessibilityLabel="Synchroniser au retour dans l’application" value={automatic} disabled={busy || !pair} onValueChange={v => { setAutomatic(v); SecureStore.setItemAsync(KEY + '.auto', String(v)).catch(fail); }} /></View>
        {last ? <Text style={styles.small}>Dernier envoi : {new Date(last).toLocaleString('fr-FR')}</Text> : null}
        {busy && <ActivityIndicator color="#225649" />}
        <Text accessibilityLiveRegion="polite" style={styles.body}>{status}</Text>
        {error ? <Text accessibilityRole="alert" style={styles.error}>{error}</Text> : null}
        {last && <Button text="Consulter mes mesures dans Santé" onPress={() => openTab('health')} disabled={busy} secondary />}
      </View>
      <View style={styles.card}><Text style={styles.cardTitle}>Mon compte et mes données</Text>
        <Button text="Réglages et état des connexions" onPress={() => openSettings()} disabled={busy} secondary />
        <Button text="Données et confidentialité" onPress={() => openSettings('/mobile/privacy')} disabled={busy} secondary />
        {pair && <Button text="Dissocier ce téléphone" onPress={disconnect} disabled={busy} secondary />}
      </View>
    </ScrollView>}
    <View style={webVisible ? styles.web : styles.hidden}>
      {showWeb && <Button text="Retour aux appareils" onPress={() => { if (pending.current) finish(); setShowWeb(false); setTab('devices'); }} secondary />}
      <WebView ref={web} source={{ uri: url }} style={styles.web} sharedCookiesEnabled thirdPartyCookiesEnabled={false}
        injectedJavaScriptBeforeContentLoaded={appShellScript} injectedJavaScript={appShellScript}
        originWhitelist={['https://*']} mixedContentMode="never" allowFileAccess={false} setSupportMultipleWindows
        onMessage={message} onError={() => fail(new Error('Impossible de charger PaceForge. Vérifie ta connexion.'))}
        onHttpError={event => { if (event.nativeEvent.url === url) fail(new Error(`PaceForge ne répond pas correctement (${event.nativeEvent.statusCode}).`)); }}
        onShouldStartLoadWithRequest={request => {
          if (trusted(request.url)) {
            const path = new URL(request.url).pathname;
            if (['/auth/strava', '/coros/connect', '/setup'].includes(path)) {
              // Start AND finish OAuth in the same browser; carrying only the
              // provider redirect outside the WebView would lose its session.
              Alert.alert('Connecter une montre ou Strava',
                'Termine la connexion dans les réglages du navigateur, en vérifiant le compte PaceForge utilisé. Reviens ensuite dans l’application.',
                [{ text: 'Annuler', style: 'cancel' }, { text: 'Ouvrir les réglages', onPress: () => { Linking.openURL(ORIGIN + '/settings').catch(fail); } }]);
              return false;
            }
            return true;
          }
          if (request.url.startsWith('https://')) Linking.openURL(request.url).catch(() => setError('Impossible d’ouvrir ce lien.'));
          return false;
        }}
        onNavigationStateChange={nav => {
          if (!trusted(nav.url)) return;
          const path = new URL(nav.url).pathname;
          if (pending.current && (path.startsWith('/auth/') || path === '/')) {
            setShowWeb(true); if (watchdog.current) clearTimeout(watchdog.current);
          }
          if (!nav.loading && pending.current && (path === '/sante' || path === '/activities')) {
            setUrl(pending.current.url + "#resume-" + Date.now()); guard();
          }
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
function Button({ text, onPress, disabled = false, secondary = false }: { text: string; onPress: () => void; disabled?: boolean; secondary?: boolean }) { return <Pressable accessibilityRole="button" accessibilityState={{ disabled }} disabled={disabled} onPress={onPress} style={[styles.button, secondary && styles.secondaryButton, disabled && styles.disabled]}><Text style={[styles.buttonText, secondary && styles.secondaryText]}>{text}</Text></Pressable>; }
export default function App() { return <SafeAreaProvider><Companion /></SafeAreaProvider>; }
const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: '#f5f7f6' }, header: { paddingHorizontal: 22, paddingVertical: 12, backgroundColor: '#ffffff' }, brand: { color: '#163d35', fontSize: 23, fontWeight: '700' }, sub: { color: '#62736d', fontSize: 13, marginTop: 2 },
  content: { padding: 20, paddingBottom: 32 }, title: { fontSize: 28, fontWeight: '700', color: '#173e38', marginBottom: 10 }, body: { fontSize: 15, lineHeight: 23, color: '#41554e', marginVertical: 6 }, small: { color: '#62736d', fontSize: 13, marginVertical: 8 },
  card: { padding: 18, backgroundColor: '#ffffff', borderRadius: 18, marginTop: 18, borderWidth: 1, borderColor: '#e4ebe6' }, cardTitle: { fontSize: 19, fontWeight: '600', color: '#173e38', marginBottom: 5 },
  button: { backgroundColor: '#245e4e', paddingVertical: 13, paddingHorizontal: 14, borderRadius: 11, marginTop: 10 }, buttonText: { color: '#ffffff', textAlign: 'center', fontSize: 15, fontWeight: '600' }, disabled: { opacity: 0.45 },
  toggle: { flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 14 }, error: { color: '#a33129', fontSize: 15, lineHeight: 23, marginVertical: 8 },
  eyebrow: { color: '#62736d', fontSize: 11, letterSpacing: 1, fontWeight: '700', marginBottom: 8 },
  secondaryButton: { backgroundColor: '#e7f0ea' }, secondaryText: { color: '#173e38' },
  measurement: { paddingVertical: 10 }, pulse: { color: '#173e38', fontSize: 54, fontWeight: '700', fontVariant: ['tabular-nums'] }, unit: { fontSize: 19, fontWeight: '400' },
  liveBar: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 20, paddingVertical: 8, backgroundColor: '#e7f0ea', gap: 8 }, banner: { paddingHorizontal: 20 },
  web: { flex: 1 }, hidden: { width: 1, height: 1, opacity: 0, position: 'absolute', overflow: 'hidden' }, tabs: { flexDirection: 'row', padding: 8, gap: 3, backgroundColor: '#fff', borderTopWidth: 1, borderColor: '#e4ebe6' }, tab: { flex: 1, paddingVertical: 14, borderRadius: 10 }, active: { backgroundColor: '#e7f0ea' }, tabText: { fontSize: 12, textAlign: 'center', color: '#62736d', fontWeight: '600' }, activeText: { color: '#173e38' },
});
