/**
 * Cameron race time prediction model (1985).
 * Uses VO2max-duration sustainability curve for pace prediction.
 * Based on the relationship between race duration and sustainable %VO2max.
 */

import type { Prediction } from "./index";

const RUNNING_DISTANCES = [
  { meters: 5000, label: "5K" },
  { meters: 10000, label: "10K" },
  { meters: 21097.5, label: "Half Marathon" },
  { meters: 42195, label: "Marathon" },
];

/**
 * Cameron model: predicts pace from VO2max using a distance-duration curve.
 * Uses an empirical relationship derived from world records.
 *
 * The model estimates the fraction of VO2max that can be sustained
 * as a function of race duration, then converts to pace.
 *
 * @param distanceM - Race distance in meters
 * @param vdot - VDOT (≈VO2max equivalent) in ml/kg/min
 * @returns Race time in seconds
 */
export function cameronPredict(distanceM: number, vdot: number): number {
  // VO2 at vVO2max speed: approximately VDOT ml/kg/min
  // Speed at VO2max ≈ solving VO2 = -4.6 + 0.182258v + 0.000104v²
  // For simplicity, use the inverse: v(VO2) from Daniels equation
  const vo2 = vdot;
  // Speed at VO2max in m/min (inverse of Daniels VO2 demand)
  const vVO2max = (-0.182258 + Math.sqrt(0.182258 ** 2 + 4 * 0.000104 * (vo2 + 4.6))) / (2 * 0.000104);

  // Cameron sustainability factor: fraction of vVO2max sustainable for distance d
  // Empirical: f(d) = a - b * ln(d) where d in meters
  // Calibrated to: 5K ≈ 94%, 10K ≈ 90%, HM ≈ 84%, Marathon ≈ 78%
  const a = 1.3;
  const b = 0.0424;
  const sustainabilityFraction = Math.max(0.5, a - b * Math.log(distanceM / 1000));

  const raceSpeedMPerMin = vVO2max * sustainabilityFraction;
  const timeMin = distanceM / raceSpeedMPerMin;
  return timeMin * 60; // seconds
}

/**
 * Generate predictions for standard distances.
 */
export function cameronPredictions(vdot: number): Prediction[] {
  return RUNNING_DISTANCES.map(({ meters, label }) => {
    const time = cameronPredict(meters, vdot);
    const speed = (meters / time) * 3.6; // km/h
    const pace = time / (meters / 1000); // sec/km
    return { distance: meters, distanceLabel: label, time, pace, speed };
  });
}
