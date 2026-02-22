import { create } from "zustand";

interface CursorState {
  /** Distance along the route in meters (null = no hover) */
  distance: number | null;
  /** Source of the hover event */
  source: "map" | "profile" | null;

  setDistance: (distance: number | null, source: "map" | "profile") => void;
  clear: () => void;
}

export const useCursorStore = create<CursorState>((set) => ({
  distance: null,
  source: null,

  setDistance: (distance, source) => set({ distance, source }),
  clear: () => set({ distance: null, source: null }),
}));
