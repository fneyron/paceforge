import React from "react";
import {
  Document,
  Page,
  Text,
  View,
  StyleSheet,
} from "@react-pdf/renderer";
import type { SplitResult } from "@/types/route";
import type { WeatherCondition } from "@/types/route";

const styles = StyleSheet.create({
  page: { padding: 30, fontFamily: "Helvetica", fontSize: 10 },
  title: { fontSize: 18, fontWeight: "bold", marginBottom: 10 },
  subtitle: { fontSize: 14, fontWeight: "bold", marginBottom: 8, marginTop: 12 },
  row: { flexDirection: "row", borderBottomWidth: 0.5, borderColor: "#ddd", padding: 3 },
  headerRow: { flexDirection: "row", borderBottomWidth: 1, borderColor: "#333", padding: 3, fontWeight: "bold" },
  cell: { flex: 1, fontSize: 8 },
  stat: { flexDirection: "row", marginBottom: 4 },
  statLabel: { width: 120, color: "#666" },
  statValue: { fontWeight: "bold" },
  footer: { position: "absolute", bottom: 20, left: 30, right: 30, fontSize: 7, color: "#999", textAlign: "center" },
});

function formatTime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}h${m.toString().padStart(2, "0")}m${s.toString().padStart(2, "0")}s`;
  return `${m}m${s.toString().padStart(2, "0")}s`;
}

interface Props {
  routeName: string;
  sport: string;
  totalDistance: number;
  elevationGain: number;
  elevationLoss: number;
  splits: SplitResult[];
  totalTime: number;
  weather?: WeatherCondition[];
}

export function RacePlanPDF({
  routeName,
  sport,
  totalDistance,
  elevationGain,
  elevationLoss,
  splits,
  totalTime,
  weather,
}: Props) {
  let cumulativeTime = 0;

  return (
    <Document>
      <Page size="A4" style={styles.page}>
        <Text style={styles.title}>{routeName}</Text>
        <Text style={{ fontSize: 10, color: "#666", marginBottom: 12 }}>
          PaceForge Race Plan — {sport.replace("_", " ").toUpperCase()}
        </Text>

        <Text style={styles.subtitle}>Route Summary</Text>
        <View style={styles.stat}>
          <Text style={styles.statLabel}>Distance:</Text>
          <Text style={styles.statValue}>
            {(totalDistance / 1000).toFixed(1)} km
          </Text>
        </View>
        <View style={styles.stat}>
          <Text style={styles.statLabel}>Elevation Gain:</Text>
          <Text style={styles.statValue}>{Math.round(elevationGain)} m</Text>
        </View>
        <View style={styles.stat}>
          <Text style={styles.statLabel}>Elevation Loss:</Text>
          <Text style={styles.statValue}>{Math.round(elevationLoss)} m</Text>
        </View>
        <View style={styles.stat}>
          <Text style={styles.statLabel}>Estimated Time:</Text>
          <Text style={styles.statValue}>{formatTime(totalTime)}</Text>
        </View>

        <Text style={styles.subtitle}>Splits</Text>
        <View style={styles.headerRow}>
          <Text style={[styles.cell, { flex: 0.5 }]}>#</Text>
          <Text style={styles.cell}>Dist (km)</Text>
          <Text style={styles.cell}>D+ (m)</Text>
          <Text style={styles.cell}>Time</Text>
          <Text style={styles.cell}>Cumul.</Text>
          <Text style={styles.cell}>Speed</Text>
          <Text style={styles.cell}>Pace</Text>
        </View>
        {splits.map((split, i) => {
          cumulativeTime += split.time;
          return (
            <View key={i} style={styles.row}>
              <Text style={[styles.cell, { flex: 0.5 }]}>{i + 1}</Text>
              <Text style={styles.cell}>
                {(split.distance / 1000).toFixed(1)}
              </Text>
              <Text style={styles.cell}>
                {split.elevationGain.toFixed(0)}
              </Text>
              <Text style={styles.cell}>{formatTime(split.time)}</Text>
              <Text style={styles.cell}>{formatTime(cumulativeTime)}</Text>
              <Text style={styles.cell}>
                {(split.speed * 3.6).toFixed(1)}
              </Text>
              <Text style={styles.cell}>{split.pace.toFixed(1)}</Text>
            </View>
          );
        })}

        {weather && weather.length > 0 && (
          <>
            <Text style={styles.subtitle}>Weather Conditions</Text>
            <View style={styles.headerRow}>
              <Text style={styles.cell}>Distance</Text>
              <Text style={styles.cell}>Temp</Text>
              <Text style={styles.cell}>Wind</Text>
              <Text style={styles.cell}>Precip</Text>
            </View>
            {weather.map((wx, i) => (
              <View key={i} style={styles.row}>
                <Text style={styles.cell}>
                  {(wx.distance / 1000).toFixed(0)} km
                </Text>
                <Text style={styles.cell}>{wx.temperature.toFixed(0)}°C</Text>
                <Text style={styles.cell}>
                  {wx.windSpeed.toFixed(1)} m/s
                </Text>
                <Text style={styles.cell}>
                  {wx.precipitation > 0 ? `${wx.precipitation.toFixed(1)} mm` : "—"}
                </Text>
              </View>
            ))}
          </>
        )}

        <Text style={styles.footer}>
          Generated by PaceForge — {new Date().toLocaleDateString()}
        </Text>
      </Page>
    </Document>
  );
}
