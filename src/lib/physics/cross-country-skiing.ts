/**
 * Cross-Country Skiing simulation engine.
 * Power-based model similar to cycling but with snow friction.
 *
 * P_mechanical = (m·g·sin(θ) + μ·m·g·cos(θ))·v + 0.5·ρ·CdA·v³
 * P_metabolic = VO2max × weight / 60 × 20.9 (J/L O2) × efficiency × sustainability
 */

import type { CrossCountrySkiingConfig } from "@/types/route";

const G = 9.81; // gravity m/s²
const RHO_AIR = 1.225; // sea level air density kg/m³

/** CdA defaults by technique */
const TECHNIQUE_CDA: Record<string, number> = {
  classic: 0.5,
  skating: 0.45,
};

/** Efficiency defaults by technique */
const TECHNIQUE_EFFICIENCY: Record<string, number> = {
  classic: 0.17,
  skating: 0.19,
};

/**
 * Snow friction coefficient adjusted for temperature.
 * Optimal glide around -5 to -10°C. Worse at warm (sticky) or very cold (dry/brittle) temps.
 */
function adjustedSnowFriction(baseFriction: number, tempC?: number): number {
  if (tempC === undefined) return baseFriction;
  // Optimal around -8°C, penalty ~20% at 0°C or -20°C
  const optimalTemp = -8;
  const deviation = Math.abs(tempC - optimalTemp);
  const penalty = 1 + 0.015 * deviation; // ~1.5% per degree from optimal
  return baseFriction * penalty;
}

/**
 * Compute maximum sustainable mechanical power from VO2max.
 * @param vo2max - ml/kg/min
 * @param weight - kg
 * @param technique - classic or skating
 * @param durationHours - for sustainability factor
 */
function maxMechanicalPower(
  vo2max: number,
  weight: number,
  technique: string,
  durationHours: number = 1
): number {
  // VO2max in L/min
  const vo2maxLPerMin = (vo2max * weight) / 1000;
  // Metabolic power in watts (20.9 kJ per liter O2 → 20900 / 60 = 348.3 W per L/min)
  const metabolicPower = vo2maxLPerMin * 348.3;
  // Mechanical efficiency
  const efficiency = TECHNIQUE_EFFICIENCY[technique] ?? 0.18;
  // Sustainability factor: ~85% for 1h, decreasing for longer
  const sustainability = Math.max(0.6, 0.85 - 0.03 * Math.max(0, durationHours - 1));
  return metabolicPower * efficiency * sustainability;
}

/**
 * Solve speed given power, grade, and config using Newton-Raphson.
 */
export function solveXCSkiSpeed(
  power: number,
  grade: number,
  config: CrossCountrySkiingConfig
): number {
  const mass = config.weight;
  const mu = adjustedSnowFriction(config.snowFriction, config.temperature);
  const cda = TECHNIQUE_CDA[config.technique] ?? 0.5;
  const sinTheta = Math.sin(Math.atan(grade));
  const cosTheta = Math.cos(Math.atan(grade));

  // P = (m·g·sinθ + μ·m·g·cosθ)·v + 0.5·ρ·CdA·v³
  // For steep downhills, constrain to max safe speed
  const gravResist = mass * G * sinTheta;
  const frictionResist = mu * mass * G * cosTheta;
  const linearCoeff = gravResist + frictionResist;

  // Newton-Raphson: f(v) = linearCoeff·v + 0.5·ρ·CdA·v³ - P = 0
  let v = 5.0; // initial guess: 18 km/h
  for (let i = 0; i < 50; i++) {
    const f = linearCoeff * v + 0.5 * RHO_AIR * cda * v * v * v - power;
    const fp = linearCoeff + 1.5 * RHO_AIR * cda * v * v;
    if (Math.abs(fp) < 1e-12) break;
    const newV = v - f / fp;
    if (Math.abs(newV - v) < 0.001) {
      v = newV;
      break;
    }
    v = Math.max(0.5, newV);
  }

  // Clamp speed: min 0.5 m/s, max ~70 km/h for downhills
  return Math.max(0.5, Math.min(v, 19.5));
}

/**
 * Compute segment time for XC skiing.
 */
export function computeXCSkiSegmentTime(
  distance: number,
  grade: number,
  config: CrossCountrySkiingConfig,
  elapsedHours: number = 0
): number {
  const power = maxMechanicalPower(
    config.vo2max,
    config.weight,
    config.technique,
    elapsedHours
  );
  const speed = solveXCSkiSpeed(power, grade, config);
  return distance / speed;
}
