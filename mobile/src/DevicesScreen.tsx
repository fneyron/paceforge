import React, { useContext } from 'react';
import { ActivityIndicator, Alert, Image, ImageSourcePropType, Platform, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { ConnectionAction, connectionLabel, Connections, Provider, providerNames, providers } from './connections';
import { Pair } from './types';
import { reportText, SyncReport } from './sync';
import { Button, darkStyles, DeviceTheme, useDeviceUI } from './device-ui';
import appleHealth from '../assets/connections/apple-health.png';
import coros from '../assets/connections/coros.png';
import garmin from '../assets/connections/garmin.png';
import strava from '../assets/connections/strava.png';

export type DeviceDetail = 'health' | Provider | 'help' | null;
type Props = {
  detail: DeviceDetail; select: (detail: DeviceDetail) => void;
  pair: Pair | null; last: string; report: SyncReport | null; busy: boolean; ready: boolean;
  status: string; error: string; connections: Connections | null; needsReconnect: boolean;
  connectHealth: () => void; sync: () => void; disconnect: () => void;
  connect: (provider: Provider) => void; act: (action: ConnectionAction) => void; settings: (path?: string) => void;
};
const healthName = Platform.OS === 'ios' ? 'Apple Santé' : 'Health Connect';
// The apps' own icons (owner, 2026-10-17): Apple's Health icon, the App Store icons of COROS, Garmin Connect, Strava.
const icons: Record<Provider, ImageSourcePropType> = { coros, garmin, strava };
const summary: Record<Provider, string> = { coros: 'Activités et données de santé', garmin: 'Activités et données de santé',
  strava: 'Activités sportives' };
const intro: Record<Provider, string> = { coros: 'Retrouve les activités et données de santé de ton compte COROS.',
  garmin: 'Retrouve les mesures et activités de Garmin Connect.', strava: 'Retrouve les activités partagées sur ton compte Strava.' };
const imported: Record<Provider, string> = { coros: 'Activités, sommeil, VFC et fréquence cardiaque.',
  garmin: 'Activités, sommeil, VFC, fréquence cardiaque, respiration, stress et pas.', strava: 'Activités sportives.' };
const isProvider = (detail: DeviceDetail): detail is Provider => providers.includes(detail as Provider);
const when = (iso: string) => new Date(iso).toLocaleString('fr-FR');

/** Every connection's screen has the same two actions (owner, 2026-10-17); a disconnection is confirmed. */
function confirmDisconnect(name: string, go: () => void) {
  Alert.alert(`Déconnecter ${name} ?`, 'Les mesures déjà reçues restent dans ton compte.', [
    { text: 'Annuler', style: 'cancel' }, { text: 'Déconnecter', style: 'destructive', onPress: go }]);
}

export function DevicesScreen(p: Props) {
  const ui = useDeviceUI(), styles = useDeviceStyles();
  const { detail, pair, busy, last } = p;
  const mismatch = p.needsReconnect || (!!pair && !!p.connections && pair.user_id !== p.connections.user_id);
  const healthState = mismatch ? 'Compte à confirmer' : !pair ? 'Non connecté' : p.report && !p.report.complete ? 'À actualiser' : 'Connecté';
  return <ScrollView contentContainerStyle={ui.content}>
    {detail && <Link text="‹ Toutes les connexions" onPress={() => p.select(null)} disabled={busy} />}
    <Text accessibilityRole="header" style={ui.title}>{detail === 'health' ? healthName : isProvider(detail) ? providerNames[detail] : detail === 'help' ? 'Comment ça fonctionne ?' : 'Réglages'}</Text>
    {busy && <View style={styles.progress}><ActivityIndicator color="#225649" /><Text accessibilityLiveRegion="polite" style={ui.body}>{p.status || 'Chargement…'}</Text></View>}
    {!detail && <>
      <Text style={ui.body}>Choisis les services que tu utilises.</Text>
      <View style={styles.list}>
        <Row name={healthName} icon={Platform.OS === 'ios' ? appleHealth : null} description={Platform.OS === 'ios' ? 'Sommeil, cœur et mesures quotidiennes' : 'Poids, pas et mesures du téléphone'} state={healthState}
          action={pair && !mismatch ? 'Gérer' : 'Connecter'} disabled={busy || !p.ready}
          onPress={() => { p.select('health'); if (!pair || mismatch) p.connectHealth(); }} />
        {providers.map(key => {
          const state = p.connections?.providers[key];
          const unlinked = state === 'disconnected' || state === 'reconnect';
          return <Row key={key} name={providerNames[key]} icon={icons[key]} description={summary[key]}
            state={connectionLabel(state)} action={state === 'reconnect' ? 'Reconnecter' : state && state !== 'disconnected' ? 'Gérer' : 'Connecter'}
            disabled={busy} last={key === 'strava'} onPress={() => { p.select(key); if (unlinked) p.connect(key); }} />;
        })}
      </View>
      {p.connections && <Text style={ui.small}>Compte de {p.connections.name}</Text>}
      <Link text="Comment ça fonctionne ?" onPress={() => p.select('help')} disabled={busy} />
      <Link text="Compte, apparence et confidentialité" onPress={() => p.settings()} disabled={busy} />
    </>}
    {detail === 'health' && <>
      <Text style={ui.body}>Retrouve tes mesures du téléphone dans PaceForge.</Text>
      <View style={ui.card}>
        <Text style={ui.cardTitle}>{healthState}</Text>
        <Text style={ui.body}>{Platform.OS === 'ios' ? 'Sommeil, VFC, fréquence cardiaque, respiration, poids, masse grasse et pas.' : 'Poids, masse grasse, pas, fréquence cardiaque et respiration.'}</Text>
        {pair && <Text style={ui.small}>Compte de {pair.name}{last ? ` · Dernier envoi : ${when(last)}` : ''}</Text>}
        {/* Two actions (owner, 2026-10-17): sync now, disconnect. Updates are automatic; a permission never
            asked opens by itself (App.upload). */}
        <Button text={pair && !mismatch ? 'Synchroniser' : `Connecter ${healthName}`} onPress={pair && !mismatch ? p.sync : p.connectHealth} disabled={busy || !p.ready} />
        {pair && <Button text={`Déconnecter ${healthName}`} secondary disabled={busy} onPress={() => confirmDisconnect(healthName, p.disconnect)} />}
      </View>
    </>}
    {isProvider(detail) && <ProviderCard provider={detail} connections={p.connections} busy={busy} connect={p.connect} act={p.act} />}
    {detail === 'help' && <>
      <View style={ui.card}><Text style={ui.cardTitle}>Montres et capteurs</Text>
        <Text style={ui.body}>Synchronise tes appareils dans l’application du fabricant, puis connecte ce compte à PaceForge.</Text>
        <Text style={ui.body}>Ton brassard, ta ceinture cardiaque ou tes autres capteurs restent reliés à la montre, au compteur ou à l’application qui enregistre ta séance. Les mesures incluses dans l’activité partagée arrivent par ce compte.</Text>
        <Text style={ui.small}>Aucun appairage séparé du capteur dans PaceForge.</Text>
      </View>
      <View style={ui.card}><Text style={ui.cardTitle}>Mesures de santé et balance</Text>
        <Text style={ui.body}>Dans l’application de ta balance ou de ton appareil, active le partage vers {healthName} si cette option est proposée. Connecte ensuite {healthName} à PaceForge.</Text>
        <Button text={`Ouvrir ${healthName}`} onPress={() => p.select('health')} disabled={busy} />
      </View>
      <View style={ui.card}><Text style={ui.cardTitle}>Applications sportives</Text>
        <Text style={ui.body}>Une application comme Zwift peut partager tes séances avec un service compatible, comme Strava. Connecte ce service à PaceForge pour les retrouver dans Activités.</Text>
        <Button text="Ouvrir la connexion Strava" onPress={() => p.select('strava')} disabled={busy} />
      </View>
    </>}
    {!busy && (detail === 'health' || isProvider(detail)) && (p.status || (detail === 'health' && p.report)) ?
      <Text accessibilityLiveRegion="polite" style={ui.body}>{p.status || (p.report && reportText(p.report))}</Text> : null}
    {p.error ? <Text accessibilityRole="alert" style={ui.error} dataDetectorType="link">{p.error}</Text> : null}
  </ScrollView>;
}

/** COROS, Garmin or Strava as the Apple Santé screen: its state, what it brings, the last sync, two actions. */
function ProviderCard({ provider, connections, busy, connect, act }: { provider: Provider; connections: Connections | null;
  busy: boolean; connect: (provider: Provider) => void; act: (action: ConnectionAction) => void }) {
  const ui = useDeviceUI();
  const name = providerNames[provider], state = connections?.providers[provider], info = connections?.details[provider];
  const linked = !!state && state !== 'disconnected';
  return <>
    <Text style={ui.body}>{intro[provider]}</Text>
    <View style={ui.card}>
      <Text style={ui.cardTitle}>{connectionLabel(state)}</Text>
      <Text style={ui.body}>{imported[provider]}</Text>
      {connections && linked && <Text style={ui.small}>Compte de {connections.name}{info?.lastSync ? ` · Dernière synchronisation : ${when(info.lastSync)}` : ''}</Text>}
      {info?.notice ? <Text style={ui.small}>{info.notice}</Text> : null}
      {linked && state !== 'reconnect' && <Button text="Synchroniser" onPress={() => act({ provider, kind: 'sync' })} disabled={busy} />}
      {(state === 'disconnected' || state === 'reconnect') && <Button text={`${state === 'reconnect' ? 'Reconnecter' : 'Connecter'} ${name}`}
        onPress={() => connect(provider)} disabled={busy} />}
      {linked && <Button text={`Déconnecter ${name}`} secondary disabled={busy}
        onPress={() => confirmDisconnect(name, () => act({ provider, kind: 'disconnect' }))} />}
    </View>
  </>;
}

function Row(p: { name: string; icon: ImageSourcePropType | null; description: string; state: string; action: string; onPress: () => void; disabled: boolean; last?: boolean }) {
  const ui = useDeviceUI(), styles = useDeviceStyles();
  return <Pressable accessibilityRole="button" accessibilityLabel={`${p.name}. ${p.description}. ${p.state}. ${p.action}`} accessibilityState={{ disabled: p.disabled }}
    disabled={p.disabled} onPress={p.onPress} style={[styles.row, !p.last && styles.divider, p.disabled && ui.disabled]}>
    {p.icon ? <Image source={p.icon} style={styles.logo} accessibilityIgnoresInvertColors />
      : <View style={styles.icon}><Text style={styles.iconText}>♥</Text></View>}
    <View style={{ flex: 1 }}><Text style={styles.name}>{p.name}</Text><Text style={styles.description}>{p.description}</Text><Text style={styles.state}>{p.state}</Text></View>
    <Text style={styles.action}>{p.action} ›</Text>
  </Pressable>;
}
function Link({ text, onPress, disabled = false }: { text: string; onPress: () => void; disabled?: boolean }) {
  const ui = useDeviceUI();
  return <Pressable accessibilityRole="button" disabled={disabled} accessibilityState={{ disabled }} onPress={onPress} style={[ui.link, disabled && ui.disabled]}><Text style={ui.linkText}>{text}</Text></Pressable>;
}
const styles = StyleSheet.create({
  list: { backgroundColor: '#fff', borderRadius: 20, paddingHorizontal: 14, borderWidth: 1, borderColor: '#e4ebe6', marginTop: 18 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 16, minHeight: 92 }, divider: { borderBottomWidth: 1, borderColor: '#edf1ee' },
  icon: { width: 36, height: 36, borderRadius: 11, alignItems: 'center', justifyContent: 'center', backgroundColor: '#e7f0ea' }, iconText: { color: '#245e4e', fontSize: 19, fontWeight: '700' },
  name: { color: '#173e38', fontSize: 17, fontWeight: '600' }, description: { color: '#62736d', fontSize: 12, lineHeight: 18, marginTop: 3 },
  state: { color: '#41554e', fontSize: 12, lineHeight: 18 }, action: { color: '#245e4e', fontSize: 12, fontWeight: '600', maxWidth: 86 },
  progress: { paddingTop: 18, gap: 8 },
  logo: { width: 36, height: 36, borderRadius: 9, borderWidth: StyleSheet.hairlineWidth, borderColor: '#e4ebe6' },
});
const nightStyles = darkStyles(styles);
function useDeviceStyles() { return useContext(DeviceTheme) ? nightStyles : styles; }
