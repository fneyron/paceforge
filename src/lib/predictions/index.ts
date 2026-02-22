/**
 * Race prediction engine.
 * Running: VDOT-based Daniels equations
 * Cycling: FTP-based with power-to-speed model
 * Swimming: CSS-based with distance decay
 */

import { riegelPredict, vdotToMarathonSpeed } from "@/lib/physics/road-running";
import { solveSpeed } from "@/lib/physics/cycling";
import type { CyclingConfig } from "@/types/route";

// ── Types ──

export interface Prediction {
  distance: number;      // meters
  distanceLabel: string;  // "5K", "Marathon", etc.
  time: number;          // seconds
  pace: number;          // sec/km (running) or sec/100m (swimming)
  speed: number;         // km/h
}

// ── Running Predictions (Daniels VDOT) ──

const RUNNING_DISTANCES: Array<{ meters: number; label: string }> = [
  { meters: 5000, label: "5K" },
  { meters: 10000, label: "10K" },
  { meters: 21097.5, label: "Half Marathon" },
  { meters: 42195, label: "Marathon" },
];

/**
 * Daniels VO2 demand equation:
 *   VO2 = -4.6 + 0.182258v + 0.000104v²  (v in m/min)
 *
 * %VO2max sustainable for time t (minutes):
 *   %VO2max = 0.8 + 0.1894393·e^(-0.012778·t) + 0.2989558·e^(-0.1932605·t)
 *
 * VDOT = VO2_demand / %VO2max
 *
 * For prediction: find time t such that VO2_demand(d/t) / %VO2max(t) = VDOT
 */
function vo2Demand(speedMPerMin: number): number {
  return -4.6 + 0.182258 * speedMPerMin + 0.000104 * speedMPerMin * speedMPerMin;
}

function pctVo2Max(timeMin: number): number {
  return 0.8 + 0.1894393 * Math.exp(-0.012778 * timeMin) + 0.2989558 * Math.exp(-0.1932605 * timeMin);
}

/**
 * Predict race time for a given distance using VDOT.
 * Uses iterative bisection to find time.
 */
function predictRunningTime(distanceM: number, vdot: number): number {
  // Initial estimate from marathon speed
  const marathonSpeed = vdotToMarathonSpeed(vdot); // m/s
  const initialEstimate = distanceM / marathonSpeed; // seconds

  // Bisection search
  let lo = initialEstimate * 0.3;
  let hi = initialEstimate * 3.0;

  for (let iter = 0; iter < 100; iter++) {
    const mid = (lo + hi) / 2;
    const timeMin = mid / 60;
    const speedMPerMin = distanceM / timeMin;
    const demand = vo2Demand(speedMPerMin);
    const pct = pctVo2Max(timeMin);
    const computedVdot = demand / pct;

    if (Math.abs(computedVdot - vdot) < 0.01) return mid;

    if (computedVdot > vdot) {
      // Running too fast → need more time
      lo = mid;
    } else {
      hi = mid;
    }
  }

  return (lo + hi) / 2;
}

export function runningPredictions(vdot: number): Prediction[] {
  return RUNNING_DISTANCES.map(({ meters, label }) => {
    const time = predictRunningTime(meters, vdot);
    const speed = (meters / time) * 3.6; // km/h
    const pace = time / (meters / 1000); // sec/km

    return { distance: meters, distanceLabel: label, time, pace, speed };
  });
}

// ── Cycling Predictions (FTP-based) ──

interface CyclingPredictionInput {
  ftp: number;
  weight: number;
  bikeWeight: number;
  cda: number;
  crr: number;
  efficiency: number;
}

const CYCLING_DISTANCES: Array<{ meters: number; label: string; ftpPct: number; durationHoursApprox: number }> = [
  { meters: 20000, label: "20km TT", ftpPct: 1.05, durationHoursApprox: 0.5 },
  { meters: 40000, label: "40km TT", ftpPct: 1.0, durationHoursApprox: 1 },
  { meters: 90000, label: "90km", ftpPct: 0.85, durationHoursApprox: 2.5 },
  { meters: 180000, label: "180km", ftpPct: 0.75, durationHoursApprox: 5 },
];

export function cyclingPredictions(input: CyclingPredictionInput): Prediction[] {
  const config: CyclingConfig = {
    ftp: input.ftp,
    weight: input.weight,
    bikeWeight: input.bikeWeight,
    cda: input.cda,
    crr: input.crr,
    efficiency: input.efficiency,
    powerTargets: [],
  };

  return CYCLING_DISTANCES.map(({ meters, label, ftpPct }) => {
    const power = input.ftp * ftpPct;
    const speed = solveSpeed(power, 0, 100, config, 0); // flat, sea level, no wind
    const time = meters / speed;
    const speedKmh = speed * 3.6;
    const pace = time / (meters / 1000); // sec/km

    return { distance: meters, distanceLabel: label, time, pace, speed: speedKmh };
  });
}

// ── Swimming Predictions (CSS-based) ──

const SWIMMING_DISTANCES: Array<{ meters: number; label: string }> = [
  { meters: 400, label: "400m" },
  { meters: 750, label: "750m" },
  { meters: 1500, label: "1500m" },
  { meters: 1900, label: "1900m" },
  { meters: 3800, label: "3800m" },
];

/**
 * Swimming speed decays slightly with distance from CSS.
 * Speed = CSS_speed × (400/d)^0.04
 */
export function swimmingPredictions(css: number): Prediction[] {
  const cssSpeed = 100 / css; // m/s

  return SWIMMING_DISTANCES.map(({ meters, label }) => {
    const speed = cssSpeed * Math.pow(400 / meters, 0.04);
    const time = meters / speed;
    const pace = time / (meters / 100); // sec/100m
    const speedKmh = speed * 3.6;

    return { distance: meters, distanceLabel: label, time, pace, speed: speedKmh };
  });
}

// ── Formatting ──

export function formatPredictionTime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.round(seconds % 60);

  if (h > 0) {
    return `${h}:${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
  }
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export function formatPace(secPerUnit: number): string {
  const m = Math.floor(secPerUnit / 60);
  const s = Math.round(secPerUnit % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}
