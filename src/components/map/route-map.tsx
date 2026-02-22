"use client";

import { useEffect, useRef, useCallback, useState } from "react";
import { Map, Source, Layer, Marker } from "@vis.gl/react-maplibre";
import type { MapRef, MapLayerMouseEvent } from "@vis.gl/react-maplibre";
import * as turf from "@turf/turf";
import { useRouteStore } from "@/store/route-store";
import { useCursorStore } from "@/store/cursor-store";
import { snapToRouteWithDistance } from "@/lib/gpx/snap";
import "maplibre-gl/dist/maplibre-gl.css";
import type { RoutePoint } from "@/types/route";
import type * as GeoJSON from "geojson";

const STREETS_STYLE = "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json";

// ESRI World Imagery satellite tiles as a MapLibre style object
const SATELLITE_STYLE = {
  version: 8 as const,
  sources: {
    satellite: {
      type: "raster" as const,
      tiles: [
        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      ],
      tileSize: 256,
      maxzoom: 19,
      attribution: "Esri, Maxar, Earthstar Geographics",
    },
  },
  layers: [
    {
      id: "satellite-tiles",
      type: "raster" as const,
      source: "satellite",
      minzoom: 0,
      maxzoom: 19,
    },
  ],
};

type MapStyleKey = "streets" | "satellite";

