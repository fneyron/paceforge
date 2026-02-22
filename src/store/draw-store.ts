import { create } from "zustand";
import type { RoutePoint, RouteStats, Segment } from "@/types/route";
import type { FeatureCollection } from "geojson";

interface DrawPoint {
  lat: number;
  lon: number;
}

interface DrawState {
  isDrawing: boolean;
  drawPoints: DrawPoint[];
  routedPoints: RoutePoint[];
  segments: Segment[];
  stats: RouteStats | null;
  previewGeojson: FeatureCollection | null;

  startDrawing: () => void;
  stopDrawing: () => void;
  addPoint: (point: DrawPoint) => void;
  removeLastPoint: () => void;
  updateRoutedPoints: (points: RoutePoint[], segments: Segment[], stats: RouteStats, geojson: FeatureCollection) => void;
  clear: () => void;
}

export const useDrawStore = create<DrawState>((set) => ({
  isDrawing: false,
  drawPoints: [],
  routedPoints: [],
  segments: [],
  stats: null,
  previewGeojson: null,

  startDrawing: () => set({ isDrawing: true }),
  stopDrawing: () => set({ isDrawing: false }),

  addPoint: (point) =>
    set((state) => ({
      drawPoints: [...state.drawPoints, point],
    })),

  removeLastPoint: () =>
    set((state) => ({
      drawPoints: state.drawPoints.slice(0, -1),
    })),

  updateRoutedPoints: (points, segments, stats, geojson) =>
    set({
      routedPoints: points,
      segments,
      stats,
      previewGeojson: geojson,
    }),

  clear: () =>
    set({
      isDrawing: false,
      drawPoints: [],
      routedPoints: [],
      segments: [],
      stats: null,
      previewGeojson: null,
    }),
}));
