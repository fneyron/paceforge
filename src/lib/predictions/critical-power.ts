/**
 * Critical Power / W' model for cycling time predictions.
 * CP (Critical Power) = sustainable aerobic power (~96% FTP)
 * W' (W prime) = anaerobic work capacity in joules (typically 15-25 kJ)
 *
 * Time to exhaustion: t_lim = W' / (P - CP) for P > CP
 * For a distance: iteratively solve T = D / speed(CP + W'/T)
 */

import type { Prediction } from "./index";
import { solveSpeed } from "@/lib/physics/cycling";
import type { CyclingConfig } from "@/types/route";

const CYCLING_DISTANCES = [
  { meters: 4000, label: "4km Pursuit" },
  { meters: 20000, label: "20km TT" },
  { meters: 40000, label: "40km TT" },
  { meters: 90000, label: "90km" },
  { meters: 180000, label: "180km" },
];

/**
 * For short efforts (< ~40 min), power = CP + W'/t
 * For long efforts, power decays below CP due to fatigue.
 * We model long efforts as: P(t) = CP * decayFactor(t)
 * where decay accounts for fueling, hydration, etc.
 */
function sustainablePower(cp: number, wPrime: number, durationS: number): number {
  if (durationS <= 0) return cp * 2;

  // For efforts under ~30 min, W' model applies
  const wPrimePower = wPrime / durationS;
  const totalPower = cp + wPrimePower;

  // For very long efforts, cap at CP and apply sub-CP decay
  if (durationS > 7200) {
    // After 2h, gradual decay below CP
    const hoursOver2 = (durationS - 7200) / 3600;
    const decayFactor = Math.max(0.7, 1.0 - 0.03 * hoursOver2);
    return Math.min(totalPower, cp * decayFactor);
  }

  return totalPower;
}

/**
 * Iteratively solve for race time given distance, CP, W', and bike config.
 */
function solveTimeForDistance(
  distanceM: number,
  cp: number,
  wPrime: number,
  config: CyclingConfig
): number {
  // Initial estimate: distance / speed at CP
  const cpSpeed = solveSpeed(cp, 0, 100, config, 0);
  let timeEstimate = distanceM / cpSpeed;

  // Newton-Raphson style iteration
  for (let i = 0; i < 50; i++) {
    const power = sustainablePower(cp, wPrime, timeEstimate);
    const speed = solveSpeed(power, 0, 100, config, 0);
    const newTime = distanceM / speed;

    if (Math.abs(newTime - timeEstimate) < 0.1) break;
    timeEstimate = (timeEstimate + newTime) / 2; // damped update
  }

  return timeEstimate;
}

/**
 * Generate cycling predictions using CP/W' model.
 */
export function cpPredictions(
  cp: number,
  wPrime: number,
  config: CyclingConfig
): Prediction[] {
  return CYCLING_DISTANCES.map(({ meters, label }) => {
    const time = solveTimeForDistance(meters, cp, wPrime, config);
    const speed = (meters / time) * 3.6; // km/h
    const pace = time / (meters / 1000); // sec/km
    return { distance: meters, distanceLabel: label, time, pace, speed };
  });
}

/**
 * Estimate CP and W' from FTP.
 * CP ≈ 96% of FTP, W' ≈ 20 kJ (default for recreational cyclist)
 */
export function estimateCPFromFTP(ftp: number): { cp: number; wPrime: number } {
  return {
    cp: ftp * 0.96,
    wPrime: 20000, // 20 kJ default
  };
}
