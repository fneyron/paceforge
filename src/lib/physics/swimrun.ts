/**
 * SwimRun simulation engine.
 * Alternating swim and run legs with equipment penalties.
 * - Wetsuit penalty on running: configurable (default -3%)
 * - Shoe penalty in water: configurable (default -5%)
 * - Pull buoy benefit in water: +3%
 * - Hand paddles benefit in water: +2%
 */

import type {
  SwimRunConfig,
  Segment,
  RoutePoint,
  FatigueConfig,
  SimulationResult,
  SplitResult,
} from "@/types/route";
import { computeSwimmingSegmentTime } from "./swimming";
import { computeRoadRunningSegmentTime } from "./road-running";
import { computeTrailSegmentTime } from "./trail";
import { fatigueFactor, DEFAULT_FATIGUE } from "./fatigue";

const PULL_BUOY_BONUS = 0.03;
const HAND_PADDLES_BONUS = 0.02;

interface SwimRunLeg {
  discipline: "swim" | "run";
  segments: Segment[];
  points: RoutePoint[];
}

interface SimulateSwimRunInput {
  legs: SwimRunLeg[];
  config: SwimRunConfig;
  fatigueConfig?: FatigueConfig;
}

function isTrailConfig(config: unknown): config is { vma: number; weight: number; packWeight: number } {
  return typeof config === "object" && config !== null && "vma" in config;
}

export function simulateSwimRun(input: SimulateSwimRunInput): SimulationResult {
  const { legs, config, fatigueConfig } = input;
  const fatigue = fatigueConfig || DEFAULT_FATIGUE.swimrun || DEFAULT_FATIGUE.triathlon;
  const splits: SplitResult[] = [];
  let cumulativeTime = 0;

  for (const leg of legs) {
    if (leg.discipline === "swim") {
      const totalDist = leg.segments.reduce((sum, s) => sum + s.length, 0);
      const elapsedHours = cumulativeTime / 3600;
      const ff = fatigueFactor(elapsedHours, fatigue);

      // Apply equipment modifiers
      let speedModifier = 1.0;
      speedModifier -= config.shoeSwimPenalty; // shoes slow you down
      if (config.hasPullBuoy) speedModifier += PULL_BUOY_BONUS;
      if (config.hasHandPaddles) speedModifier += HAND_PADDLES_BONUS;

      const effectiveCSS = config.swimConfig.css / (ff * speedModifier); // lower CSS = faster
      const swimConfig = { ...config.swimConfig, css: effectiveCSS };
      const time = computeSwimmingSegmentTime(totalDist, swimConfig);
      const speed = totalDist / time;

      splits.push({
        segmentId: `swim-${splits.length}`,
        distance: totalDist,
        elevationGain: 0,
        elevationLoss: 0,
        time,
        speed,
        pace: speed > 0 ? 1000 / speed / 60 : 999,
      });
      cumulativeTime += time;
    } else {
      // Run leg
      for (const segment of leg.segments) {
        const elapsedHours = cumulativeTime / 3600;
        const ff = fatigueFactor(elapsedHours, fatigue);
        const wetsuitPenalty = 1 - config.wetsuitRunPenalty;

        let time: number;
        let speed: number;

        if (isTrailConfig(config.runConfig)) {
          const effectiveConfig = {
            ...config.runConfig,
            vma: config.runConfig.vma * ff * wetsuitPenalty,
          };
          time = computeTrailSegmentTime(segment.length, segment.averageGrade, effectiveConfig);
          speed = segment.length / time;
        } else {
          const effectiveConfig = {
            ...config.runConfig,
            vdot: config.runConfig.vdot * ff * wetsuitPenalty,
          };
          time = computeRoadRunningSegmentTime(segment.length, segment.averageGrade, effectiveConfig);
          speed = segment.length / time;
        }

        splits.push({
          segmentId: segment.id || `run-${splits.length}`,
          distance: segment.length,
          elevationGain: segment.elevationGain,
          elevationLoss: segment.elevationLoss,
          time,
          speed,
          pace: speed > 0 ? 1000 / speed / 60 : 999,
        });
        cumulativeTime += time;
      }
    }
  }

  const movingTime = splits.reduce((sum, s) => sum + s.time, 0);

  return {
    splits,
    totalTime: cumulativeTime,
    movingTime,
  };
}
