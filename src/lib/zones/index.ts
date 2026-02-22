/**
 * Training zone calculation engine.
 * Supports power (Coggan), pace (Daniels), HR, and swimming zones.
 */

// ── Power Zones (Coggan, 7 zones based on FTP) ──

export interface Zone {
  number: number;
  name: string;
  color: string;
  min: number;  // lower bound (inclusive)
  max: number;  // upper bound (exclusive, Infinity for last)
}

export const POWER_ZONE_DEFS: Omit<Zone, "min" | "max">[] = [
  { number: 1, name: "Recovery", color: "#9ca3af" },       // gray
  { number: 2, name: "Endurance", color: "#3b82f6" },      // blue
  { number: 3, name: "Tempo", color: "#22c55e" },          // green
  { number: 4, name: "Threshold", color: "#eab308" },      // yellow
  { number: 5, name: "VO2max", color: "#f97316" },         // orange
  { number: 6, name: "Anaerobic", color: "#ef4444" },      // red
  { number: 7, name: "Neuromuscular", color: "#8b5cf6" },  // violet
];

const POWER_ZONE_BOUNDS = [0, 0.55, 0.75, 0.90, 1.05, 1.20, 1.50, Infinity];

export function powerZones(ftp: number): Zone[] {
  return POWER_ZONE_DEFS.map((def, i) => ({
    ...def,
    min: Math.round(ftp * POWER_ZONE_BOUNDS[i]),
    max: POWER_ZONE_BOUNDS[i + 1] === Infinity
      ? Infinity
      : Math.round(ftp * POWER_ZONE_BOUNDS[i + 1]),
  }));
}

export function classifyPowerZone(watts: number, ftp: number): Zone {
  const ratio = watts / ftp;
  const zones = powerZones(ftp);
  for (let i = zones.length - 1; i >= 0; i--) {
    if (ratio >= POWER_ZONE_BOUNDS[i]) return zones[i];
  }
  return zones[0];
}

// ── Running Pace Zones (Jack Daniels, based on VDOT) ──

export const PACE_ZONE_DEFS: Omit<Zone, "min" | "max">[] = [
  { number: 1, name: "Easy", color: "#3b82f6" },           // blue
  { number: 2, name: "Marathon", color: "#22c55e" },        // green
  { number: 3, name: "Threshold", color: "#eab308" },       // yellow
  { number: 4, name: "Interval", color: "#f97316" },        // orange
  { number: 5, name: "Repetition", color: "#ef4444" },      // red
];

// %VO2max ranges for each zone
const PACE_ZONE_VO2_RANGES: [number, number][] = [
  [0.59, 0.74],  // Easy
  [0.75, 0.84],  // Marathon
  [0.83, 0.88],  // Threshold
  [0.95, 1.00],  // Interval
  [1.05, 1.10],  // Repetition
];

/**
 * VO2 demand equation (Daniels):
 *   VO2 = -4.6 + 0.182258v + 0.000104v²  (v in m/min)
 * Solve for v given VO2: quadratic formula
 */
function vo2ToSpeedMPerMin(vo2: number): number {
  const a = 0.000104;
  const b = 0.182258;
  const c = -4.6 - vo2;
  const disc = b * b - 4 * a * c;
  if (disc < 0) return 0;
  return (-b + Math.sqrt(disc)) / (2 * a);
}

function speedMPerMinToSecPerKm(vMPerMin: number): number {
  if (vMPerMin <= 0) return 999;
  return (1000 / vMPerMin) * 60; // sec/km
}

/**
 * Compute running pace zones from VDOT.
 * Returns zones with pace in sec/km (min = faster pace, max = slower pace).
 */
export function paceZones(vdot: number): Zone[] {
  return PACE_ZONE_DEFS.map((def, i) => {
    const [vo2Low, vo2High] = PACE_ZONE_VO2_RANGES[i];
    // Higher %VO2max → faster speed → lower sec/km
    const fastPace = speedMPerMinToSecPerKm(vo2ToSpeedMPerMin(vo2High * vdot));
    const slowPace = speedMPerMinToSecPerKm(vo2ToSpeedMPerMin(vo2Low * vdot));
    return {
      ...def,
      min: Math.round(fastPace),  // faster boundary (lower sec/km)
      max: Math.round(slowPace),  // slower boundary (higher sec/km)
    };
  });
}

