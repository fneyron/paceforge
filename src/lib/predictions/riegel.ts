/**
 * Riegel race time prediction model.
 * T2 = T1 × (D2/D1)^exponent
 * Default exponent: 1.06 (Riegel 1981)
 */

import type { Prediction } from "./index";

const RUNNING_DISTANCES = [
  { meters: 5000, label: "5K" },
  { meters: 10000, label: "10K" },
  { meters: 21097.5, label: "Half Marathon" },
  { meters: 42195, label: "Marathon" },
];

/**
 * Predict race time using Riegel formula.
 * @param refDistanceM - Reference race distance in meters
 * @param refTimeS - Reference race time in seconds
 * @param targetDistanceM - Target distance in meters
 * @param exponent - Riegel exponent (default 1.06)
 */
export function riegelPredict(
  refDistanceM: number,
  refTimeS: number,
  targetDistanceM: number,
  exponent: number = 1.06
): number {
  return refTimeS * Math.pow(targetDistanceM / refDistanceM, exponent);
}

/**
 * Generate predictions for standard distances from a reference race.
 */
export function riegelPredictions(
  refDistanceM: number,
  refTimeS: number,
  exponent: number = 1.06
): Prediction[] {
  return RUNNING_DISTANCES.map(({ meters, label }) => {
    const time = riegelPredict(refDistanceM, refTimeS, meters, exponent);
    const speed = (meters / time) * 3.6; // km/h
    const pace = time / (meters / 1000); // sec/km
    return { distance: meters, distanceLabel: label, time, pace, speed };
  });
}
