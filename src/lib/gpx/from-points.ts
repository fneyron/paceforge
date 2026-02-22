import * as turf from "@turf/turf";
import type { RoutePoint, RouteStats, Segment } from "@/types/route";
import type { FeatureCollection } from "geojson";
import { fetchElevation } from "./elevation";
import { smoothElevation } from "./smoother";
import { detectSegments } from "./segmenter";
import { analyzeRoute } from "./analyzer";

export interface DrawnRouteResult {
  geojson: FeatureCollection;
  points: RoutePoint[];
  segments: Segment[];
  stats: RouteStats;
  gpxString: string;
}

/**
 * Process an array of drawn lat/lon points into a full route.
 * Fetches elevation, smooths, detects segments, generates GeoJSON + GPX.
 */
export async function processDrawnRoute(
  rawPoints: { lat: number; lon: number }[],
  name: string = "Drawn Route"
): Promise<DrawnRouteResult> {
  if (rawPoints.length < 2) {
    throw new Error("Need at least 2 points to create a route");
  }

  // Build RoutePoint[] with cumulative distance
  let routePoints: RoutePoint[] = [];
  let cumulativeDistance = 0;

  for (let i = 0; i < rawPoints.length; i++) {
    if (i > 0) {
      const from = turf.point([rawPoints[i - 1].lon, rawPoints[i - 1].lat]);
      const to = turf.point([rawPoints[i].lon, rawPoints[i].lat]);
      cumulativeDistance += turf.distance(from, to, { units: "meters" });
    }

    routePoints.push({
      lat: rawPoints[i].lat,
      lon: rawPoints[i].lon,
      ele: 0,
      distance: cumulativeDistance,
      grade: 0,
    });
  }

  // Fetch elevation from Open-Meteo
  routePoints = await fetchElevation(routePoints);

  // Smooth elevation and compute grades
  routePoints = smoothElevation(routePoints);

  // Detect segments
  const segments = detectSegments(routePoints);

  // Compute stats
  const stats = analyzeRoute(routePoints);

  // Build GeoJSON
  const geojson: FeatureCollection = {
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        properties: {},
        geometry: {
          type: "LineString",
          coordinates: routePoints.map((p) => [p.lon, p.lat, p.ele]),
        },
      },
    ],
  };

  // Generate minimal GPX
  const trkpts = routePoints
    .map((p) => `      <trkpt lat="${p.lat}" lon="${p.lon}"><ele>${p.ele}</ele></trkpt>`)
    .join("\n");

  const gpxString = `<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="PaceForge">
  <metadata><name>${escapeXml(name)}</name></metadata>
  <trk>
    <name>${escapeXml(name)}</name>
    <trkseg>
${trkpts}
    </trkseg>
  </trk>
</gpx>`;

  return { geojson, points: routePoints, segments, stats, gpxString };
}

function escapeXml(str: string): string {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
