import React from 'react';
import { Alert, Pressable, ScrollView, Text, View } from 'react-native';
import { Button, useDeviceUI } from './device-ui';
import { clockLabel, OfflinePlan } from './offline';

export function OfflinePlansScreen({ plans, selected, select, close, remove, notice }: {
  plans: OfflinePlan[]; selected: 'list' | number; select: (id: 'list' | number) => void;
  close: () => void; remove: (id: number) => void; notice: string;
}) {
  const ui = useDeviceUI(), plan = plans.find(p => p.route_id === selected);
  return <ScrollView contentContainerStyle={ui.content}>
    <Button text={plan ? 'Plans hors connexion' : 'Retour aux courses'} secondary onPress={plan ? () => select('list') : close} />
    <Text accessibilityRole="header" style={[ui.title, { marginTop: 20 }]}>{plan?.name || 'Plans hors connexion'}</Text>
    {!!notice && <Text accessibilityRole="alert" style={ui.error}>{notice}</Text>}
    {plan ? <>
      <Text style={ui.body}>{plan.distance_km.toLocaleString('fr-FR', { maximumFractionDigits: 1 })} km · {Math.round(plan.elevation_m)} m D+</Text>
      <Text style={ui.small}>Copie du {new Date(plan.saved_at).toLocaleString('fr-FR', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}. Les changements ultérieurs ne sont pas inclus.</Text>
      <Text style={ui.small}>Lecture seule · Carte et modifications avec connexion.</Text>
      <View style={ui.card}>
        <Text accessibilityRole="header" style={ui.cardTitle}>Départ · {clockLabel(plan.start_s)}</Text>
        {!!plan.race_date && <Text style={ui.body}>{new Date(plan.race_date + 'T12:00:00').toLocaleDateString('fr-FR', { day: 'numeric', month: 'long', year: 'numeric' })}</Text>}
        <Text style={ui.small}>{plan.planned ? 'Horaires du plan enregistré' : 'Horaires estimés'}</Text>
      </View>
      {plan.points.map((point, index) => <View key={index} style={ui.card}>
        <Text accessibilityRole="header" style={ui.cardTitle}>{point.name}</Text>
        <Text style={ui.body}>{clockLabel(point.clock_s)} · km {point.km.toLocaleString('fr-FR', { maximumFractionDigits: 1 })}</Text>
        {(point.passed || point.pinned) && <Text style={ui.small}>{point.passed ? 'Passage déjà effectué' : 'Horaire fixé dans le plan'}</Text>}
        {point.kind !== 'none' && <Text style={ui.body}>{{ water: 'Eau', full: 'Ravitaillement', base: 'Base vie' }[point.kind]}</Text>}
        {(point.crew || point.drop_bag) && <Text style={ui.small}>{[point.crew && 'Assistance', point.drop_bag && 'Drop bag'].filter(Boolean).join(' · ')}</Text>}
        {!!point.cutoff_clock && <Text style={point.cutoff_margin_s !== null && point.cutoff_margin_s < 0 ? ui.error : ui.body}>Barrière {point.cutoff_clock}{point.cutoff_margin_s !== null ? ` · ${point.cutoff_margin_s < 0 ? 'retard' : 'marge'} ${Math.floor(Math.abs(point.cutoff_margin_s) / 60)} min` : ''}</Text>}
      </View>)}
      <Button text="Supprimer la copie" secondary onPress={() => Alert.alert('Supprimer la copie hors connexion ?', 'Le plan reste dans ton compte PaceForge.', [{ text: 'Annuler', style: 'cancel' }, { text: 'Supprimer', style: 'destructive', onPress: () => remove(plan.route_id) }])} />
    </> : <>
      <Text style={ui.body}>{plans.length ? 'Ces copies restent accessibles sans réseau.' : 'Ouvre une course avec une connexion, puis choisis « Garder hors connexion » dans le menu ⋯.'}</Text>
      {plans.map(p => <Pressable key={p.route_id} accessibilityRole="button" style={ui.card} onPress={() => select(p.route_id)}>
        <Text style={ui.cardTitle}>{p.name}</Text>
        <Text style={ui.small}>{p.distance_km.toLocaleString('fr-FR', { maximumFractionDigits: 1 })} km · Copie du {new Date(p.saved_at).toLocaleDateString('fr-FR')}</Text>
      </Pressable>)}
    </>}
  </ScrollView>;
}
