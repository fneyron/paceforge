import type { RoutePoint, RouteStats } from "@/types/route";

/**
 * Compute aggregated route statistics from processed points.
 */
export function analyzeRoute(points: RoutePoint[]): RouteStats {
  if (points.length === 0) {
    return {
      totalDistance: 0,
      elevationGain: 0,
      elevationLoss: 0,
      minElevation: 0,
      maxElevation: 0,
    };
  }

  let elevationGain = 0;
  let elevationLoss = 0;
  let minElevation = points[0].ele;
  let maxElevation = points[0].ele;

  for (let i = 1; i < points.length; i++) {
    const diff = points[i].ele - points[i - 1].ele;
    if (diff > 0) elevationGain += diff;
    else elevationLoss += Math.abs(diff);

    if (points[i].ele < minElevation) minElevation = points[i].ele;
    if (points[i].ele > maxElevation) maxElevation = points[i].ele;
  }

  const totalDistance = points[points.length - 1].distance;

  return {
    totalDistance: Math.round(totalDistance),
    elevationGain: Math.round(elevationGain),
    elevationLoss: Math.round(elevationLoss),
    minElevation: Math.round(minElevation),
    maxElevation: Math.round(maxElevation),
  };
}
