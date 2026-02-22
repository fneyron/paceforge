import type { CyclingConfig } from "@/types/route";

const G = 9.81; // m/s²
const RHO_SEA_LEVEL = 1.225; // kg/m³

/**
 * Air density correction for altitude.
 * ρ(h) = 1.225 × exp(-h/8500)
 */
function airDensity(altitude: number): number {
  return RHO_SEA_LEVEL * Math.exp(-altitude / 8500);
}

/**
 * Solve for velocity given power, using Newton-Raphson.
 *
 * Power balance equation:
 *   P_rider × η = (m·g·sin(θ) + m·g·cos(θ)·Crr)·v + 0.5·ρ·CdA·(v+Vw)²·v
 *
 * Where:
 *   - P_rider: rider power output (watts)
 *   - η: drivetrain efficiency
 *   - m: total mass (rider + bike)
 *   - θ: slope angle (rad)
 *   - Crr: rolling resistance coefficient
 *   - ρ: air density (altitude-adjusted)
 *   - CdA: drag area
 *   - Vw: headwind speed (m/s, positive = headwind)
 *
 * Returns speed in m/s. Minimum speed clamp at 1 m/s (~3.6 km/h).
 */
export function solveSpeed(
  power: number,
  grade: number,
  altitude: number,
  config: CyclingConfig,
  headwind: number = 0
): number {
  const totalMass = config.weight + config.bikeWeight;
  const theta = Math.atan(grade);
  const rho = airDensity(altitude);
  const P = power * config.efficiency;

  // Forces that don't depend on velocity
  const gravityForce = totalMass * G * Math.sin(theta);
  const rollingForce = totalMass * G * Math.cos(theta) * config.crr;

  // Newton-Raphson to solve: P = (Fg + Fr)·v + 0.5·ρ·CdA·(v+Vw)²·v
  // f(v) = (Fg + Fr)·v + 0.5·ρ·CdA·(v+Vw)²·v - P = 0
  // f'(v) = (Fg + Fr) + 0.5·ρ·CdA·[3v² + 4v·Vw + Vw²]

  // Initial guess: flat-ground speed estimate
  let v = Math.max(1, Math.cbrt((2 * P) / (rho * config.cda)));

  for (let iter = 0; iter < 50; iter++) {
    const vw = v + headwind;
    const aeroForce = 0.5 * rho * config.cda * vw * vw;

    const f = (gravityForce + rollingForce) * v + aeroForce * v - P;
    const df =
      gravityForce +
      rollingForce +
      0.5 * rho * config.cda * (3 * v * v + 4 * v * headwind + headwind * headwind);

    if (Math.abs(df) < 1e-10) break;

    const newV = v - f / df;
    v = Math.max(0.5, newV); // Min ~1.8 km/h

    if (Math.abs(f) < 1e-6) break;
  }

  return Math.max(1, v); // Clamp minimum speed to ~3.6 km/h
}

/**
 * Compute time for a segment given power and cycling config.
 * Returns time in seconds.
 */
export function computeCyclingSegmentTime(
  distance: number,
  averageGrade: number,
  averageAltitude: number,
  power: number,
  config: CyclingConfig
): number {
  const speed = solveSpeed(power, averageGrade, averageAltitude, config);
  return distance / speed;
}
