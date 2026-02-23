import type { FatigueConfig } from "@/types/route";

/**
 * Fatigue model: exponential decay with minimum floor.
 *
 * F(t) = max(F_min, e^(-ln(2)·t / t_half))
 *
 * Where:
 *   - t: elapsed time in hours
 *   - t_half: half-life of performance (hours)
 *   - F_min: minimum performance factor (0-1)
 *
 * Returns a factor between F_min and 1.0 to multiply speed/power by.
 */
export function fatigueFactor(
  elapsedHours: number,
  config: FatigueConfig
): number {
  const decay = Math.exp((-Math.LN2 * elapsedHours) / config.halfLife);
  return Math.max(config.minFactor, decay);
}

/** Default fatigue configs per sport */
export const DEFAULT_FATIGUE: Record<string, FatigueConfig> = {
  cycling: { halfLife: 5, minFactor: 0.7 },
  gravel: { halfLife: 6, minFactor: 0.65 },
  trail: { halfLife: 10, minFactor: 0.6 },
  ultra_trail: { halfLife: 15, minFactor: 0.5 },
  road_running: { halfLife: 4, minFactor: 0.75 },
  swimming: { halfLife: 3, minFactor: 0.8 },
  triathlon: { halfLife: 8, minFactor: 0.6 },
  cross_country_skiing: { halfLife: 4, minFactor: 0.65 },
  rowing: { halfLife: 3, minFactor: 0.7 },
  duathlon: { halfLife: 6, minFactor: 0.65 },
  swimrun: { halfLife: 8, minFactor: 0.6 },
};
