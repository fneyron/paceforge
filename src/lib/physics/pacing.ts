import type { Segment } from "@/types/route";
import type { PacingStrategy, SegmentEffortModifier } from "@/types/pacing";

/**
 * Resolve a pacing strategy into per-segment effort modifiers.
 */
export function resolveStrategy(
  strategy: PacingStrategy,
  segments: Segment[],
  totalDistance: number
): SegmentEffortModifier[] {
  switch (strategy.type) {
    case "even_effort":
      return segments.map((seg) => ({
        segmentId: seg.id || "",
        effortFactor: 1.0,
      }));

    case "negative_split":
      return resolveNegativeSplit(strategy, segments, totalDistance);

    case "positive_split":
      return resolvePositiveSplit(strategy, segments, totalDistance);

    case "race_strategy":
      return resolveRaceStrategy(strategy, segments);

    case "optimal":
      // Optimal pacing is resolved by the solver in simulate.ts
      // Return even effort as placeholder — simulate.ts will override
      return segments.map((seg) => ({
        segmentId: seg.id || "",
        effortFactor: 1.0,
      }));
  }
}

function resolveNegativeSplit(
  strategy: { firstHalfFactor: number; secondHalfFactor: number; transitionPoint: number },
  segments: Segment[],
  totalDistance: number
): SegmentEffortModifier[] {
  const tp = strategy.transitionPoint * totalDistance;
  const bandWidth = 0.1 * totalDistance; // 10% transition band

  return segments.map((seg) => {
    const midDist = (seg.startDistance + seg.endDistance) / 2;
    const factor = interpolateFactor(
      midDist,
      tp,
      bandWidth,
      strategy.firstHalfFactor,
      strategy.secondHalfFactor
    );
    return { segmentId: seg.id || "", effortFactor: factor };
  });
}

function resolvePositiveSplit(
  strategy: { firstHalfFactor: number; secondHalfFactor: number; transitionPoint: number },
  segments: Segment[],
  totalDistance: number
): SegmentEffortModifier[] {
  const tp = strategy.transitionPoint * totalDistance;
  const bandWidth = 0.1 * totalDistance;

  return segments.map((seg) => {
    const midDist = (seg.startDistance + seg.endDistance) / 2;
    const factor = interpolateFactor(
      midDist,
      tp,
      bandWidth,
      strategy.firstHalfFactor,
      strategy.secondHalfFactor
    );
    return { segmentId: seg.id || "", effortFactor: factor };
  });
}

function resolveRaceStrategy(
  strategy: { climbFactor: number; flatFactor: number; descentFactor: number },
  segments: Segment[]
): SegmentEffortModifier[] {
  return segments.map((seg) => {
    let factor: number;
    switch (seg.type) {
      case "climb":
        factor = strategy.climbFactor;
        break;
      case "descent":
        factor = strategy.descentFactor;
        break;
      default:
        factor = strategy.flatFactor;
    }
    return { segmentId: seg.id || "", effortFactor: factor };
  });
}

/**
 * Linear interpolation between two factors over a transition band.
 */
function interpolateFactor(
  distance: number,
  transitionPoint: number,
  bandWidth: number,
  factorBefore: number,
  factorAfter: number
): number {
  const halfBand = bandWidth / 2;
  const start = transitionPoint - halfBand;
  const end = transitionPoint + halfBand;

  if (distance <= start) return factorBefore;
  if (distance >= end) return factorAfter;

  const t = (distance - start) / (end - start);
  return factorBefore + t * (factorAfter - factorBefore);
}