export function classifyPaceZone(secPerKm: number, vdot: number): Zone {
  const zones = paceZones(vdot);
  // Zones are ordered from slow (Easy) to fast (Repetition)
  // secPerKm: lower = faster
  for (let i = zones.length - 1; i >= 0; i--) {
    if (secPerKm <= zones[i].max) return zones[i];
  }
  return zones[0]; // Easy (slowest)
}

// ── Heart Rate Zones (5 zones, %HRmax) ──

export const HR_ZONE_DEFS: Omit<Zone, "min" | "max">[] = [
  { number: 1, name: "Recovery", color: "#9ca3af" },     // gray
  { number: 2, name: "Aerobic", color: "#3b82f6" },      // blue
  { number: 3, name: "Tempo", color: "#22c55e" },        // green
  { number: 4, name: "Threshold", color: "#eab308" },    // yellow
  { number: 5, name: "Max", color: "#ef4444" },          // red
];

const HR_ZONE_PCT = [0, 0.68, 0.82, 0.87, 0.92, 1.0];

export function hrZones(fcMax: number, lactateThresholdHR?: number): Zone[] {
  return HR_ZONE_DEFS.map((def, i) => {
    let min = Math.round(fcMax * HR_ZONE_PCT[i]);
    let max = i < HR_ZONE_PCT.length - 2
      ? Math.round(fcMax * HR_ZONE_PCT[i + 1])
      : fcMax;

    // Anchor Z4 to lactate threshold HR if known
    if (lactateThresholdHR && i === 3) {
      min = Math.round(lactateThresholdHR * 0.95);
      max = Math.round(lactateThresholdHR * 1.05);
    }

    return { ...def, min, max };
  });
}

export function classifyHRZone(bpm: number, fcMax: number, lactateThresholdHR?: number): Zone {
  const zones = hrZones(fcMax, lactateThresholdHR);
  for (let i = zones.length - 1; i >= 0; i--) {
    if (bpm >= zones[i].min) return zones[i];
  }
  return zones[0];
}

// ── Swimming Zones (based on CSS in sec/100m) ──

export const SWIM_ZONE_DEFS: Omit<Zone, "min" | "max">[] = [
  { number: 1, name: "EN1", color: "#3b82f6" },          // blue
  { number: 2, name: "EN2", color: "#22c55e" },          // green
  { number: 3, name: "Threshold", color: "#eab308" },    // yellow
  { number: 4, name: "VO2max", color: "#f97316" },       // orange
  { number: 5, name: "Sprint", color: "#ef4444" },       // red
];

/**
 * Swimming zones based on CSS (sec/100m).
 * Returns zones with min/max as sec/100m (lower = faster).
 */
export function swimZones(css: number): Zone[] {
  return [
    { ...SWIM_ZONE_DEFS[0], min: css + 8, max: css + 15 },   // EN1: slow
    { ...SWIM_ZONE_DEFS[1], min: css + 2, max: css + 8 },    // EN2
    { ...SWIM_ZONE_DEFS[2], min: css - 2, max: css + 2 },    // Threshold
    { ...SWIM_ZONE_DEFS[3], min: css - 8, max: css - 2 },    // VO2max
    { ...SWIM_ZONE_DEFS[4], min: 0, max: css - 8 },          // Sprint: fastest
  ];
}

export function classifySwimZone(secPer100m: number, css: number): Zone {
  const zones = swimZones(css);
  // secPer100m: lower = faster → Sprint end
  if (secPer100m < css - 8) return zones[4]; // Sprint
  if (secPer100m < css - 2) return zones[3]; // VO2max
  if (secPer100m < css + 2) return zones[2]; // Threshold
  if (secPer100m < css + 8) return zones[1]; // EN2
  return zones[0]; // EN1
}

// ── Formatting helpers ──

export function formatPace(secPerKm: number): string {
  const min = Math.floor(secPerKm / 60);
  const sec = Math.round(secPerKm % 60);
  return `${min}:${sec.toString().padStart(2, "0")}`;
}

export function formatSwimPace(secPer100m: number): string {
  const min = Math.floor(secPer100m / 60);
  const sec = Math.round(secPer100m % 60);
  return `${min}:${sec.toString().padStart(2, "0")}`;
}
