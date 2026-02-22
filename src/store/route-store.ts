import { create } from "zustand";
import type { RoutePoint, Segment, Waypoint, RouteStats } from "@/types/route";
import type { FeatureCollection } from "geojson";

interface RouteState {
  routeId: string | null;
  name: string;
  sport: string;
  raceDate: string | null;
  raceStartTime: string | null;
  geojson: FeatureCollection | null;
  points: RoutePoint[];
  segments: Segment[];
  waypoints: Waypoint[];
  stats: RouteStats | null;

  setRoute: (data: {
    routeId: string;
    name: string;
    sport: string;
    geojson: FeatureCollection;
    points: RoutePoint[];
    segments: Segment[];
    stats: RouteStats;
    raceDate?: string | null;
    raceStartTime?: string | null;
  }) => void;
  setName: (name: string) => void;
  setSport: (sport: string) => void;
  setRaceDate: (raceDate: string | null) => void;
  setRaceStartTime: (raceStartTime: string | null) => void;
  setWaypoints: (waypoints: Waypoint[]) => void;
  addWaypoint: (waypoint: Waypoint) => void;
  updateWaypoint: (id: string, data: Partial<Waypoint>) => void;
  removeWaypoint: (id: string) => void;
  clear: () => void;
}

export const useRouteStore = create<RouteState>((set) => ({
  routeId: null,
  name: "",
  sport: "cycling",
  raceDate: null,
  raceStartTime: null,
  geojson: null,
  points: [],
  segments: [],
  waypoints: [],
  stats: null,

  setRoute: (data) =>
    set({
      routeId: data.routeId,
      name: data.name,
      sport: data.sport,
      geojson: data.geojson,
      points: data.points,
      segments: data.segments,
      stats: data.stats,
      raceDate: data.raceDate ?? null,
      raceStartTime: data.raceStartTime ?? null,
    }),

  setName: (name) => set({ name }),
  setSport: (sport) => set({ sport }),
  setRaceDate: (raceDate) => set({ raceDate }),
  setRaceStartTime: (raceStartTime) => set({ raceStartTime }),

  setWaypoints: (waypoints) => set({ waypoints }),

  addWaypoint: (waypoint) =>
    set((state) => ({
      waypoints: [...state.waypoints, waypoint].sort(
        (a, b) => a.distance - b.distance
      ),
    })),

  updateWaypoint: (id, data) =>
    set((state) => ({
      waypoints: state.waypoints.map((w) =>
        w.id === id ? { ...w, ...data } : w
      ),
    })),

  removeWaypoint: (id) =>
    set((state) => ({
      waypoints: state.waypoints.filter((w) => w.id !== id),
    })),

  clear: () =>
    set({
      routeId: null,
      name: "",
      sport: "cycling",
      raceDate: null,
      raceStartTime: null,
      geojson: null,
      points: [],
      segments: [],
      waypoints: [],
      stats: null,
    }),
}));
