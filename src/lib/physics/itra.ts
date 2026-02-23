/**
 * ITRA (International Trail Running Association) effort calculator.
 * Calculates effort points, race classification, and performance index.
 */

export interface ITRAClassification {
  category: string;
  stars: number;
}

/**
 * Compute ITRA effort points.
 * Formula: points = distance_km + elevation_gain_m / 100
 */
export function computeITRAPoints(distanceKm: number, elevationGainM: number): number {
  return distanceKm + elevationGainM / 100;
}

/**
 * Classify ITRA race by effort points.
 * Based on official ITRA classification.
 */
export function classifyITRA(points: number): ITRAClassification {
  if (points < 25) return { category: "XXS", stars: 1 };
  if (points < 44) return { category: "XS", stars: 1 };
  if (points < 64) return { category: "S", stars: 2 };
  if (points < 94) return { category: "M", stars: 3 };
  if (points < 134) return { category: "L", stars: 4 };
  if (points < 184) return { category: "XL", stars: 5 };
  return { category: "XXL", stars: 6 };
}

/**
 * Compute ITRA Performance Index.
 * Based on the ratio of km-effort per hour vs reference elite speed.
 * Elite reference: ~12 km-effort/hour for top male runners.
 */
export function computePerformanceIndex(
  itraPoints: number,
  timeHours: number
): number {
  if (timeHours <= 0) return 0;
  const kmEffortPerHour = itraPoints / timeHours;
  // Scale: 1000 = elite (12 km-effort/h), linear down
  // Approximate ITRA PI formula
  return Math.round(Math.min(1000, (kmEffortPerHour / 12) * 1000));
}
