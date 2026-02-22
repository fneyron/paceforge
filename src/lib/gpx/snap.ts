import type { RoutePoint } from "@/types/route";

/**
 * Snap a lat/lon coordinate to the nearest point on the route.
 * Returns the route point closest to the given coordinate.
 */
export function snapToRoute(
  lat: number,
  lon: number,
  points: RoutePoint[]
): RoutePoint | null {
  if (points.length === 0) return null;

  let minDist = Infinity;
  let nearest: RoutePoint = points[0];

  for (const point of points) {
    // Simple Euclidean distance (good enough for nearby points)
    const dLat = point.lat - lat;
    const dLon = point.lon - lon;
    const dist = dLat * dLat + dLon * dLon;

    if (dist < minDist) {
      minDist = dist;
      nearest = point;
    }
  }

  return nearest;
}

/**
 * Snap a lat/lon coordinate to the nearest point on the route.
 * Returns the route point and the distance along the route.
 * Interpolates between segment endpoints for better accuracy.
 */
export function snapToRouteWithDistance(
  lat: number,
  lon: number,
  points: RoutePoint[]
): { point: RoutePoint; distance: number } | null {
  if (points.length === 0) return null;

  let minDist = Infinity;
  let bestPoint: RoutePoint = points[0];
  let bestDistance = 0;

  for (let i = 1; i < points.length; i++) {
    const p0 = points[i - 1];
    const p1 = points[i];

    // Project onto the segment [p0, p1]
    const dx = p1.lon - p0.lon;
    const dy = p1.lat - p0.lat;
    const segLenSq = dx * dx + dy * dy;

    let t = 0;
    if (segLenSq > 0) {
      t = ((lon - p0.lon) * dx + (lat - p0.lat) * dy) / segLenSq;
      t = Math.max(0, Math.min(1, t));
    }

    const projLon = p0.lon + t * dx;
    const projLat = p0.lat + t * dy;
    const distSq = (lon - projLon) ** 2 + (lat - projLat) ** 2;

    if (distSq < minDist) {
      minDist = distSq;
      const segDist = p1.distance - p0.distance;
      bestDistance = p0.distance + t * segDist;
      bestPoint = {
        lat: projLat,
        lon: projLon,
        ele: p0.ele + t * (p1.ele - p0.ele),
        distance: bestDistance,
        grade: p1.grade,
      };
    }
  }

  return { point: bestPoint, distance: bestDistance };
}

/**
 * Find the route point at a given distance along the route.
 * Interpolates between points if needed.
 */
export function pointAtDistance(
  distance: number,
  points: RoutePoint[]
): RoutePoint | null {
  if (points.length === 0) return null;
  if (distance <= 0) return points[0];
  if (distance >= points[points.length - 1].distance)
    return points[points.length - 1];

  for (let i = 1; i < points.length; i++) {
    if (points[i].distance >= distance) {
      const ratio =
        (distance - points[i - 1].distance) /
        (points[i].distance - points[i - 1].distance);

      return {
        lat: points[i - 1].lat + ratio * (points[i].lat - points[i - 1].lat),
        lon: points[i - 1].lon + ratio * (points[i].lon - points[i - 1].lon),
        ele: points[i - 1].ele + ratio * (points[i].ele - points[i - 1].ele),
        distance,
        grade: points[i].grade,
      };
    }
  }

  return points[points.length - 1];
}
