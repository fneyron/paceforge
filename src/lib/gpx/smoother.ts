import type { RoutePoint } from "@/types/route";

/**
 * Smooth elevation data using a box filter (moving average).
 * Preserves lat/lon/distance, only smooths elevation.
 * Then computes grade for each point.
 */
export function smoothElevation(
  points: RoutePoint[],
  windowSize: number = 11
): RoutePoint[] {
  if (points.length < 3) return points;

  const halfWindow = Math.floor(windowSize / 2);
  const smoothed: RoutePoint[] = [];

  // Smooth elevation with moving average
  for (let i = 0; i < points.length; i++) {
    const start = Math.max(0, i - halfWindow);
    const end = Math.min(points.length - 1, i + halfWindow);
    let sum = 0;
    let count = 0;

    for (let j = start; j <= end; j++) {
      sum += points[j].ele;
      count++;
    }

    smoothed.push({
      ...points[i],
      ele: sum / count,
    });
  }

  // Compute grade for each point
  for (let i = 0; i < smoothed.length; i++) {
    if (i === 0) {
      smoothed[i].grade = 0;
    } else {
      const dDistance = smoothed[i].distance - smoothed[i - 1].distance;
      const dEle = smoothed[i].ele - smoothed[i - 1].ele;

      if (dDistance > 0) {
        // Clamp grade to reasonable values (-0.6 to 0.6 = -60% to 60%)
        smoothed[i].grade = Math.max(-0.6, Math.min(0.6, dEle / dDistance));
      } else {
        smoothed[i].grade = 0;
      }
    }
  }

  return smoothed;
}
