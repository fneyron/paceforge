import type {
  Segment,
  RoutePoint,
  CyclingConfig,
  TrailConfig,
  SwimmingConfig,
  RoadRunningConfig,
  TriathlonConfig,
  FatigueConfig,
  SimulationResult,
  SplitResult,
  SportType,
  WeatherCondition,
} from "@/types/route";
import type { PacingStrategy } from "@/types/pacing";
import { computeCyclingSegmentTime, solveSpeed } from "./cycling";
import { computeTrailSegmentTime, computeTrailSpeed } from "./trail";
import { computeSwimmingSegmentTime } from "./swimming";
import { computeRoadRunningSegmentTime } from "./road-running";
import { simulateTriathlon } from "./triathlon";
import { fatigueFactor, DEFAULT_FATIGUE } from "./fatigue";
import { resolveStrategy } from "./pacing";
import { classifyPowerZone, classifyPaceZone, classifySwimZone } from "@/lib/zones";

type SportConfig =
  | CyclingConfig
  | TrailConfig
  | SwimmingConfig
  | RoadRunningConfig
  | TriathlonConfig;

interface SimulateInput {
  sport: SportType;
  segments: Segment[];
  points: RoutePoint[];
  config: SportConfig;
  fatigueConfig?: FatigueConfig;
  weather?: WeatherCondition[];
  pacingStrategy?: PacingStrategy;
}

/**
 * Find weather condition closest to a given distance along the route.
 */
function findWeatherAtDistance(
  weather: WeatherCondition[] | undefined,
  distance: number
): WeatherCondition | undefined {
  if (!weather || weather.length === 0) return undefined;

  let closest = weather[0];
  let minDiff = Math.abs(weather[0].distance - distance);

  for (let i = 1; i < weather.length; i++) {
    const diff = Math.abs(weather[i].distance - distance);
    if (diff < minDiff) {
      minDiff = diff;
      closest = weather[i];
    }
  }

  return closest;
}

/**
 * Compute headwind component from wind speed/direction and segment bearing.
 */
function computeHeadwind(
  windSpeed: number,
  windDirection: number,
  segmentBearing: number
): number {
  const angleRad = ((windDirection - segmentBearing) * Math.PI) / 180;
  return windSpeed * Math.cos(angleRad); // positive = headwind
}

/**
 * Estimate bearing between two points.
 */
function estimateSegmentBearing(points: RoutePoint[]): number {
  if (points.length < 2) return 0;
  const start = points[0];
  const end = points[points.length - 1];
  const dLon = ((end.lon - start.lon) * Math.PI) / 180;
  const lat1 = (start.lat * Math.PI) / 180;
  const lat2 = (end.lat * Math.PI) / 180;
  const y = Math.sin(dLon) * Math.cos(lat2);
  const x =
    Math.cos(lat1) * Math.sin(lat2) -
    Math.sin(lat1) * Math.cos(lat2) * Math.cos(dLon);
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
}

/**
 * Run a full race simulation across all segments.
 * Dispatches to sport-specific engines.
 */
