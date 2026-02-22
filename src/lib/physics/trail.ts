import type { TrailConfig } from "@/types/route";

/**
 * Minetti (2002) metabolic cost polynomial.
 * C(i) = cost in J/kg/m as a function of gradient i (fraction, e.g. 0.1 = 10%)
 *
 * C(i) = 155.4·i⁵ - 30.4·i⁴ - 43.3·i³ + 46.3·i² + 19.5·i + 3.6
 */
export function minettiCost(gradient: number): number {
  const i = gradient;
  const cost =
    155.4 * Math.pow(i, 5) -
    30.4 * Math.pow(i, 4) -
    43.3 * Math.pow(i, 3) +
    46.3 * Math.pow(i, 2) +
    19.5 * i +
    3.6;

  // Minimum cost is around 2 J/kg/m (running on flat)
  return Math.max(2, cost);
}

/**
 * Compute running speed from metabolic power and gradient.
 *
 * P_flat = VMA(m/s) × C(0) = VMA × 3.6 J/kg/m
 * Speed = P_available / C(gradient)
 *
 * Where P_available = P_flat for the runner (can be adjusted for pack weight).
 */
export function computeTrailSpeed(
  gradient: number,
  config: TrailConfig
): number {
  const vmaMs = config.vma / 3.6; // km/h to m/s
  const flatCost = minettiCost(0); // ~3.6 J/kg/m
  const pFlat = vmaMs * flatCost;

  // Adjust for pack weight
  const massRatio = (config.weight + config.packWeight) / config.weight;

  const cost = minettiCost(gradient);
  const speed = pFlat / (cost * massRatio);

  // Clamp speed: min 0.5 m/s (~1.8 km/h) for very steep, max VMA
  return Math.max(0.5, Math.min(vmaMs, speed));
}

/**
 * Compute time for a trail segment.
 * Returns time in seconds.
 */
export function computeTrailSegmentTime(
  distance: number,
  averageGrade: number,
  config: TrailConfig
): number {
  const speed = computeTrailSpeed(averageGrade, config);
  return distance / speed;
}

/**
 * Compute pace (min/km) from speed (m/s).
 */
export function speedToPace(speedMs: number): number {
  if (speedMs <= 0) return 999;
  return 1000 / speedMs / 60;
}
