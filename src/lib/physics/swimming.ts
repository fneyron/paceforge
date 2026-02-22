import type { SwimmingConfig } from "@/types/route";

/**
 * Swimming simulation engine.
 * Based on Critical Swim Speed (CSS) model with corrections for
 * open water, wetsuit, temperature, and current.
 */

/**
 * Compute swimming speed from CSS and conditions.
 * CSS is in sec/100m → base speed in m/s = 100 / CSS
 *
 * Corrections:
 *   - Open water: -7% (sighting, navigation, waves)
 *   - Wetsuit: +4% (buoyancy, reduced drag)
 *   - Cold water (<15°C): -3% (muscle stiffness)
 *   - Warm water (>28°C): -2% (thermoregulation stress)
 *   - Current: additive (m/s)
 */
export function computeSwimSpeed(config: SwimmingConfig): number {
  let speed = 100 / config.css; // base speed in m/s

  // Open water penalty
  if (config.isOpenWater) {
    speed *= 0.93;
  }

  // Wetsuit bonus
  if (config.hasWetsuit) {
    speed *= 1.04;
  }

  // Temperature correction
  if (config.waterTemperature !== undefined) {
    if (config.waterTemperature < 15) {
      speed *= 0.97;
    } else if (config.waterTemperature > 28) {
      speed *= 0.98;
    }
  }

  // Current (positive = favorable)
  if (config.currentSpeed) {
    speed += config.currentSpeed;
  }

  return Math.max(0.3, speed); // Min ~18m/min
}

/**
 * Compute swimming segment time.
 * Returns time in seconds.
 */
export function computeSwimmingSegmentTime(
  distance: number,
  config: SwimmingConfig
): number {
  const speed = computeSwimSpeed(config);
  return distance / speed;
}

/**
 * Swimming calorie expenditure (MET-based).
 * Moderate freestyle: 6 MET, vigorous: 10 MET
 * kcal/hour = MET × weight × 1.05
 */
export function swimmingCalories(
  durationHours: number,
  weight: number,
  intensity: number = 0.8 // 0-1 scale
): number {
  const met = 6 + intensity * 4; // 6-10 MET range
  return met * weight * 1.05 * durationHours;
}
