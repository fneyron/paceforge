import type { RoutePoint } from "@/types/route";

const ELEVATION_API = "https://api.open-meteo.com/v1/elevation";
const BATCH_SIZE = 100;
const MAX_RETRIES = 5;
const DELAY_BETWEEN_BATCHES_MS = 600;

/**
 * Detect if elevation data is missing (all zeros or quasi-identical values).
 */
function isElevationMissing(points: RoutePoint[]): boolean {
  if (points.length === 0) return false;

  const allZero = points.every((p) => p.ele === 0);
  if (allZero) return true;

  // Check if all elevations are quasi-identical (std deviation < 0.1m)
  const mean = points.reduce((sum, p) => sum + p.ele, 0) / points.length;
  const variance =
    points.reduce((sum, p) => sum + (p.ele - mean) ** 2, 0) / points.length;
  return Math.sqrt(variance) < 0.1;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Fetch elevation data from Open-Meteo Elevation API for a batch of coordinates.
 * Retries with exponential backoff on 429 (rate limit) errors.
 */
async function fetchElevationBatch(
  lats: number[],
  lons: number[]
): Promise<number[]> {
  const latParam = lats.map((l) => l.toFixed(6)).join(",");
  const lonParam = lons.map((l) => l.toFixed(6)).join(",");

  const url = `${ELEVATION_API}?latitude=${latParam}&longitude=${lonParam}`;

  for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
    const response = await fetch(url);

    if (response.ok) {
      const data = (await response.json()) as { elevation: number[] };
      return data.elevation;
    }

    if (response.status === 429 && attempt < MAX_RETRIES) {
      const backoff = Math.pow(2, attempt + 1) * 1500; // 3s, 6s, 12s, 24s, 48s
      console.warn(
        `Open-Meteo rate limited, retrying in ${backoff}ms (attempt ${attempt + 1}/${MAX_RETRIES})`
      );
      await sleep(backoff);
      continue;
    }

    throw new Error(
      `Open-Meteo Elevation API error: ${response.status} ${response.statusText}`
    );
  }

  throw new Error("Open-Meteo Elevation API: max retries exceeded");
}

/**
 * Enrich route points with elevation data from Open-Meteo when elevation is missing.
 *
 * For large GPX files, sub-samples every N points, fetches elevation for the
 * sampled points, then linearly interpolates elevation for the rest.
 */
export async function fetchElevation(
  points: RoutePoint[]
): Promise<RoutePoint[]> {
  if (points.length === 0) return points;
  if (!isElevationMissing(points)) return points;

  // Sub-sample to limit API calls (500 points = 5 batches max)
  const MAX_API_POINTS = 500;
  const step = Math.max(1, Math.ceil(points.length / MAX_API_POINTS));
  const sampledIndices: number[] = [];

  for (let i = 0; i < points.length; i += step) {
    sampledIndices.push(i);
  }
  // Always include the last point
  if (sampledIndices[sampledIndices.length - 1] !== points.length - 1) {
    sampledIndices.push(points.length - 1);
  }

  const sampledLats = sampledIndices.map((i) => points[i].lat);
  const sampledLons = sampledIndices.map((i) => points[i].lon);

  // Fetch elevations in batches of BATCH_SIZE with delay between requests
  const allElevations: number[] = [];
  for (let i = 0; i < sampledLats.length; i += BATCH_SIZE) {
    if (i > 0) await sleep(DELAY_BETWEEN_BATCHES_MS);
    const batchLats = sampledLats.slice(i, i + BATCH_SIZE);
    const batchLons = sampledLons.slice(i, i + BATCH_SIZE);
    const batchElevations = await fetchElevationBatch(batchLats, batchLons);
    allElevations.push(...batchElevations);
  }

  // Build a map of sampled index → elevation
  const elevationMap = new Map<number, number>();
  for (let i = 0; i < sampledIndices.length; i++) {
    elevationMap.set(sampledIndices[i], allElevations[i]);
  }

  // Interpolate elevation for non-sampled points
  const enrichedPoints = points.map((p) => ({ ...p }));

  for (let i = 0; i < sampledIndices.length; i++) {
    const idx = sampledIndices[i];
    enrichedPoints[idx].ele = elevationMap.get(idx)!;
  }

  // Linear interpolation between sampled points
  if (step > 1) {
    for (let s = 0; s < sampledIndices.length - 1; s++) {
      const startIdx = sampledIndices[s];
      const endIdx = sampledIndices[s + 1];
      const startEle = enrichedPoints[startIdx].ele;
      const endEle = enrichedPoints[endIdx].ele;

      for (let i = startIdx + 1; i < endIdx; i++) {
        const ratio = (i - startIdx) / (endIdx - startIdx);
        enrichedPoints[i].ele = startEle + ratio * (endEle - startEle);
      }
    }
  }

  return enrichedPoints;
}
