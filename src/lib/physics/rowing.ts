/**
 * Rowing simulation engine.
 * Hydrodynamic resistance model with Newton-Raphson speed solver.
 *
 * P = (0.5·ρ_water·Cd_hull·A_wetted·v² + 0.5·ρ_air·CdA_rower·v²)·v
 *   = (k_water + k_air)·v³
 */

import type { RowingConfig, BoatClass } from "@/types/route";

const RHO_WATER = 1000; // kg/m³
const RHO_AIR = 1.225; // kg/m³

/** Hull parameters by boat class */
const BOAT_PARAMS: Record<BoatClass, { cd: number; wettedArea: number; cdaRower: number; hullWeight: number }> = {
  "1x": { cd: 0.012, wettedArea: 0.9, cdaRower: 0.40, hullWeight: 14 },
  "2x": { cd: 0.010, wettedArea: 1.1, cdaRower: 0.35, hullWeight: 27 },
  "2-": { cd: 0.010, wettedArea: 1.1, cdaRower: 0.35, hullWeight: 27 },
  "4x": { cd: 0.008, wettedArea: 1.4, cdaRower: 0.30, hullWeight: 52 },
  "4-": { cd: 0.008, wettedArea: 1.4, cdaRower: 0.30, hullWeight: 50 },
  "8+": { cd: 0.007, wettedArea: 1.8, cdaRower: 0.25, hullWeight: 96 },
};

/**
 * Solve boat speed from total power using Newton-Raphson.
 * P = (k_water + k_air) · v³
 * where k_water = 0.5 · ρ_water · Cd · A_wet
 *       k_air   = 0.5 · ρ_air · CdA_rower
 */
export function solveRowingSpeed(
  totalPower: number,
  config: RowingConfig
): number {
  const params = BOAT_PARAMS[config.boatClass] || BOAT_PARAMS["1x"];
  const kWater = 0.5 * RHO_WATER * params.cd * params.wettedArea;
  const kAir = 0.5 * RHO_AIR * params.cdaRower;
  const kTotal = kWater + kAir;

  // P = k · v³ → v = (P/k)^(1/3)
  const v = Math.pow(totalPower / kTotal, 1 / 3);

  // Add current
  const current = config.currentSpeed ?? 0;
  return Math.max(0.5, v + current);
}

/**
 * Compute segment time for rowing.
 * @param distance - meters
 * @param config - rowing configuration
 */
export function computeRowingSegmentTime(
  distance: number,
  config: RowingConfig
): number {
  const speed = solveRowingSpeed(config.power, config);
  return distance / speed;
}

/**
 * Get number of rowers for a boat class.
 */
export function getCrewSize(boatClass: BoatClass): number {
  switch (boatClass) {
    case "1x": return 1;
    case "2x":
    case "2-": return 2;
    case "4x":
    case "4-": return 4;
    case "8+": return 9; // 8 rowers + cox
  }
}

/**
 * Standard rowing distances.
 */
export const ROWING_DISTANCES = [
  { meters: 500, label: "500m Sprint" },
  { meters: 1000, label: "1000m" },
  { meters: 2000, label: "2000m Olympic" },
  { meters: 6000, label: "6000m Head Race" },
];
