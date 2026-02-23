/**
 * Duathlon simulation engine.
 * Run1 → T1 → Bike → T2 → Run2
 * Brick effect: 5% on Run2 after cycling.
 */

import type {
  DuathlonConfig,
  Segment,
  RoutePoint,
  FatigueConfig,
  SimulationResult,
  SplitResult,
} from "@/types/route";
import { computeRoadRunningSegmentTime } from "./road-running";
import { computeTrailSegmentTime } from "./trail";
import { computeCyclingSegmentTime, solveSpeed } from "./cycling";
import { fatigueFactor, DEFAULT_FATIGUE } from "./fatigue";
import { classifyPowerZone, classifyPaceZone } from "@/lib/zones";

const BRICK_EFFECT_PENALTY = 0.05; // 5% slowdown on run after bike

interface DuathlonLeg {
  discipline: "run1" | "bike" | "run2";
  segments: Segment[];
  points: RoutePoint[];
}

interface SimulateDuathlonInput {
  legs: DuathlonLeg[];
  config: DuathlonConfig;
  fatigueConfig?: FatigueConfig;
}

function isTrailConfig(config: unknown): config is { vma: number; weight: number; packWeight: number } {
  return typeof config === "object" && config !== null && "vma" in config;
}

export function simulateDuathlon(input: SimulateDuathlonInput): SimulationResult {
  const { legs, config, fatigueConfig } = input;
  const fatigue = fatigueConfig || DEFAULT_FATIGUE.duathlon || DEFAULT_FATIGUE.triathlon;
  const splits: SplitResult[] = [];
  let cumulativeTime = 0;

  for (const leg of legs) {
    if (leg.discipline === "run1" || leg.discipline === "run2") {
      // Run leg
      const runConfig = leg.discipline === "run1" ? config.run1Config : config.run2Config;
      const brickPenalty = leg.discipline === "run2" ? BRICK_EFFECT_PENALTY : 0;

      for (const segment of leg.segments) {
        const elapsedHours = cumulativeTime / 3600;
        const ff = fatigueFactor(elapsedHours, fatigue) * (1 - brickPenalty);

        let time: number;
        let speed: number;
        let zone: { name: string; color: string; number: number } | undefined;

        if (isTrailConfig(runConfig)) {
          const effectiveConfig = { ...runConfig, vma: runConfig.vma * ff };
          time = computeTrailSegmentTime(segment.length, segment.averageGrade, effectiveConfig);
          speed = segment.length / time;
        } else {
          const effectiveConfig = { ...runConfig, vdot: runConfig.vdot * ff };
          time = computeRoadRunningSegmentTime(segment.length, segment.averageGrade, effectiveConfig);
          speed = segment.length / time;
          const secPerKm = speed > 0 ? 1000 / speed : 999;
          const z = classifyPaceZone(secPerKm, runConfig.vdot);
          zone = { name: z.name, color: z.color, number: z.number };
        }

        splits.push({
          segmentId: segment.id || `${leg.discipline}-${splits.length}`,
          distance: segment.length,
          elevationGain: segment.elevationGain,
          elevationLoss: segment.elevationLoss,
          time,
          speed,
          pace: speed > 0 ? 1000 / speed / 60 : 999,
          zone,
        });
        cumulativeTime += time;
      }
    } else if (leg.discipline === "bike") {
      // Bike leg
      const cyclingCfg = config.cyclingConfig;

      for (const segment of leg.segments) {
        const elapsedHours = cumulativeTime / 3600;
        const ff = fatigueFactor(elapsedHours, fatigue);
        const effectivePower = cyclingCfg.ftp * ff;

        const segPoints = leg.points.slice(segment.startIndex, segment.endIndex + 1);
        const avgAltitude = segPoints.length > 0
          ? segPoints.reduce((s, p) => s + p.ele, 0) / segPoints.length
          : 100;

        const speed = solveSpeed(effectivePower, segment.averageGrade, avgAltitude, cyclingCfg, 0);
        const time = segment.length / speed;

        const z = classifyPowerZone(effectivePower, cyclingCfg.ftp);

        splits.push({
          segmentId: segment.id || `bike-${splits.length}`,
          distance: segment.length,
          elevationGain: segment.elevationGain,
          elevationLoss: segment.elevationLoss,
          time,
          speed,
          pace: speed > 0 ? 1000 / speed / 60 : 999,
          power: effectivePower,
          zone: { name: z.name, color: z.color, number: z.number },
        });
        cumulativeTime += time;
      }
    }

    // Add transition time
    if (leg.discipline === "run1") {
      splits.push({
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
      splits.push({
        segmentId: "t2",
        distance: 0,
        elevationGain: 0,
        elevationLoss: 0,
        time: config.t2Time,
        speed: 0,
        pace: 0,
      });
      cumulativeTime += config.t2Time;
    }
  }

  return {
    splits,
    totalTime: cumulativeTime,
    movingTime: cumulativeTime - config.t1Time - config.t2Time,
  };
}
