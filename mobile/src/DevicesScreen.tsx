import React from 'react';
import { ActivityIndicator, Platform, Pressable, ScrollView, StyleSheet, Switch, Text, View } from 'react-native';
import { connectionLabel, Connections, Provider } from './connections';
import { Pair } from './types';
import { Button, ui } from './device-ui';

export type DeviceDetail = 'health' | 'other' | 'help' | null;
type Props = {
  detail: DeviceDetail; select: (detail: DeviceDetail) => void;
  pair: Pair | null; last: string; automatic: boolean; busy: boolean; ready: boolean;
  status: string; error: string; connections: Connections | null; needsReconnect: boolean;
  connectHealth: () => void; sync: () => void; disconnect: () => void;
  auto: (value: boolean) => void; provider: (provider: Provider) => void;
  refresh: () => void; settings: (path?: string) => void; health: () => void;
};
const healthName = Platform.OS === 'ios' ? 'Apple Santé' : 'Health Connect';

export function DevicesScreen(p: Props) {
  const { detail, pair, busy, last } = p;
  const mismatch = p.needsReconnect || (!!pair && !!p.connections && pair.user_id !== p.connections.user_id);
  const healthState = mismatch ? 'Compte à confirmer' : !pair ? 'Non connecté' : last ? 'Données synchronisées' : 'À terminer';
  return <ScrollView contentContainerStyle={ui.content}>
    {detail && <Link text="‹ Toutes les connexions" onPress={() => p.select(null)} disabled={busy} />}
    <Text accessibilityRole="header" style={ui.title}>{detail === 'health' ? healthName : detail === 'other' ? 'Autres marques' : detail === 'help' ? 'Comment ça fonctionne ?' : 'Connexions'}</Text>
    {busy && <View style={styles.progress}><ActivityIndicator color="#225649" /><Text accessibilityLiveRegion="polite" style={ui.body}>{p.status || 'Chargement…'}</Text></View>}
    {!detail && <>
      <Text style={ui.body}>Choisis les services que tu utilises.</Text>
      <View style={styles.list}>
        {(['coros', 'garmin', 'strava'] as const).map(key => {
          const state = p.connections?.providers[key];
          return <Row key={key} name={{ coros: 'COROS', garmin: 'Garmin', strava: 'Strava' }[key]}
            icon={{ coros: 'C', garmin: 'G', strava: 'S' }[key]}
            description={{ coros: 'Activités et données de santé', garmin: 'Activités et données de santé', strava: 'Activités sportives' }[key]}
            state={connectionLabel(state)} action={state === 'reconnect' ? 'Reconnecter' : state && state !== 'disconnected' ? 'Gérer' : 'Connecter'}
            disabled={busy} onPress={() => p.provider(key)} />;
        })}
        <Row name={healthName} icon="♥" description={Platform.OS === 'ios' ? 'Sommeil, cœur et mesures quotidiennes' : 'Poids, pas et mesures du téléphone'} state={healthState}
          action={pair && last && !mismatch ? 'Gérer' : 'Connecter'} disabled={busy || !p.ready} last
          onPress={() => { p.select('health'); if (!pair || !last || mismatch) p.connectHealth(); }} />
      </View>
      {p.connections && <Text style={ui.small}>Compte de {p.connections.name}</Text>}
      <View style={styles.footer}>
        <Link text="Autres marques" onPress={() => p.select('other')} disabled={busy} />
        <Link text="Actualiser" onPress={p.refresh} disabled={busy} />
      </View>
      <Link text="Comment ça fonctionne ?" onPress={() => p.select('help')} disabled={busy} />
      <Link text="Mon compte et confidentialité" onPress={() => p.settings()} disabled={busy} />
    </>}
    {detail === 'health' && <>
      <Text style={ui.body}>Retrouve tes mesures du téléphone dans PaceForge.</Text>
      <View style={ui.card}>
        <Text style={ui.cardTitle}>{healthState}</Text>
        <Text style={ui.body}>{Platform.OS === 'ios' ? 'Sommeil, VFC, fréquence cardiaque, respiration, poids, masse grasse et pas.' : 'Poids, masse grasse, pas, fréquence cardiaque et respiration.'}</Text>
        {pair && <Text style={ui.small}>Compte de {pair.name}{last ? ` · Dernier envoi : ${new Date(last).toLocaleString('fr-FR')}` : ''}</Text>}
        <Button text={pair && last && !mismatch ? 'Synchroniser' : `Connecter ${healthName}`} onPress={pair && last && !mismatch ? p.sync : p.connectHealth} disabled={busy || !p.ready} />
        {last && !mismatch && <Link text="Voir mes mesures" onPress={p.health} disabled={busy} />}
      </View>
      {pair && <>
        <View style={ui.toggle}><View style={{ flex: 1 }}><Text style={ui.body}>Actualisation automatique</Text><Text style={ui.small}>Au retour dans PaceForge, au plus toutes les 6 h.</Text></View>
          <Switch accessibilityLabel="Actualisation automatique" value={p.automatic} disabled={busy || mismatch} onValueChange={p.auto} />
        </View>
        <Link text="Revoir les autorisations" onPress={p.connectHealth} disabled={busy} />
      </>}
      <Text style={ui.small}>{Platform.OS === 'ios' ? 'Selon tes autorisations : nuits sur 60 jours et mesures quotidiennes sur 30 jours.' : 'Lecture des 30 derniers jours, selon tes autorisations. Sommeil et VFC : utilise la connexion de ta montre.'}</Text>
      <Link text="Ajouter les mesures de ma balance" onPress={() => p.select('help')} disabled={busy} />
      {pair && <Link text={`Déconnecter ${healthName}`} onPress={p.disconnect} disabled={busy} />}
    </>}
    {detail === 'other' && <>
      <Text style={ui.body}>Les connexions directes à ces services ne sont pas encore disponibles dans PaceForge.</Text>
      <View style={ui.card}>
        {['Suunto', 'Amazfit (Zepp)'].map(name => <View key={name} style={styles.unavailable}>
          <Text style={ui.cardTitle}>{name}</Text><Text style={ui.small}>À intégrer</Text>
        </View>)}
      </View>
      <Text style={ui.body}>Si ton application fabricant partage tes activités avec Strava, connecte Strava pour les retrouver ici.</Text>
      <Button text="Ouvrir la connexion Strava" onPress={() => p.provider('strava')} disabled={busy} />
      <Text style={ui.body}>Pour les mesures de santé, utilise {healthName} si ton application fabricant y écrit les données. Seules les catégories prises en charge par PaceForge seront importées.</Text>
      <Button text={`Ouvrir ${healthName}`} onPress={() => p.select('health')} disabled={busy} secondary />
    </>}
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
        <Button text="Ouvrir la connexion Strava" onPress={() => p.provider('strava')} disabled={busy} />
      </View>
    </>}
    {!busy && detail === 'health' && p.status ? <Text accessibilityLiveRegion="polite" style={ui.body}>{p.status}</Text> : null}
    {p.error ? <Text accessibilityRole="alert" style={ui.error}>{p.error}</Text> : null}
  </ScrollView>;
}

