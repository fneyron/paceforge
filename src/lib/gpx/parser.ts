import { DOMParser } from "@xmldom/xmldom";
import { gpx } from "@tmcw/togeojson";
import * as turf from "@turf/turf";
import type { RoutePoint } from "@/types/route";
import type { FeatureCollection } from "geojson";

export interface ParseResult {
  geojson: FeatureCollection;
  points: RoutePoint[];
  name: string;
}

export function parseGPX(gpxString: string): ParseResult {
  const parser = new DOMParser();
  const doc = parser.parseFromString(gpxString, "text/xml");
  const geojson = gpx(doc) as FeatureCollection;

  if (!geojson.features.length) {
    throw new Error("No track found in GPX file");
  }

  // Extract name from GPX metadata or first track
  const nameEl = doc.getElementsByTagName("name")[0];
  const name = nameEl?.textContent || "Unnamed Route";

  // Get the first LineString feature (track)
  const trackFeature = geojson.features.find(
    (f) =>
      f.geometry.type === "LineString" ||
      f.geometry.type === "MultiLineString"
  );

  if (!trackFeature) {
    throw new Error("No track geometry found in GPX file");
  }

  let coordinates: number[][];
  if (trackFeature.geometry.type === "MultiLineString") {
    coordinates = trackFeature.geometry.coordinates.flat();
  } else if (trackFeature.geometry.type === "LineString") {
    coordinates = trackFeature.geometry.coordinates;
  } else {
    throw new Error("Unsupported geometry type");
  }

  // Build RoutePoint[] with cumulative distance
  const points: RoutePoint[] = [];
  let cumulativeDistance = 0;

  for (let i = 0; i < coordinates.length; i++) {
    const [lon, lat, ele = 0] = coordinates[i];

    if (i > 0) {
      const from = turf.point([coordinates[i - 1][0], coordinates[i - 1][1]]);
      const to = turf.point([lon, lat]);
      const dist = turf.distance(from, to, { units: "meters" });
      cumulativeDistance += dist;
    }

    // Grade will be computed after smoothing
    points.push({
      lat,
      lon,
      ele,
      distance: cumulativeDistance,
      grade: 0,
    });
  }

  // Normalize geojson to a single LineString for display
  const normalizedGeojson: FeatureCollection = {
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        properties: { name },
        geometry: {
          type: "LineString",
          coordinates: coordinates.map(([lon, lat, ele]) => [lon, lat, ele || 0]),
        },
      },
    ],
  };

  return { geojson: normalizedGeojson, points, name };
}
