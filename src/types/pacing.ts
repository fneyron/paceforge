export type PacingStrategyType =
  | "even_effort"
  | "negative_split"
  | "positive_split"
  | "race_strategy"
  | "optimal";

export interface EvenEffortStrategy {
  type: "even_effort";
}

export interface NegativeSplitStrategy {
  type: "negative_split";
  firstHalfFactor: number; // 0.90 - 0.95
  secondHalfFactor: number; // 1.00 - 1.05
  transitionPoint: number; // 0 - 1 (fraction of total distance)
}

export interface PositiveSplitStrategy {
  type: "positive_split";
  firstHalfFactor: number; // 1.00 - 1.05
  secondHalfFactor: number; // 0.90 - 0.95
  transitionPoint: number; // 0 - 1
}

export interface RaceStrategyConfig {
  type: "race_strategy";
  climbFactor: number; // 0.85 - 1.0
  flatFactor: number; // 0.95 - 1.05
  descentFactor: number; // 0.9 - 1.1
}

export interface OptimalPacingStrategy {
  type: "optimal";
  maxEffort: number; // 1.0 - 1.15
  minEffort: number; // 0.80 - 0.95
}

export type PacingStrategy =
  | EvenEffortStrategy
  | NegativeSplitStrategy
  | PositiveSplitStrategy
  | RaceStrategyConfig
  | OptimalPacingStrategy;

export interface SegmentEffortModifier {
  segmentId: string;
  effortFactor: number;
}
