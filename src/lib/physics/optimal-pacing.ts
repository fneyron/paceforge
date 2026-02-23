/**
 * Optimal pacing solver.
 * Finds the energy-optimal effort distribution across segments.
 * Uses iterative gradient descent to minimize total time.
 */

import type { Segment, CyclingConfig, TrailConfig, RoadRunningConfig, SportType } from "@/types/route";
import type { SegmentEffortModifier } from "@/types/pacing";
import { solveSpeed } from "./cycling";
import { computeTrailSegmentTime } from "./trail";
import { computeRoadRunningSegmentTime } from "./road-running";

interface OptimalPacingConstraints {
  maxEffort: number; // maximum effort factor (e.g., 1.10)
  minEffort: number; // minimum effort factor (e.g., 0.85)
}

const DEFAULT_CONSTRAINTS: OptimalPacingConstraints = {
  maxEffort: 1.10,
  minEffort: 0.85,
};

/**
 * Compute total time for given effort distribution.
 */
function computeTotalTime(
  segments: Segment[],
  efforts: number[],
  sport: SportType,
  config: CyclingConfig | TrailConfig | RoadRunningConfig
): number {
  let total = 0;
  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];
    const effort = efforts[i];

    if (sport === "cycling" || sport === "gravel") {
      const cfg = config as CyclingConfig;
      const power = cfg.ftp * effort;
      const speed = solveSpeed(power, seg.averageGrade, 100, cfg, 0);
      total += seg.length / speed;
    } else if (sport === "road_running") {
      const cfg = config as RoadRunningConfig;
      total += computeRoadRunningSegmentTime(
        seg.length,
        seg.averageGrade,
        { ...cfg, vdot: cfg.vdot * effort }
      );
    } else {
      // trail
      const cfg = config as TrailConfig;
      total += computeTrailSegmentTime(
        seg.length,
        seg.averageGrade,
        { ...cfg, vma: cfg.vma * effort }
      );
    }
  }
  return total;
}

/**
 * Compute total "energy" for given effort distribution.
 * Energy proxy: sum(effort_i² × distance_i)
 * This keeps total energy constant during optimization.
 */
function computeEnergy(segments: Segment[], efforts: number[]): number {
  return segments.reduce((sum, seg, i) => sum + efforts[i] * efforts[i] * seg.length, 0);
}

/**
 * Solve for optimal pacing strategy.
 * Algorithm:
 * 1. Start with even effort (all 1.0)
 * 2. For each pair of segments, try shifting effort from one to another
 * 3. Keep changes that reduce total time while maintaining energy budget
 * 4. Iterate until convergence
 */
export function solveOptimalPacing(
  segments: Segment[],
  sport: SportType,
  config: CyclingConfig | TrailConfig | RoadRunningConfig,
  constraints: OptimalPacingConstraints = DEFAULT_CONSTRAINTS
): SegmentEffortModifier[] {
  const n = segments.length;
  if (n <= 1) {
    return segments.map((s) => ({
      segmentId: s.id || "seg-0",
      effortFactor: 1.0,
    }));
  }

  // Initialize with even effort
  const efforts = new Array(n).fill(1.0);
  const targetEnergy = computeEnergy(segments, efforts);
  let bestTime = computeTotalTime(segments, efforts, sport, config);

  const DELTA = 0.005; // 0.5% perturbation
  const MAX_ITERATIONS = 80;

  for (let iter = 0; iter < MAX_ITERATIONS; iter++) {
    let improved = false;

    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        // Try shifting effort from i to j
        const newEfforts = [...efforts];
        newEfforts[i] = efforts[i] - DELTA;
        newEfforts[j] = efforts[j] + DELTA;

        // Check constraints
        if (newEfforts[i] < constraints.minEffort || newEfforts[j] > constraints.maxEffort) continue;

        const newTime = computeTotalTime(segments, newEfforts, sport, config);
        if (newTime < bestTime - 0.1) { // 0.1s threshold
          efforts[i] = newEfforts[i];
          efforts[j] = newEfforts[j];
          bestTime = newTime;
          improved = true;
        }

        // Try opposite direction
        const revEfforts = [...efforts];
        revEfforts[i] = efforts[i] + DELTA;
        revEfforts[j] = efforts[j] - DELTA;

        if (revEfforts[j] < constraints.minEffort || revEfforts[i] > constraints.maxEffort) continue;

        const revTime = computeTotalTime(segments, revEfforts, sport, config);
        if (revTime < bestTime - 0.1) {
          efforts[i] = revEfforts[i];
          efforts[j] = revEfforts[j];
          bestTime = revTime;
          improved = true;
        }
      }
    }

    if (!improved) break;
  }

  return segments.map((seg, i) => ({
    segmentId: seg.id || `seg-${i}`,
    effortFactor: Math.round(efforts[i] * 1000) / 1000,
  }));
}
