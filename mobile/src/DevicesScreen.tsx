import React from 'react';
import { ActivityIndicator, Platform, Pressable, ScrollView, StyleSheet, Switch, Text, View } from 'react-native';
import { connectionLabel, Connections, Provider } from './connections';
import { Pair } from './types';
import { useHeartRate } from './use-heart-rate';
import { Button, ui } from './device-ui';

export type DeviceDetail = 'health' | 'bluetooth' | 'help' | null;
type Props = {
  detail: DeviceDetail; select: (detail: DeviceDetail) => void;
  pair: Pair | null; last: string; automatic: boolean; busy: boolean; ready: boolean;
  status: string; error: string; connections: Connections | null; needsReconnect: boolean;
  heart: ReturnType<typeof useHeartRate>;
  connectHealth: () => void; sync: () => void; disconnect: () => void;
  auto: (value: boolean) => void; provider: (provider: Provider) => void;
  refresh: () => void; settings: (path?: string) => void; health: () => void;
};
const healthName = Platform.OS === 'ios' ? 'Apple Santé' : 'Health Connect';

export function DevicesScreen(p: Props) {
  const { detail, pair, heart, busy, last } = p;
  const mismatch = p.needsReconnect || (!!pair && !!p.connections && pair.user_id !== p.connections.user_id);
  const healthState = mismatch ? 'Compte à confirmer' : !pair ? 'Non connecté' : last ? 'Données synchronisées' : 'À terminer';
  return <ScrollView contentContainerStyle={ui.content}>
    {detail && <Link text="‹ Tous les appareils" onPress={() => p.select(null)} disabled={busy} />}
    <Text accessibilityRole="header" style={ui.title}>{detail === 'health' ? healthName : detail === 'bluetooth' ? 'Capteur cardiaque' : detail === 'help' ? 'Balance et Zwift' : 'Appareils'}</Text>
    {!detail && <>
      <Text style={ui.body}>Choisis ce que tu veux connecter.</Text>
      <View style={styles.list}>
        <Row name={healthName} icon="♥" description="Poids, pas et mesures du téléphone" state={healthState}
          action={pair && last && !mismatch ? 'Gérer' : 'Connecter'} disabled={busy || !p.ready}
          onPress={() => { p.select('health'); if (!pair || !last || mismatch) p.connectHealth(); }} />
        {(['coros', 'garmin', 'strava'] as const).map(key => {
          const state = p.connections?.providers[key];
          return <Row key={key} name={{ coros: 'COROS', garmin: 'Garmin', strava: 'Strava' }[key]}
            icon={{ coros: 'C', garmin: 'G', strava: 'S' }[key]}
            description={{ coros: 'Montre APEX et compteur DURA', garmin: 'Montres et compteurs Garmin', strava: 'Activités et séances Zwift' }[key]}
            state={connectionLabel(state)} action={state === 'reconnect' ? 'Reconnecter' : state && state !== 'disconnected' ? 'Gérer' : 'Connecter'}
            disabled={busy} onPress={() => p.provider(key)} />;
        })}
        <Row name="Capteur Bluetooth" icon="♡" description="Brassard · pouls en direct" state={heart.state.phase === 'connected' ? 'Connecté' : 'Non connecté'}
          action={heart.state.phase === 'connected' ? 'Voir' : 'Connecter'} disabled={busy} last
          onPress={() => { p.select('bluetooth'); if (heart.state.phase === 'idle') void heart.controller.current?.scan(); }} />
      </View>
      {p.connections && <Text style={ui.small}>Compte de {p.connections.name}</Text>}
      <View style={styles.footer}>
        <Link text="Balance ou Zwift ?" onPress={() => p.select('help')} disabled={busy} />
        <Link text="Actualiser" onPress={p.refresh} disabled={busy} />
      </View>
      <Link text="Mon compte et confidentialité" onPress={() => p.settings()} disabled={busy} />
    </>}
    {detail === 'health' && <>
      <Text style={ui.body}>Retrouve tes mesures du téléphone dans PaceForge.</Text>
      <View style={ui.card}>
        <Text style={ui.cardTitle}>{healthState}</Text>
        <Text style={ui.body}>Poids, masse grasse, pas, fréquence cardiaque et respiration.</Text>
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
      <Text style={ui.small}>Lecture des 30 derniers jours, selon tes autorisations. Sommeil et VFC : utilise la connexion de ta montre.</Text>
      <Link text="Ajouter les mesures de ma balance" onPress={() => p.select('help')} disabled={busy} />
      {pair && <Link text={`Déconnecter ${healthName}`} onPress={p.disconnect} disabled={busy} />}
    </>}
    {detail === 'bluetooth' && <>
      <Text style={ui.body}>Porte ton brassard et garde-le près du téléphone.</Text>
      <View style={ui.card}>
        {heart.state.phase === 'connected' && <>
          <Text style={ui.cardTitle}>{heart.state.sensor?.name}</Text>
          <Text style={styles.pulse}>{heart.reading ? heart.reading.bpm : '—'}<Text style={styles.unit}> bpm</Text></Text>
          <Button text="Voir dans Santé" onPress={p.health} disabled={busy} secondary />
        </>}
        {['preparing', 'scanning', 'connecting'].includes(heart.state.phase) && <ActivityIndicator color="#225649" />}
        <Text accessibilityLiveRegion="polite" style={ui.body}>{heart.state.phase === 'connected' && !heart.reading ? 'En attente d’une mesure récente…' : heart.state.message}</Text>
        {heart.state.error ? <Text accessibilityRole="alert" style={ui.error}>{heart.state.error}</Text> : null}
        {['idle', 'scanning'].includes(heart.state.phase) && heart.state.sensors.map((sensor, index) =>
          <Button key={sensor.id} text={`${sensor.name}${heart.state.sensors.filter(s => s.name === sensor.name).length > 1 ? ` · ${index + 1}` : ''} · Connecter`}
            onPress={() => { void heart.controller.current?.connect(sensor); }} secondary />)}
        <Button text={heart.state.phase === 'idle' ? 'Rechercher un capteur' : heart.state.phase === 'connected' ? 'Déconnecter' : 'Arrêter la recherche'}
          onPress={() => { if (heart.state.phase === 'idle') void heart.controller.current?.scan(); else heart.controller.current?.stop(); }} secondary={heart.state.phase !== 'idle'} />
      </View>
      <Text style={ui.small}>Pouls en direct uniquement, sans enregistrement. La lecture s’arrête quand tu quittes PaceForge.</Text>
      <Disclosure title="Mon capteur n’apparaît pas">
        <Text style={ui.body}>Vérifie que le Bluetooth est activé et que le brassard est porté. Le brassard COROS accepte trois connexions : si ta montre, DURA et Zwift les utilisent déjà, libère une place.</Text>
        <Text style={ui.body}>Pour une montre APEX 4, active la diffusion de la fréquence cardiaque sur la montre.</Text>
      </Disclosure>
    </>}
    {detail === 'help' && <>
      <View style={ui.card}><Text style={ui.cardTitle}>Balance EXZACT</Text>
        <Text style={ui.body}>{Platform.OS === 'ios' ? 'Dans SenssunHealth, active le partage vers Apple Santé. Dès qu’une pesée apparaît dans Apple Santé, PaceForge peut l’importer.' : 'Active le partage vers Health Connect si l’application de ta balance le propose.'}</Text>
        <Button text={`Ouvrir ${healthName} dans PaceForge`} onPress={() => p.select('health')} />
        <Text style={ui.small}>La connexion Bluetooth directe de cette balance n’est pas encore disponible.</Text>
      </View>
      <View style={ui.card}><Text style={ui.cardTitle}>Zwift</Text>
        <Text style={ui.body}>Partage tes séances Zwift avec COROS ou Strava, puis connecte ce compte à PaceForge.</Text>
        <Button text="Connecter COROS" onPress={() => p.provider('coros')} />
        <Button text="Connecter Strava" onPress={() => p.provider('strava')} secondary />
        <Disclosure title="Où activer le partage COROS ?"><Text style={ui.body}>Application COROS → Profil → Applications tierces → Synchronisation des données → Zwift.</Text></Disclosure>
      </View>
    </>}
    {busy && <View style={styles.progress}><ActivityIndicator color="#225649" /><Text accessibilityLiveRegion="polite" style={ui.body}>{p.status || 'Chargement…'}</Text></View>}
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
function Disclosure({ title, children }: { title: string; children: React.ReactNode }) {
  const [open, setOpen] = React.useState(false);
  return <View><Pressable accessibilityRole="button" accessibilityState={{ expanded: open }} onPress={() => setOpen(!open)} style={ui.link}><Text style={ui.linkText}>{title} {open ? '−' : '+'}</Text></Pressable>{open && children}</View>;
}
const styles = StyleSheet.create({
  list: { backgroundColor: '#fff', borderRadius: 20, paddingHorizontal: 14, borderWidth: 1, borderColor: '#e4ebe6', marginTop: 18 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 16, minHeight: 92 }, divider: { borderBottomWidth: 1, borderColor: '#edf1ee' },
  icon: { width: 36, height: 36, borderRadius: 11, alignItems: 'center', justifyContent: 'center', backgroundColor: '#e7f0ea' }, iconText: { color: '#245e4e', fontSize: 19, fontWeight: '700' },
  name: { color: '#173e38', fontSize: 17, fontWeight: '600' }, description: { color: '#62736d', fontSize: 12, lineHeight: 18, marginTop: 3 },
  state: { color: '#41554e', fontSize: 12, lineHeight: 18 }, action: { color: '#245e4e', fontSize: 12, fontWeight: '600', maxWidth: 86 },
  footer: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'space-between' }, progress: { paddingTop: 18, gap: 8 },
  pulse: { color: '#173e38', fontSize: 54, fontWeight: '700', fontVariant: ['tabular-nums'] }, unit: { fontSize: 19, fontWeight: '400' },
});