export function simulate(input: SimulateInput): SimulationResult {
  const { sport, segments, points, config, fatigueConfig, weather, pacingStrategy } = input;

  // Triathlon has its own orchestrator
  if (sport === "triathlon") {
    const triConfig = config as TriathlonConfig;
    // For triathlon with a single route, treat all segments as bike leg
    // (proper triathlon needs multi-leg routing)
    return simulateTriathlon({
      legs: [
        { discipline: "bike", segments, points },
      ],
      config: triConfig,
      fatigueConfig,
    });
  }

  // Swimming: single segment (no elevation)
  if (sport === "swimming") {
    const swimConfig = config as SwimmingConfig;
    const totalDist = segments.reduce((sum, s) => sum + s.length, 0);
    const time = computeSwimmingSegmentTime(totalDist, swimConfig);
    const speed = totalDist / time;

    const secPer100m = speed > 0 ? 100 / speed : 999;
    const swimZone = classifySwimZone(secPer100m, swimConfig.css);

    return {
      splits: [
        {
          segmentId: "swim",
          distance: totalDist,
          elevationGain: 0,
          elevationLoss: 0,
          time,
          speed,
          pace: speed > 0 ? 1000 / speed / 60 : 999,
          zone: { name: swimZone.name, color: swimZone.color, number: swimZone.number },
        },
      ],
      totalTime: time,
      movingTime: time,
    };
  }

  const fatigue =
    fatigueConfig || DEFAULT_FATIGUE[sport] || DEFAULT_FATIGUE.cycling;

  // Resolve pacing strategy into per-segment effort modifiers
  const totalDistance = segments.reduce((sum, s) => sum + s.length, 0);
  const effortModifiers = pacingStrategy
    ? resolveStrategy(pacingStrategy, segments, totalDistance)
    : [];

  const splits: SplitResult[] = [];
  let cumulativeTime = 0;

  for (const segment of segments) {
    const elapsedHours = cumulativeTime / 3600;
    const ff = fatigueFactor(elapsedHours, fatigue);
    const effortMod =
      effortModifiers.find((m) => m.segmentId === (segment.id || `seg-${splits.length}`))
        ?.effortFactor ?? 1.0;

    // Segment points and average altitude
    const segPoints = points.slice(segment.startIndex, segment.endIndex + 1);
    const avgAltitude =
      segPoints.reduce((s, p) => s + p.ele, 0) / segPoints.length;

    // Weather at segment midpoint
    const segMidDist = (segment.startDistance + segment.endDistance) / 2;
    const wx = findWeatherAtDistance(weather, segMidDist);

    let time: number;
    let speed: number;
    let power: number | undefined;

    if (sport === "cycling" || sport === "gravel") {
      const cyclingCfg = config as CyclingConfig;
      const powerTarget =
        cyclingCfg.powerTargets?.find((pt) => pt.segmentId === segment.id)
          ?.power || cyclingCfg.ftp;

      const effectivePower = powerTarget * ff * effortMod;
      power = effectivePower;

      // Compute headwind from weather
      let headwind = 0;
      if (wx) {
        const bearing = estimateSegmentBearing(segPoints);
        headwind = computeHeadwind(wx.windSpeed, wx.windDirection, bearing);
      }

      speed = solveSpeed(
        effectivePower,
        segment.averageGrade,
        avgAltitude,
        cyclingCfg,
        headwind
      );
      time = segment.length / speed;
    } else if (sport === "road_running") {
      const runCfg = config as RoadRunningConfig;
      const effectiveConfig: RoadRunningConfig = {
        ...runCfg,
        vdot: runCfg.vdot * ff * effortMod,
      };

      // Add weather temperature correction
      if (wx) {
        effectiveConfig.temperature = wx.temperature;
        effectiveConfig.humidity = wx.humidity;
      }

      time = computeRoadRunningSegmentTime(
        segment.length,
        segment.averageGrade,
        effectiveConfig
      );
      speed = segment.length / time;
    } else {
      // trail or ultra_trail
      const trailCfg = config as TrailConfig;
      const effectiveConfig: TrailConfig = {
        ...trailCfg,
        vma: trailCfg.vma * ff * effortMod,
      };

      time = computeTrailSegmentTime(
        segment.length,
        segment.averageGrade,
        effectiveConfig
      );
      speed = segment.length / time;
    }

    const pace = speed > 0 ? 1000 / speed / 60 : 999;

    // Classify zone based on sport
    let zone: { name: string; color: string; number: number } | undefined;
    if ((sport === "cycling" || sport === "gravel") && power !== undefined) {
      const cyclingCfg = config as CyclingConfig;
      const z = classifyPowerZone(power, cyclingCfg.ftp);
      zone = { name: z.name, color: z.color, number: z.number };
    } else if (sport === "road_running") {
      const runCfg = config as RoadRunningConfig;
      const secPerKm = speed > 0 ? 1000 / speed : 999;
      const z = classifyPaceZone(secPerKm, runCfg.vdot);
      zone = { name: z.name, color: z.color, number: z.number };
    }

    splits.push({
      segmentId: segment.id || `seg-${splits.length}`,
      distance: segment.length,
      elevationGain: segment.elevationGain,
      elevationLoss: segment.elevationLoss,
      time,
      speed,
      pace,
      power,
      effortFactor: effortMod !== 1.0 ? effortMod : undefined,
      zone,
    });

    cumulativeTime += time;
  }

  return {
    splits,
    totalTime: cumulativeTime,
    movingTime: cumulativeTime,
  };
}

/**
 * Format seconds to HH:MM:SS string.
 */
export function formatTime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);

  if (h > 0) {
    return `${h}h${m.toString().padStart(2, "0")}m${s.toString().padStart(2, "0")}s`;
  }
  return `${m}m${s.toString().padStart(2, "0")}s`;
}
