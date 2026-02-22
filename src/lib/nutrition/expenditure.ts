/**
 * Caloric expenditure estimation by sport.
 */

/**
 * Cycling expenditure from power output.
 * kcal/h ≈ power(W) × 3.6 / efficiency
 * Efficiency is typically 20-25%.
 */
export function cyclingExpenditure(
  powerWatts: number,
  efficiency: number = 0.24
): number {
  // kJ/h = power × 3.6, kcal = kJ / 4.184
  return (powerWatts * 3.6) / (4.184 * efficiency);
}

/**
 * Running expenditure from Minetti metabolic cost model.
 * kcal/km ≈ weight(kg) × cost(J/kg/m) × distance(m) / 4184
 *
 * Simplified: ~1 kcal/kg/km on flat terrain
 */
export function runningExpenditure(
  weightKg: number,
  distanceM: number,
  averageGrade: number = 0
): number {
  // Minetti flat cost ≈ 3.6 J/kg/m
  const baseCostJPerKgM = 3.6;
  // Grade correction (simplified)
  const gradeFactor = 1 + Math.abs(averageGrade) * 5;
  const totalJoules = weightKg * baseCostJPerKgM * gradeFactor * distanceM;
  return totalJoules / 4184; // J to kcal
}

/**
 * Swimming expenditure (MET-based).
 * Moderate freestyle: 6-10 MET depending on intensity.
 * kcal/h = MET × weight × 1.05
 */
export function swimmingExpenditure(
  weightKg: number,
  durationHours: number,
  intensity: number = 0.8 // 0-1
): number {
  const met = 6 + intensity * 4; // 6-10 MET
  return met * weightKg * 1.05 * durationHours;
}

/**
 * Total expenditure for a simulation based on splits.
 */
export function totalExpenditure(
  sport: string,
  totalTimeSeconds: number,
  weightKg: number,
  averagePower?: number,
  totalDistance?: number
): number {
  const hours = totalTimeSeconds / 3600;

  switch (sport) {
    case "cycling":
    case "gravel":
      return cyclingExpenditure(averagePower || 200) * hours;
    case "swimming":
      return swimmingExpenditure(weightKg, hours);
    case "trail":
    case "ultra_trail":
    case "road_running":
      return runningExpenditure(weightKg, totalDistance || 0);
    default:
      // Generic: 500 kcal/h baseline
      return 500 * hours;
  }
}
