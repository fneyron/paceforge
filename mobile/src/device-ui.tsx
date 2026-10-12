import React from 'react';
import { Pressable, StyleSheet, Text } from 'react-native';

export function Button({ text, onPress, disabled = false, secondary = false }: { text: string; onPress: () => void; disabled?: boolean; secondary?: boolean }) {
  return <Pressable accessibilityRole="button" accessibilityState={{ disabled }} disabled={disabled} onPress={onPress}
    style={[ui.button, secondary && ui.secondaryButton, disabled && ui.disabled]}>
    <Text style={[ui.buttonText, secondary && ui.secondaryText]}>{text}</Text>
  </Pressable>;
}
export const ui = StyleSheet.create({
  content: { padding: 20, paddingBottom: 36 }, title: { fontSize: 29, fontWeight: '700', color: '#173e38', marginBottom: 6 },
  body: { fontSize: 15, lineHeight: 23, color: '#41554e', marginVertical: 6 }, small: { color: '#62736d', fontSize: 13, lineHeight: 19, marginVertical: 6 },
  card: { padding: 18, backgroundColor: '#fff', borderRadius: 18, marginTop: 18, borderWidth: 1, borderColor: '#e4ebe6' },
  cardTitle: { fontSize: 19, fontWeight: '600', color: '#173e38', marginBottom: 5 },
  button: { backgroundColor: '#245e4e', minHeight: 48, justifyContent: 'center', paddingVertical: 13, paddingHorizontal: 14, borderRadius: 11, marginTop: 10 },
  buttonText: { color: '#fff', textAlign: 'center', fontSize: 15, fontWeight: '600' }, secondaryButton: { backgroundColor: '#e7f0ea' }, secondaryText: { color: '#173e38' }, disabled: { opacity: 0.45 },
  error: { color: '#a33129', fontSize: 15, lineHeight: 23, marginVertical: 8 },
  toggle: { flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 14 },
  link: { paddingVertical: 14, minHeight: 44 }, linkText: { color: '#245e4e', fontSize: 15, fontWeight: '600' },
});
