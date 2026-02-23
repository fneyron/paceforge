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

export type SportType =
  | "cycling"
  | "gravel"
  | "trail"
  | "ultra_trail"
  | "road_running"
  | "swimming"
  | "triathlon"
  | "cross_country_skiing"
  | "rowing"
  | "duathlon"
  | "swimrun";

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
  // ITRA (trail/ultra_trail only)
  itraPoints?: number;
  itraCategory?: string;
  itraStars?: number;
  itraPerformanceIndex?: number;
  // Glycogen depletion timeline (optional)
  glycogenTimeline?: GlycogenDataPoint[];
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

// ── Cross-Country Skiing ──

export type XCSkiTechnique = "classic" | "skating";

export interface CrossCountrySkiingConfig {
  vo2max: number; // ml/kg/min
  weight: number; // kg
  technique: XCSkiTechnique;
  snowFriction: number; // μ coefficient 0.02-0.15
  temperature?: number; // °C (affects snow friction)
}

// ── Rowing ──

export type BoatClass = "1x" | "2x" | "2-" | "4x" | "4-" | "8+";

export interface RowingConfig {
  power: number; // watts (per rower)
  weight: number; // kg (total crew + boat)
  boatClass: BoatClass;
  currentSpeed?: number; // m/s (positive = favorable)
}

// ── Duathlon ──

export type DuathlonFormat = "sprint" | "standard" | "long" | "custom";

export interface DuathlonConfig {
  run1Config: RoadRunningConfig | TrailConfig;
  cyclingConfig: CyclingConfig;
  run2Config: RoadRunningConfig | TrailConfig;
  t1Time: number; // seconds (run1 → bike)
  t2Time: number; // seconds (bike → run2)
  raceFormat: DuathlonFormat;
}

// ── SwimRun ──

export interface SwimRunConfig {
  swimConfig: SwimmingConfig;
  runConfig: RoadRunningConfig | TrailConfig;
  hasPullBuoy: boolean;
  hasHandPaddles: boolean;
  wetsuitRunPenalty: number; // default 0.03
  shoeSwimPenalty: number; // default 0.05
}

// ── Multi-Model Predictions ──

export interface ModelPrediction {
  model: string; // 'daniels' | 'riegel' | 'cameron' | 'cp_wprime'
  time: number; // seconds
  pace: number; // sec/km or sec/100m
  speed: number; // km/h
  confidence: number; // 0-1
}

export interface MultiModelPrediction {
  distance: number;
  distanceLabel: string;
  models: ModelPrediction[];
  consensusTime: number; // weighted average
  rangeMin: number; // fastest estimate (seconds)
  rangeMax: number; // slowest estimate (seconds)
}

// ── Glycogen Model ──

export interface GlycogenDataPoint {
  time: number; // seconds
  distance: number; // meters
  muscleGlycogen: number; // grams remaining
  liverGlycogen: number; // grams remaining
  fatOxRate: number; // g/min current rate
  carbIntake: number; // cumulative grams
  bonkRisk: number; // 0-1
}

// ── Performance Management Chart ──

export interface PMCDataPoint {
  date: string; // ISO date
  tss: number;
  ctl: number; // chronic training load (fitness)
  atl: number; // acute training load (fatigue)
  tsb: number; // training stress balance (form)
}

// ── Nutrition ──

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
