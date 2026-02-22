import type { RoutePoint, SportType } from "@/types/route";
import type { ActivityStreams } from "./streams";

const STRAVA_SPORT_MAP: Record<string, SportType> = {
  Ride: "cycling",
  VirtualRide: "cycling",
  GravelRide: "gravel",
  Run: "road_running",
  TrailRun: "trail",
  VirtualRun: "road_running",
  Swim: "swimming",
};

/**
 * Map a Strava sport type to a PaceForge SportType.
 */
export function mapStravaSport(stravaSport: string): SportType {
  return STRAVA_SPORT_MAP[stravaSport] || "cycling";
}

/**
 * Convert Strava streams to RoutePoint array.
 */
export function streamsToRoutePoints(streams: ActivityStreams): RoutePoint[] {
  const { latlng, altitude, distance } = streams;

  if (!latlng || latlng.length === 0) {
    throw new Error("Activity has no GPS data");
  }

  const points: RoutePoint[] = [];
  const len = latlng.length;

  for (let i = 0; i < len; i++) {
    const [lat, lon] = latlng[i];
    const ele = altitude?.[i] ?? 0;
    const dist = distance?.[i] ?? 0;

    let grade = 0;
    if (i > 0 && distance) {
      const dDist = (distance[i] ?? 0) - (distance[i - 1] ?? 0);
      const dEle = (altitude?.[i] ?? 0) - (altitude?.[i - 1] ?? 0);
      if (dDist > 0) {
        grade = Math.max(-0.6, Math.min(0.6, dEle / dDist));
      }
    }

    points.push({ lat, lon, ele, distance: dist, grade });
  }

  return points;
}

/**
 * Generate a minimal GPX string from Strava streams.
 */
export function streamsToGPX(streams: ActivityStreams, name: string): string {
  const { latlng, altitude } = streams;
  if (!latlng || latlng.length === 0) {
    throw new Error("Activity has no GPS data");
  }

  const trkpts = latlng
    .map((coords, i) => {
      const [lat, lon] = coords;
      const ele = altitude?.[i] ?? 0;
      return `      <trkpt lat="${lat}" lon="${lon}"><ele>${ele}</ele></trkpt>`;
    })
    .join("\n");

  return `<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="PaceForge">
  <metadata><name>${escapeXml(name)}</name></metadata>
  <trk>
    <name>${escapeXml(name)}</name>
    <trkseg>
${trkpts}
    </trkseg>
  </trk>
</gpx>`;
}

function escapeXml(str: string): string {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
