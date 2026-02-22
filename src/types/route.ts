export interface RoutePoint {
  lat: number;
  lon: number;
  ele: number;
  distance: number; // cumulative distance in meters
  grade: number; // gradient in fraction (-1 to 1)
}

export interface RouteStats {
  totalDistance: number; // meters
  elevationGain: number; // meters
  elevationLoss: number; // meters
  minElevation: number; // meters
  maxElevation: number; // meters
}

export type SegmentType = "climb" | "descent" | "flat";

export interface Segment {
  id?: string;
  type: SegmentType;
  startDistance: number; // meters
  endDistance: number; // meters
  startIndex: number;
  endIndex: number;
  elevationGain: number;
  elevationLoss: number;
  averageGrade: number; // fraction
  maxGrade: number; // fraction
  length: number; // meters
}

export type WaypointType = "aid_station" | "power_target" | "pace_change" | "nutrition" | "transition" | "custom";

export interface Waypoint {
  id: string;
  routeId: string;
  type: WaypointType;
  name: string;
  distance: number; // meters along route
  lat: number;
  lon: number;
  ele: number;
  config: Record<string, unknown>;
}

export type SportType = "cycling" | "gravel" | "trail" | "ultra_trail" | "road_running" | "swimming" | "triathlon";

export interface CyclingConfig {
  ftp: number; // watts
  weight: number; // kg
  bikeWeight: number; // kg
  cda: number; // m²
  crr: number; // coefficient
  efficiency: number; // 0-1
  powerTargets: { segmentId: string; power: number }[];
}

export interface TrailConfig {
  vma: number; // km/h
  weight: number; // kg
  packWeight: number; // kg
}

export interface FatigueConfig {
  halfLife: number; // hours
  minFactor: number; // 0-1
}

export interface SimulationResult {
  splits: SplitResult[];
  totalTime: number; // seconds
  movingTime: number; // seconds
}

export interface SplitZone {
  name: string;
  color: string;
  number: number;
}

export interface SplitResult {
  segmentId: string;
  distance: number; // meters
  elevationGain: number;
  elevationLoss: number;
  time: number; // seconds
  speed: number; // m/s
  pace: number; // min/km
  power?: number; // watts
  effortFactor?: number; // pacing effort modifier
  zone?: SplitZone;
}

export interface SwimmingConfig {
  css: number; // sec/100m (Critical Swim Speed)
  weight: number; // kg
  height: number; // cm (for drag estimation)
  isOpenWater: boolean;
  hasWetsuit: boolean;
  waterTemperature?: number; // °C
  currentSpeed?: number; // m/s, positive = favorable
}

export interface RoadRunningConfig {
  vdot: number; // VDOT score (30-85)
  weight: number; // kg
  temperature?: number; // °C
  humidity?: number; // 0-100%
}

export type TriathlonFormat = "sprint" | "olympic" | "half_ironman" | "ironman" | "custom";

export interface TriathlonConfig {
  swimConfig: SwimmingConfig;
  cyclingConfig: CyclingConfig;
  runConfig: RoadRunningConfig | TrailConfig;
  t1Time: number; // seconds (swim → bike)
  t2Time: number; // seconds (bike → run)
  raceFormat: TriathlonFormat;
}

export interface WeatherCondition {
  lat: number;
  lon: number;
  distance: number; // meters along route
  temperature: number; // °C
  humidity: number; // 0-100%
  windSpeed: number; // m/s
  windDirection: number; // degrees (0 = north)
  pressure: number; // hPa
  precipitation: number; // mm/h
  cloudCover: number; // 0-100%
}

export interface NutritionStrategy {
  carbsPerHour: number; // grams (60-90)
  fluidPerHour: number; // ml (500-800)
  sodiumPerHour: number; // mg (500-1000)
  caffeineStrategy?: {
    startAfterHours: number;
    dosePerIntake: number; // mg
    maxTotal: number; // mg
  };
}