function Row(p: { name: string; icon: string; description: string; state: string; action: string; onPress: () => void; disabled: boolean; last?: boolean }) {
  return <Pressable accessibilityRole="button" accessibilityLabel={`${p.name}. ${p.description}. ${p.state}. ${p.action}`} accessibilityState={{ disabled: p.disabled }}
    disabled={p.disabled} onPress={p.onPress} style={[styles.row, !p.last && styles.divider, p.disabled && ui.disabled]}>
    <View style={styles.icon}><Text style={styles.iconText}>{p.icon}</Text></View>
    <View style={{ flex: 1 }}><Text style={styles.name}>{p.name}</Text><Text style={styles.description}>{p.description}</Text><Text style={styles.state}>{p.state}</Text></View>
    <Text style={styles.action}>{p.action} ›</Text>
  </Pressable>;
}
function Link({ text, onPress, disabled = false }: { text: string; onPress: () => void; disabled?: boolean }) {
  return <Pressable accessibilityRole="button" disabled={disabled} accessibilityState={{ disabled }} onPress={onPress} style={[ui.link, disabled && ui.disabled]}><Text style={ui.linkText}>{text}</Text></Pressable>;
}
const styles = StyleSheet.create({
  list: { backgroundColor: '#fff', borderRadius: 20, paddingHorizontal: 14, borderWidth: 1, borderColor: '#e4ebe6', marginTop: 18 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 16, minHeight: 92 }, divider: { borderBottomWidth: 1, borderColor: '#edf1ee' },
  icon: { width: 36, height: 36, borderRadius: 11, alignItems: 'center', justifyContent: 'center', backgroundColor: '#e7f0ea' }, iconText: { color: '#245e4e', fontSize: 19, fontWeight: '700' },
  name: { color: '#173e38', fontSize: 17, fontWeight: '600' }, description: { color: '#62736d', fontSize: 12, lineHeight: 18, marginTop: 3 },
  state: { color: '#41554e', fontSize: 12, lineHeight: 18 }, action: { color: '#245e4e', fontSize: 12, fontWeight: '600', maxWidth: 86 },
  footer: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'space-between' }, progress: { paddingTop: 18, gap: 8 },
  unavailable: { paddingVertical: 8 },
});
