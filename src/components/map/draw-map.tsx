"use client";

import { useCallback, useEffect, useRef } from "react";
import { Map, Source, Layer, Marker } from "@vis.gl/react-maplibre";
import type { MapRef, MapLayerMouseEvent } from "@vis.gl/react-maplibre";
import { useDrawStore } from "@/store/draw-store";
import "maplibre-gl/dist/maplibre-gl.css";
import type { FeatureCollection } from "geojson";

export function DrawMap() {
  const mapRef = useRef<MapRef>(null);
  const drawPoints = useDrawStore((s) => s.drawPoints);
  const isDrawing = useDrawStore((s) => s.isDrawing);
  const addPoint = useDrawStore((s) => s.addPoint);
  const previewGeojson = useDrawStore((s) => s.previewGeojson);

  const handleClick = useCallback(
    (e: MapLayerMouseEvent) => {
      if (!isDrawing) return;
      addPoint({ lat: e.lngLat.lat, lon: e.lngLat.lng });
    },
    [isDrawing, addPoint]
  );

  // Build line preview from raw draw points
  const drawLineGeojson: FeatureCollection | null =
    drawPoints.length >= 2
      ? {
          type: "FeatureCollection",
          features: [
            {
              type: "Feature",
              properties: {},
              geometry: {
                type: "LineString",
                coordinates: drawPoints.map((p) => [p.lon, p.lat]),
              },
            },
          ],
        }
      : null;

  return (
    <Map
      ref={mapRef}
      initialViewState={{
        longitude: 2.3,
        latitude: 46.5,
        zoom: 6,
      }}
      style={{ width: "100%", height: "100%" }}
      mapStyle="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json"
      onClick={handleClick}
      cursor={isDrawing ? "crosshair" : "grab"}
    >
      {/* Processed route preview (solid line) */}
      {previewGeojson && (
        <Source id="preview-route" type="geojson" data={previewGeojson}>
          <Layer
            id="preview-route-line"
            type="line"
            paint={{
              "line-color": "#22c55e",
              "line-width": 3,
            }}
          />
        </Source>
      )}

      {/* Draw points line preview (dashed orange) */}
      {drawLineGeojson && (
        <Source id="draw-line" type="geojson" data={drawLineGeojson}>
          <Layer
            id="draw-line-layer"
            type="line"
            paint={{
              "line-color": "#f97316",
              "line-width": 3,
              "line-dasharray": [4, 3],
            }}
          />
        </Source>
      )}

      {/* Draw point markers */}
      {drawPoints.map((point, i) => (
        <Marker
          key={i}
          longitude={point.lon}
          latitude={point.lat}
          anchor="center"
        >
          <div className="w-5 h-5 rounded-full bg-orange-500 border-2 border-white shadow-lg flex items-center justify-center text-[9px] text-white font-bold">
            {i + 1}
          </div>
        </Marker>
      ))}
    </Map>
  );
}
