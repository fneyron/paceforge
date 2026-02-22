import type {
  TriathlonConfig,
  RoadRunningConfig,
  TrailConfig,
  Segment,
  RoutePoint,
  FatigueConfig,
  SimulationResult,
  SplitResult,
} from "@/types/route";
import { computeSwimmingSegmentTime } from "./swimming";
import { computeCyclingSegmentTime, solveSpeed } from "./cycling";
import { computeRoadRunningSegmentTime } from "./road-running";
import { computeTrailSegmentTime } from "./trail";
import { fatigueFactor } from "./fatigue";

/**
 * Standard triathlon distances (in meters).
 */
export const TRIATHLON_FORMATS = {
  sprint: { swim: 750, bike: 20000, run: 5000 },
  olympic: { swim: 1500, bike: 40000, run: 10000 },
  half_ironman: { swim: 1930, bike: 90000, run: 21097 },
  ironman: { swim: 3860, bike: 180000, run: 42195 },
} as const;

const BRICK_EFFECT_PENALTY = 0.05; // 5% penalty on run after bike

interface TriathlonLeg {
  discipline: "swim" | "bike" | "run";
  segments: Segment[];
  points: RoutePoint[];
}

interface TriathlonSimulateInput {
  legs: TriathlonLeg[];
  config: TriathlonConfig;
  fatigueConfig?: FatigueConfig;
}

/**
 * Simulate a full triathlon (swim → T1 → bike → T2 → run).
 * Fatigue carries over between legs.
 * Brick effect applies to the run leg.
 */
export function simulateTriathlon(
  input: TriathlonSimulateInput
): SimulationResult {
  const { legs, config, fatigueConfig } = input;
  const fatigue = fatigueConfig || { halfLife: 8, minFactor: 0.6 };

  const allSplits: SplitResult[] = [];
  let cumulativeTime = 0;

  for (const leg of legs) {
    if (leg.discipline === "swim") {
      // Swim: flat distance-based
      const totalSwimDist = leg.segments.reduce(
        (sum, s) => sum + s.length,
        0
      );
      const swimTime = computeSwimmingSegmentTime(
        totalSwimDist,
        config.swimConfig
      );
      const speed = totalSwimDist / swimTime;

      allSplits.push({
        segmentId: "swim",
        distance: totalSwimDist,
        elevationGain: 0,
        elevationLoss: 0,
        time: swimTime,
        speed,
        pace: speed > 0 ? 1000 / speed / 60 : 999,
      });
      cumulativeTime += swimTime;

      // T1 transition
      allSplits.push({
        segmentId: "t1",
        distance: 0,
        elevationGain: 0,
        elevationLoss: 0,
        time: config.t1Time,
        speed: 0,
        pace: 0,
      });
      cumulativeTime += config.t1Time;
    } else if (leg.discipline === "bike") {
      // Bike: segment-by-segment with fatigue
      for (const segment of leg.segments) {
        const elapsedHours = cumulativeTime / 3600;
        const ff = fatigueFactor(elapsedHours, fatigue);

        const segPoints = leg.points.slice(
          segment.startIndex,
          segment.endIndex + 1
        );
        const avgAltitude =
          segPoints.length > 0
            ? segPoints.reduce((s, p) => s + p.ele, 0) / segPoints.length
            : 0;

        const effectivePower = config.cyclingConfig.ftp * ff;
        const time = computeCyclingSegmentTime(
          segment.length,
          segment.averageGrade,
          avgAltitude,
          effectivePower,
          config.cyclingConfig
        );
        const speed = segment.length / time;

        allSplits.push({
          segmentId: segment.id || `bike-${allSplits.length}`,
          distance: segment.length,
          elevationGain: segment.elevationGain,
          elevationLoss: segment.elevationLoss,
          time,
          speed,
          pace: speed > 0 ? 1000 / speed / 60 : 999,
          power: effectivePower,
        });
        cumulativeTime += time;
      }

      // T2 transition
      allSplits.push({
        segmentId: "t2",
        distance: 0,
        elevationGain: 0,
        elevationLoss: 0,
        time: config.t2Time,
        speed: 0,
        pace: 0,
      });
      cumulativeTime += config.t2Time;
    } else if (leg.discipline === "run") {
      // Run: segment-by-segment with fatigue + brick effect
      const isRoadRunning = "vdot" in config.runConfig;

      for (const segment of leg.segments) {
        const elapsedHours = cumulativeTime / 3600;
        const ff = fatigueFactor(elapsedHours, fatigue);
        const brickFactor = 1 - BRICK_EFFECT_PENALTY; // 5% slower

        let time: number;

        if (isRoadRunning) {
          const runCfg = config.runConfig as RoadRunningConfig;
          const effectiveConfig: RoadRunningConfig = {
            ...runCfg,
            vdot: runCfg.vdot * ff * brickFactor,
          };
          time = computeRoadRunningSegmentTime(
            segment.length,
            segment.averageGrade,
            effectiveConfig
          );
        } else {
          const trailCfg = config.runConfig as TrailConfig;
          const effectiveConfig: TrailConfig = {
            ...trailCfg,
            vma: trailCfg.vma * ff * brickFactor,
          };
          time = computeTrailSegmentTime(
            segment.length,
            segment.averageGrade,
            effectiveConfig
          );
        }

        const speed = segment.length / time;

        allSplits.push({
          segmentId: segment.id || `run-${allSplits.length}`,
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

  return {
    splits: allSplits,
    totalTime: cumulativeTime,
    movingTime: cumulativeTime - config.t1Time - config.t2Time,
  };
}
