import type { RoadRunningConfig } from "@/types/route";
import { minettiCost } from "./trail";

/**
 * Road running simulation engine.
 * Based on VDOT tables (Jack Daniels) + Riegel formula + thermal correction.
 */

/**
 * Daniels VDOT → VO2max equivalent.
 * VDOT effectively IS the VO2max equivalent for race prediction.
 */

/**
 * VDOT to flat running speed at various intensities.
 * Returns speed in m/s.
 *
 * Based on Jack Daniels' tables:
 *   - Easy pace: ~65-70% VO2max
 *   - Marathon pace: ~80% VO2max
 *   - Threshold pace: ~88% VO2max
 *   - Interval pace: ~95-98% VO2max
 *   - Repetition pace: ~105% VO2max (supramaximal)
 */
export function vdotToMarathonSpeed(vdot: number): number {
  // Approximate: VDOT → marathon time (minutes) → speed
  // Marathon time ≈ 460 × (42/VDOT)^1.06 (rough approximation)
  // More precisely, use the velocity equation from Daniels:
  // VO2 = -4.6 + 0.182258v + 0.000104v² (v in m/min)
  // At marathon: ~80% VO2max → VO2 = 0.80 × VDOT

  const vo2 = 0.80 * vdot;
  // Solve for v: 0.000104v² + 0.182258v + (-4.6 - vo2) = 0
  const a = 0.000104;
  const b = 0.182258;
  const c = -4.6 - vo2;
  const discriminant = b * b - 4 * a * c;
  const vMPerMin = (-b + Math.sqrt(discriminant)) / (2 * a);

  return vMPerMin / 60; // m/min → m/s
}

/**
 * VDOT to threshold (tempo) speed in m/s.
 * ~88% VO2max intensity.
 */
export function vdotToThresholdSpeed(vdot: number): number {
  const vo2 = 0.88 * vdot;
  const a = 0.000104;
  const b = 0.182258;
  const c = -4.6 - vo2;
  const discriminant = b * b - 4 * a * c;
  const vMPerMin = (-b + Math.sqrt(discriminant)) / (2 * a);
  return vMPerMin / 60;
}

/**
 * Riegel formula for distance/time prediction.
 * T2 = T1 × (D2/D1)^1.06
 */
export function riegelPredict(
  knownDistance: number,
  knownTime: number,
  targetDistance: number,
  exponent: number = 1.06
): number {
  return knownTime * Math.pow(targetDistance / knownDistance, exponent);
}

/**
 * Thermal correction factor (Ely et al., 2007).
 * Performance degrades progressively above 15°C.
 *
 * Returns a factor between 0.85 and 1.0.
 * At 15°C: 1.0 (optimal)
 * At 25°C: ~0.97
 * At 35°C: ~0.92
 */
export function thermalCorrectionFactor(
  temperature?: number,
  humidity?: number
): number {
  if (temperature === undefined) return 1.0;

  // No penalty below 15°C (could add cold penalty, but less documented)
  if (temperature <= 15) return 1.0;

  // Base thermal degradation: ~0.3% per degree above 15°C
  let degradation = (temperature - 15) * 0.003;

  // Humidity amplifies heat effect
  if (humidity !== undefined && humidity > 50) {
    degradation *= 1 + (humidity - 50) / 200; // up to 25% more degradation at 100% humidity
  }

  return Math.max(0.85, 1 - degradation);
}

/**
 * Compute road running speed for a given gradient and VDOT.
 * Uses Minetti cost model for grade effect + VDOT for base fitness.
 */
export function computeRoadRunningSpeed(
  gradient: number,
  config: RoadRunningConfig
): number {
  // Base flat speed from VDOT (marathon intensity as race pace baseline)
  const flatSpeed = vdotToMarathonSpeed(config.vdot);

  // Use Minetti cost model for grade effect
  const flatCost = minettiCost(0); // ~3.6 J/kg/m
  const gradeCost = minettiCost(gradient);

  let speed = flatSpeed * (flatCost / gradeCost);

  // Apply thermal correction
  const thermalFactor = thermalCorrectionFactor(
    config.temperature,
    config.humidity
  );
  speed *= thermalFactor;

  // Clamp: min 0.5 m/s, max sprint speed
  return Math.max(0.5, Math.min(flatSpeed * 1.2, speed));
}

/**
 * Compute time for a road running segment.
 * Returns time in seconds.
 */
export function computeRoadRunningSegmentTime(
  distance: number,
  averageGrade: number,
  config: RoadRunningConfig
): number {
  const speed = computeRoadRunningSpeed(averageGrade, config);
  return distance / speed;
}
