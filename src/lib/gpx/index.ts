import { parseGPX, type ParseResult } from "./parser";
import { fetchElevation } from "./elevation";
import { smoothElevation } from "./smoother";
import { detectSegments } from "./segmenter";
import { analyzeRoute } from "./analyzer";
import type { RoutePoint, RouteStats, Segment } from "@/types/route";
import type { FeatureCollection, LineString } from "geojson";

export interface GPXPipelineResult {
  name: string;
  geojson: FeatureCollection;
  points: RoutePoint[];
  segments: Segment[];
  stats: RouteStats;
}

/**
 * Full GPX processing pipeline:
 * parse → enrich elevation → smooth elevation → detect segments → analyze stats
 */
export async function processGPX(
  gpxString: string,
  smoothWindow: number = 11
): Promise<GPXPipelineResult> {
  // Step 1: Parse GPX to GeoJSON + raw points
  const { geojson, points: rawPoints, name } = parseGPX(gpxString);

  // Step 2: Enrich elevation from Open-Meteo if missing
  const enrichedPoints = await fetchElevation(rawPoints);

  // Update geojson coordinates with enriched elevations
  const feature = geojson.features[0];
  if (feature && feature.geometry.type === "LineString") {
    const geometry = feature.geometry as LineString;
    geometry.coordinates = geometry.coordinates.map((coord, i) => [
      coord[0],
      coord[1],
      enrichedPoints[i]?.ele ?? coord[2] ?? 0,
    ]);
  }

  // Step 3: Smooth elevation and compute grades
  const points = smoothElevation(enrichedPoints, smoothWindow);

  // Step 4: Detect segments (climbs, descents, flats)
  const segments = detectSegments(points);

  // Step 5: Compute aggregated stats
  const stats = analyzeRoute(points);

  return { name, geojson, points, segments, stats };
}

export { parseGPX, fetchElevation, smoothElevation, detectSegments, analyzeRoute };
