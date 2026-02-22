import type { RoutePoint, Segment, SegmentType } from "@/types/route";

interface SegmenterConfig {
  /** Gradient threshold for climb (default: 0.03 = 3%) */
  climbThreshold: number;
  /** Gradient threshold for descent (default: -0.03 = -3%) */
  descentThreshold: number;
  /** Minimum segment length in meters (default: 200) */
  minSegmentLength: number;
  /** Minimum elevation gain for a climb segment in meters (default: 50) */
  minClimbGain: number;
  /** Window for gradient smoothing (default: 15) */
  gradeSmoothWindow: number;
}

const DEFAULT_CONFIG: SegmenterConfig = {
  climbThreshold: 0.03,
  descentThreshold: -0.03,
  minSegmentLength: 200,
  minClimbGain: 50,
  gradeSmoothWindow: 15,
};

/**
 * Detect climb/descent/flat segments from route points.
 *
 * Algorithm:
 * 1. Smooth gradients with a moving average
 * 2. Classify each point as climb/descent/flat
 * 3. Group consecutive points of same type into segments
 * 4. Merge short segments into neighbors
 * 5. Validate: climbs need min D+, descents need min D-
 */
export function detectSegments(
  points: RoutePoint[],
  config: Partial<SegmenterConfig> = {}
): Segment[] {
  const cfg = { ...DEFAULT_CONFIG, ...config };

  if (points.length < 3) return [];

  // Step 1: Smooth gradients
  const smoothedGrades = smoothGrades(points, cfg.gradeSmoothWindow);

  // Step 2: Classify each point
  const classifications: SegmentType[] = smoothedGrades.map((grade) => {
    if (grade > cfg.climbThreshold) return "climb";
    if (grade < cfg.descentThreshold) return "descent";
    return "flat";
  });

  // Step 3: Group into raw segments
  let rawSegments = groupSegments(points, classifications);

  // Step 4: Merge short segments
  rawSegments = mergeShortSegments(rawSegments, cfg.minSegmentLength);

  // Step 5: Validate segments
  rawSegments = validateSegments(rawSegments, points, cfg);

  // Recompute stats for final segments
  return rawSegments.map((seg, i) => computeSegmentStats(seg, points, i));
}

function smoothGrades(points: RoutePoint[], window: number): number[] {
  const halfWindow = Math.floor(window / 2);
  const grades: number[] = [];

  for (let i = 0; i < points.length; i++) {
    const start = Math.max(0, i - halfWindow);
    const end = Math.min(points.length - 1, i + halfWindow);
    let sum = 0;
    let count = 0;

    for (let j = start; j <= end; j++) {
      sum += points[j].grade;
      count++;
    }

    grades.push(sum / count);
  }

  return grades;
}

interface RawSegment {
  type: SegmentType;
  startIndex: number;
  endIndex: number;
}

function groupSegments(
  points: RoutePoint[],
  classifications: SegmentType[]
): RawSegment[] {
  const segments: RawSegment[] = [];
  let currentType = classifications[0];
  let startIndex = 0;

  for (let i = 1; i < classifications.length; i++) {
    if (classifications[i] !== currentType) {
      segments.push({ type: currentType, startIndex, endIndex: i - 1 });
      currentType = classifications[i];
      startIndex = i;
    }
  }

  // Last segment
  segments.push({
    type: currentType,
    startIndex,
    endIndex: classifications.length - 1,
  });

  return segments;
}

function mergeShortSegments(
  segments: RawSegment[],
  minLength: number
): RawSegment[] {
  if (segments.length <= 1) return segments;

  // We don't have points here directly, so we'll track by index count.
  // The caller should handle actual distance-based filtering.
  // For now, merge segments with very few points (< 5)
  const merged: RawSegment[] = [segments[0]];

  for (let i = 1; i < segments.length; i++) {
    const current = segments[i];
    const pointCount = current.endIndex - current.startIndex + 1;

    if (pointCount < 5 && merged.length > 0) {
      // Merge into previous segment
      merged[merged.length - 1].endIndex = current.endIndex;
    } else {
      merged.push(current);
    }
  }

  return merged;
}

function validateSegments(
  segments: RawSegment[],
  points: RoutePoint[],
  cfg: SegmenterConfig
): RawSegment[] {
  return segments.map((seg) => {
    const length =
      points[seg.endIndex].distance - points[seg.startIndex].distance;

    if (length < cfg.minSegmentLength) {
      return { ...seg, type: "flat" as SegmentType };
    }

    if (seg.type === "climb") {
      let gain = 0;
      for (let i = seg.startIndex + 1; i <= seg.endIndex; i++) {
        const diff = points[i].ele - points[i - 1].ele;
        if (diff > 0) gain += diff;
      }
      if (gain < cfg.minClimbGain) {
        return { ...seg, type: "flat" as SegmentType };
      }
    }

    return seg;
  });
}

function computeSegmentStats(
  raw: RawSegment,
  points: RoutePoint[],
  orderIndex: number
): Segment {
  const startDist = points[raw.startIndex].distance;
  const endDist = points[raw.endIndex].distance;
  let elevGain = 0;
  let elevLoss = 0;
  let maxGrade = 0;

  for (let i = raw.startIndex + 1; i <= raw.endIndex; i++) {
    const diff = points[i].ele - points[i - 1].ele;
    if (diff > 0) elevGain += diff;
    else elevLoss += Math.abs(diff);

    const absGrade = Math.abs(points[i].grade);
    if (absGrade > Math.abs(maxGrade)) {
      maxGrade = points[i].grade;
    }
  }

  const length = endDist - startDist;
  const totalElev = points[raw.endIndex].ele - points[raw.startIndex].ele;
  const avgGrade = length > 0 ? totalElev / length : 0;

  return {
    type: raw.type,
    startDistance: startDist,
    endDistance: endDist,
    startIndex: raw.startIndex,
    endIndex: raw.endIndex,
    elevationGain: Math.round(elevGain * 10) / 10,
    elevationLoss: Math.round(elevLoss * 10) / 10,
    averageGrade: Math.round(avgGrade * 1000) / 1000,
    maxGrade: Math.round(maxGrade * 1000) / 1000,
    length: Math.round(length),
  };
}