function getPointAtDistance(
  points: RoutePoint[],
  distance: number
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

export function RouteMap() {
  const mapRef = useRef<MapRef>(null);
  const geojson = useRouteStore((s) => s.geojson);
  const points = useRouteStore((s) => s.points);
  const segments = useRouteStore((s) => s.segments);
  const waypoints = useRouteStore((s) => s.waypoints);
  const routeId = useRouteStore((s) => s.routeId);
  const updateWaypoint = useRouteStore((s) => s.updateWaypoint);
  const cursorDistance = useCursorStore((s) => s.distance);
  const cursorSource = useCursorStore((s) => s.source);
  const setDistance = useCursorStore((s) => s.setDistance);
  const [mapStyle, setMapStyle] = useState<MapStyleKey>("streets");

  // Fit map to route bounds
  useEffect(() => {
    if (!geojson || !mapRef.current) return;

    // Small delay to ensure map is ready
    const timer = setTimeout(() => {
      if (!mapRef.current || !geojson) return;
      try {
        const bbox = turf.bbox(geojson);
        mapRef.current.fitBounds(
          [
            [bbox[0], bbox[1]],
            [bbox[2], bbox[3]],
          ],
          { padding: 50, duration: 500 }
        );
      } catch {
        // ignore bbox errors
      }
    }, 100);

    return () => clearTimeout(timer);
  }, [geojson]);

  // Build colored segment features
  const segmentFeatures = useCallback(() => {
    if (!points.length || !segments.length) return null;

    const features = segments.map((seg) => {
      const segPoints = points.slice(seg.startIndex, seg.endIndex + 1);
      const color =
        seg.type === "climb"
          ? "#ef4444" // red
          : seg.type === "descent"
            ? "#3b82f6" // blue
            : "#22c55e"; // green

      return {
        type: "Feature" as const,
        properties: { color, type: seg.type, grade: seg.averageGrade },
        geometry: {
          type: "LineString" as const,
          coordinates: segPoints.map((p) => [p.lon, p.lat]),
        },
      };
    });

    return {
      type: "FeatureCollection" as const,
      features,
    };
  }, [points, segments]);

  const handleMouseMove = useCallback(
    (e: MapLayerMouseEvent) => {
      if (!points.length || !geojson) return;

      const clickPoint = turf.point([e.lngLat.lng, e.lngLat.lat]);
      const feature = geojson.features[0];
      if (!feature || feature.geometry.type !== "LineString") return;

      const line = feature as GeoJSON.Feature<GeoJSON.LineString>;
      const snapped = turf.nearestPointOnLine(line, clickPoint, {
        units: "meters",
      });
      if (snapped.properties.location != null) {
        setDistance(snapped.properties.location, "map");
      }
    },
    [points, geojson, setDistance]
  );

  const handleMouseLeave = useCallback(() => {
    useCursorStore.getState().clear();
  }, []);

  const handleWaypointDragEnd = useCallback(
    async (wpId: string, lngLat: { lng: number; lat: number }) => {
      if (!points.length || !routeId) return;

      const result = snapToRouteWithDistance(lngLat.lat, lngLat.lng, points);
      if (!result) return;

      const { point, distance } = result;

      // Update store
      updateWaypoint(wpId, {
        lat: point.lat,
        lon: point.lon,
        ele: point.ele,
        distance,
      });

      // Persist
      await fetch(`/api/routes/${routeId}/waypoints`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          id: wpId,
          lat: point.lat,
          lon: point.lon,
          ele: point.ele,
          distance,
        }),
      });
    },
    [points, routeId, updateWaypoint]
  );

  // Cursor position on map (when hovering profile)
  const cursorPoint =
    cursorSource === "profile" && cursorDistance != null
      ? getPointAtDistance(points, cursorDistance)
      : null;

  const segData = segmentFeatures();

  return (
    <div className="relative w-full h-full">
      <Map
        ref={mapRef}
        initialViewState={{
          longitude: 2.3,
          latitude: 46.5,
          zoom: 5,
        }}
        style={{ width: "100%", height: "100%" }}
        mapStyle={mapStyle === "satellite" ? SATELLITE_STYLE : STREETS_STYLE}
        onMouseMove={handleMouseMove}
        onMouseLeave={handleMouseLeave}
        interactiveLayerIds={["route-line"]}
      >
        {/* Base route line (for interaction) */}
        {geojson && (
          <Source id="route" type="geojson" data={geojson}>
            <Layer
              id="route-line"
              type="line"
              paint={{
                "line-color": "#94a3b8",
                "line-width": 4,
                "line-opacity": 0.3,
              }}
            />
          </Source>
        )}

        {/* Colored segments */}
        {segData && (
          <Source id="segments" type="geojson" data={segData}>
            <Layer
              id="segments-line"
              type="line"
              paint={{
                "line-color": ["get", "color"],
                "line-width": 4,
              }}
            />
          </Source>
        )}

        {/* Cursor marker */}
        {cursorPoint && (
          <Marker
            longitude={cursorPoint.lon}
            latitude={cursorPoint.lat}
            anchor="center"
          >
            <div className="w-4 h-4 rounded-full bg-orange-500 border-2 border-white shadow-lg" />
          </Marker>
        )}

        {/* Waypoint markers (draggable) */}
        {waypoints.map((wp) => (
          <Marker
            key={wp.id}
            longitude={wp.lon}
            latitude={wp.lat}
            anchor="bottom"
            draggable
            onDragEnd={(e) => handleWaypointDragEnd(wp.id, e.lngLat)}
          >
            <div className="flex flex-col items-center">
              <span className="text-xs font-medium bg-white px-1 rounded shadow mb-1 whitespace-nowrap">
                {wp.name}
              </span>
              <div
                className={`w-3 h-3 rounded-full border-2 border-white shadow cursor-grab active:cursor-grabbing ${
                  wp.type === "aid_station"
                    ? "bg-emerald-500"
                    : wp.type === "power_target"
                      ? "bg-amber-500"
                      : wp.type === "pace_change"
                        ? "bg-purple-500"
                        : "bg-slate-500"
                }`}
              />
            </div>
          </Marker>
        ))}
      </Map>

      {/* Map style toggle */}
      <div className="absolute top-3 right-3 z-10">
        <button
          onClick={() => setMapStyle(mapStyle === "streets" ? "satellite" : "streets")}
          className="bg-background/90 backdrop-blur-sm border rounded-md px-2.5 py-1.5 text-xs font-medium shadow-sm hover:bg-accent transition-colors"
          title="Toggle map style"
        >
          {mapStyle === "streets" ? "Satellite" : "Streets"}
        </button>
      </div>
    </div>
  );
}
