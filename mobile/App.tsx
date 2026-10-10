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

type Tab = 'sync' | 'health' | 'activities';
type Pending = { kind: 'pair'; verifier: string; challenge: string; url: string } | { kind: 'check'; nonce: string; url: string };
const KEY = 'paceforge.phone.v1';
const random = () => Array.from(Crypto.getRandomBytes(32), b => b.toString(16).padStart(2, '0')).join('');

function Companion() {
  const [tab, setTab] = useState<Tab>('sync');
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
  function sync() {
    if (!pair || !start()) return;
    const nonce = random(), target = `${ORIGIN}/mobile/session?nonce=${nonce}`;
    pending.current = { kind: 'check', nonce, url: target };
    lastAttempt.current = Date.now(); setStatus('Vérification du compte…'); setUrl(target); guard();
  }
  async function upload(account: Pair) {
    pending.current = null; if (watchdog.current) clearTimeout(watchdog.current);
    setShowWeb(false); setTab('sync');
    try {
      const result = await collect(setStatus);
      if (!result.days.length) { setStatus(result.notices.join('\n')); return; }
      setStatus(`Envoi de ${result.days.length} mesures quotidiennes…`);
      const response = await api('/api/mobile/daily', 'POST', { days: result.days }, account.token);
      const counts = Object.entries(labels).flatMap(([m, label]) => { const n = result.days.filter(d => d.metric === m as Metric).length; return n ? [`${label} : ${n} jour${n > 1 ? 's' : ''}`] : []; });
      const time = response.checked_at as string;
      await SecureStore.setItemAsync(KEY + '.last', time); setLast(time);
      setStatus(`${response.received} mesures reçues par PaceForge.\n${counts.join(' · ')}${result.notices.length ? '\n' + result.notices.join('\n') : ''}`);
    } catch (e) { fail(e); } finally { finish(); }
  }
  async function message(event: WebViewMessageEvent) {
    const task = pending.current;
    if (!task) return;
    try {
      const data = JSON.parse(event.nativeEvent.data);
      if (task.kind === 'pair' && data.type === 'pair' && data.challenge === task.challenge && trusted(event.nativeEvent.url, '/mobile/connect')) {
        pending.current = null;
        const account: Pair = await api('/api/mobile/exchange', 'POST', { code: data.code, verifier: task.verifier });
        await SecureStore.setItemAsync(KEY, JSON.stringify(account)); setPair(account); setShowWeb(false); setTab('sync');
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
      if (state === 'active' && automatic && pair && !lock.current && Date.now() - Math.max(Date.parse(last) || 0, lastAttempt.current) > 6 * 3600000) sync();
    });
    return () => subscription.remove();
  }, [automatic, pair, last, ready]);

  function openTab(next: Tab) { if (busy) return; setTab(next); setShowWeb(false); if (next !== 'sync') setUrl(ORIGIN + (next === 'health' ? '/sante' : '/activities')); }
  const webVisible = tab !== 'sync' || showWeb;
  return <SafeAreaView style={styles.root}>
    <StatusBar style="dark" />
    <View style={styles.header}><Text style={styles.brand}>PaceForge</Text><Text style={styles.sub}>Ton suivi, au même endroit</Text></View>
    {!webVisible && <ScrollView contentContainerStyle={styles.content}>
      <Text style={styles.title}>Synchronisation</Text>
      <Text style={styles.body}>{Platform.OS === 'ios' ? 'Apple Santé' : 'Health Connect'} rassemble les mesures partagées par tes applications et appareils.</Text>
      <View style={styles.card}><Text style={styles.cardTitle}>{pair ? `Compte de ${pair.name}` : 'Ton compte PaceForge'}</Text>
        <Text style={styles.body}>{pair ? 'Les données seront envoyées uniquement à ce compte, après vérification de la session.' : 'Connecte-toi avec ton compte habituel. Strava est facultatif.'}</Text>
        <Button text={pair ? 'Reconnecter le téléphone' : 'Associer ce téléphone'} onPress={associate} disabled={busy || !ready} />
      </View>
      <View style={styles.card}><Text style={styles.cardTitle}>Les données du téléphone</Text>
        <Text style={styles.body}>Poids · Masse grasse · Pas · FC · Respiration</Text>
        <Text style={styles.body}>Lecture des 30 derniers jours. Les périodes sans données restent vides. Les mesures de la journée ne remplacent pas celles du sommeil.</Text>
        <Button text="Choisir les autorisations" onPress={permissions} disabled={busy || !pair} />
        <Button text="Synchroniser maintenant" onPress={sync} disabled={busy || !pair} />
        <View style={styles.toggle}><Text style={[styles.body, { flex: 1 }]}>Synchroniser au retour dans l’application, au plus toutes les 6 h</Text><Switch accessibilityLabel="Synchroniser au retour dans l’application" value={automatic} disabled={busy || !pair} onValueChange={v => { setAutomatic(v); SecureStore.setItemAsync(KEY + '.auto', String(v)).catch(fail); }} /></View>
        {last ? <Text style={styles.small}>Dernier envoi : {new Date(last).toLocaleString('fr-FR')}</Text> : null}
        {busy && <ActivityIndicator color="#225649" />}
        <Text accessibilityLiveRegion="polite" style={styles.body}>{status}</Text>
        {error ? <Text accessibilityRole="alert" style={styles.error}>{error}</Text> : null}
      </View>
      <View style={styles.card}><Text style={styles.cardTitle}>Balance et montres</Text>
        <Text style={styles.body}>{Platform.OS === 'ios' ? 'Dans Senssun Health, active le partage du poids vers Apple Santé. Vérifie qu’une pesée y apparaît avant de synchroniser.' : 'Active le partage vers Health Connect dans les applications compatibles. Le partage Senssun Health sur Android reste à vérifier ; la balance n’est pas connectée directement.'}</Text>
        <Text style={styles.body}>Garmin, COROS et Strava se connectent dans les réglages PaceForge. Les totaux des montres et du téléphone ne sont pas additionnés.</Text>
        <Button text="Connexions et réglages" onPress={() => { setUrl(ORIGIN + '/settings'); setShowWeb(true); }} disabled={busy} />
        <Button text="Données et confidentialité" onPress={() => { setUrl(ORIGIN + '/mobile/privacy'); setShowWeb(true); }} disabled={busy} />
        {pair && <Button text="Dissocier ce téléphone" onPress={disconnect} disabled={busy} />}
      </View>
    </ScrollView>}
    <View style={webVisible ? styles.web : styles.hidden}>
      {showWeb && <Button text="Retour à la synchronisation" onPress={() => { if (pending.current) finish(); setShowWeb(false); setTab('sync'); }} />}
      <WebView ref={web} source={{ uri: url }} style={styles.web} sharedCookiesEnabled thirdPartyCookiesEnabled={false}
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
        }}
      />
    </View>
    <View style={styles.tabs}>
      {([['sync', 'Synchronisation'], ['health', 'Santé'], ['activities', 'Activités']] as const).map(([key, label]) => <Pressable key={key} accessibilityRole="tab" accessibilityState={{ selected: tab === key, disabled: busy }} onPress={() => openTab(key)} style={[styles.tab, tab === key && styles.active]}><Text style={[styles.tabText, tab === key && styles.activeText]}>{label}</Text></Pressable>)}
    </View>
  </SafeAreaView>;
}
function Button({ text, onPress, disabled = false }: { text: string; onPress: () => void; disabled?: boolean }) { return <Pressable accessibilityRole="button" accessibilityState={{ disabled }} disabled={disabled} onPress={onPress} style={[styles.button, disabled && styles.disabled]}><Text style={styles.buttonText}>{text}</Text></Pressable>; }
export default function App() { return <SafeAreaProvider><Companion /></SafeAreaProvider>; }
const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: '#f5f7f6' }, header: { paddingHorizontal: 22, paddingVertical: 12, backgroundColor: '#ffffff' }, brand: { color: '#163d35', fontSize: 23, fontWeight: '700' }, sub: { color: '#62736d', fontSize: 13, marginTop: 2 },
  content: { padding: 20, paddingBottom: 32 }, title: { fontSize: 28, fontWeight: '700', color: '#173e38', marginBottom: 10 }, body: { fontSize: 15, lineHeight: 23, color: '#41554e', marginVertical: 6 }, small: { color: '#62736d', fontSize: 13, marginVertical: 8 },
  card: { padding: 18, backgroundColor: '#ffffff', borderRadius: 18, marginTop: 18, borderWidth: 1, borderColor: '#e4ebe6' }, cardTitle: { fontSize: 19, fontWeight: '600', color: '#173e38', marginBottom: 5 },
  button: { backgroundColor: '#245e4e', paddingVertical: 13, paddingHorizontal: 14, borderRadius: 11, marginTop: 10 }, buttonText: { color: '#ffffff', textAlign: 'center', fontSize: 15, fontWeight: '600' }, disabled: { opacity: 0.45 },
  toggle: { flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 14 }, error: { color: '#a33129', fontSize: 15, lineHeight: 23, marginVertical: 8 },
  web: { flex: 1 }, hidden: { width: 1, height: 1, opacity: 0, position: 'absolute', overflow: 'hidden' }, tabs: { flexDirection: 'row', padding: 8, gap: 3, backgroundColor: '#fff', borderTopWidth: 1, borderColor: '#e4ebe6' }, tab: { flex: 1, paddingVertical: 14, borderRadius: 10 }, active: { backgroundColor: '#e7f0ea' }, tabText: { fontSize: 12, textAlign: 'center', color: '#62736d', fontWeight: '600' }, activeText: { color: '#173e38' },
});
